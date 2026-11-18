"""
Groupe SEB France — enterprise-grade crawl + PDP extraction.

Discovery: breadth-first category hubs with per-hub pagination (?page=N) until URL cap.
Outputs: raw.html, clean.html, data.json, role-based images (main_01.jpg, …).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests

from playwright.sync_api import Page

from config import ScraperConfig, effective_product_cap
from scraper.base_scraper import BaseScraper
from scraper.seb_parse import (
    assign_image_roles,
    extract_category_urls,
    extract_discovery_listing_urls,
    extract_pdp_urls_from_page,
    filter_product_medias,
    parse_seb_product,
)
from services.extraction_service import save_product_html_bundle
from services.seb_image_service import download_seb_gallery
from utils.html_checks import page_contains_captcha
from utils.language_utils import site_domain_for_reports
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.rate_limit import pause_between_requests
from utils.retry import retry_with_backoff

logger = get_logger()

SITE_DOMAIN_LABEL = "seb.fr/fr"


def _category_identity(url: str) -> str:
    """Normalize category URLs so duplicates differ only by query or trailing slash."""
    return url.split("#")[0].split("?")[0].rstrip("/")


class SebScraper(BaseScraper):
    """Official seb.fr storefront (Hybris)."""

    def __init__(self) -> None:
        self._root_url = "https://www.seb.fr"
        self.discovery_state_path: str | None = None
        self.discovery_stats: dict[str, Any] = {}

    def prepare(self, user_site_url: str) -> None:
        super().prepare(user_site_url)
        p = urlparse(user_site_url.strip() or "https://www.seb.fr")
        if not p.netloc and user_site_url:
            p = urlparse("https://" + user_site_url.strip())
        if p.netloc:
            self._root_url = f"{p.scheme or 'https'}://{p.netloc.rstrip('/')}"

    @retry_with_backoff(max_attempts=3, base_delay_sec=1.2)
    def _goto(self, page: Page, url: str, timeout_ms: int) -> None:
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_timeout(650)

    def _scroll_listing_page(self, page: Page, cfg: ScraperConfig) -> None:
        """Scroll listing grids so lazy-loaded product tiles appear in the DOM."""
        n = max(0, int(cfg.seb_listing_scroll_passes))
        w = max(0, int(cfg.seb_listing_scroll_wait_ms))
        for _ in range(n):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(w)

    def discover_urls(self, page: Page, query: str, cfg: ScraperConfig) -> list[str]:
        """
        Discovery tuned for production-scale behaviour:

        1) PDP seeds from Product sitemap XML
        2) Optional Category sitemap hub URLs (bounded queue)
        3) Homepage `/c/` extraction (strict mode avoids shallow-path queue explosion)
        4) BFS with per-hub pagination; listing pages are scrolled before HTML capture
        5) Persisted state for resume/debug
        """
        del query
        collected: list[str] = []
        seen: set[str] = set()
        seen_cat: set[str] = set()
        duplicates_skipped = 0
        queue_cap_warned = False
        category_sitemap_seed_count = 0

        root = self._root_url.rstrip("/")
        max_collect = effective_product_cap(cfg)
        max_queue = max(32, int(cfg.seb_max_discovery_queue))
        page_budget = min(
            cfg.seb_max_category_visits,
            max(cfg.max_search_pages, max_collect * 25),
        )
        max_pages_per_cat = cfg.seb_max_pages_per_category
        state_path = Path(self.discovery_state_path) if self.discovery_state_path else None
        queue: list[str] = []

        def consume(page_url: str, html: str) -> int:
            nonlocal duplicates_skipped
            n = 0
            for u in extract_pdp_urls_from_page(root, page_url, html):
                if u not in seen:
                    seen.add(u)
                    collected.append(u)
                    n += 1
                    if len(collected) >= max_collect:
                        return n
                else:
                    duplicates_skipped += 1
            return n

        def persist_state(queue_urls: list[str], visited_count: int) -> None:
            if not state_path:
                return
            payload = {
                "site": self.site_label,
                "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "visited_category_pages": visited_count,
                "queued_category_pages": len(queue_urls),
                "discovered_product_urls": len(collected),
                "duplicates_skipped": duplicates_skipped,
                "queue": queue_urls,
                "seen_categories": sorted(seen_cat),
                "discovered_products": collected,
            }
            try:
                state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as ex:
                logger.debug("Could not persist discovery state: %s", ex)

        def try_enqueue_category(url: str) -> None:
            nonlocal queue_cap_warned
            raw = url.strip().split("#")[0]
            if not raw:
                return
            cid = _category_identity(raw)
            if cid in seen_cat:
                return
            if len(queue) >= max_queue:
                if not queue_cap_warned:
                    logger.warning(
                        "SEB discovery queue cap (%s) — skipping new category hubs until queue drains.",
                        max_queue,
                    )
                    queue_cap_warned = True
                return
            seen_cat.add(cid)
            queue.append(raw)

        # Seed from sitemap XML (best-effort)
        seed_urls = self._discover_from_sitemaps(root)
        for u in seed_urls:
            if u not in seen:
                seen.add(u)
                collected.append(u)
                if len(collected) >= max_collect:
                    logger.info("SEB discovery reached cap from sitemap seed.")
                    self.discovery_stats = {
                        "from_sitemap": len(collected),
                        "category_sitemap_seeds": 0,
                        "visited_category_pages": 0,
                        "queued_category_pages_end": 0,
                        "categories_discovered": 0,
                        "duplicates_skipped": duplicates_skipped,
                        "discovery_strict": bool(cfg.seb_discovery_strict),
                        "queue_cap_reached": False,
                        "max_discovery_queue": max_queue,
                    }
                    persist_state([], 0)
                    return collected[:max_collect]

        resumed = False
        if state_path and state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                for u in state.get("discovered_products", []):
                    if isinstance(u, str) and u not in seen:
                        seen.add(u)
                        collected.append(u)
                        if len(collected) >= max_collect:
                            break
                for c in state.get("seen_categories", []):
                    if isinstance(c, str):
                        seen_cat.add(_category_identity(c))
                queue = [c.strip().split("#")[0] for c in state.get("queue", []) if isinstance(c, str)]
                if len(queue) > max_queue:
                    logger.info(
                        "Trimming resumed discovery queue from %s to cap %s.",
                        len(queue),
                        max_queue,
                    )
                    queue = queue[:max_queue]
                resumed = bool(queue)
                if resumed:
                    logger.info(
                        "Resuming discovery from state: queue=%s categories=%s products=%s",
                        len(queue),
                        len(seen_cat),
                        len(collected),
                    )
            except Exception as ex:
                logger.warning("Could not load discovery state; starting fresh: %s", ex)

        if not resumed:
            try:
                self._goto(page, root + "/", cfg.navigation_timeout_ms)
                html = get_stable_html(page, cfg.navigation_timeout_ms)
            except Exception as e:
                logger.error("SEB homepage navigation failed: %s", e)
                return []

            if page_contains_captcha(html):
                logger.error("CAPTCHA / bot page on SEB homepage — abort discovery.")
                return []

            consume(page.url, html)

            categories = extract_category_urls(root, html)
            if not cfg.seb_discovery_strict:
                categories |= extract_discovery_listing_urls(root, page.url, html, strict=False)
            if cfg.seb_bootstrap_category_sitemap:
                hub_seeds = self._category_hub_urls_from_sitemaps(root)
                category_sitemap_seed_count = len(hub_seeds)
                categories.update(hub_seeds)

            for c in sorted(categories):
                try_enqueue_category(c)

        visited = 0
        while queue and len(collected) < max_collect and visited < page_budget:
            if collected:
                pause_between_requests(cfg)
            cat_entry = queue.pop(0)
            base_cat = cat_entry.split("?")[0]
            visited += 1

            empty_streak = 0
            for pn in range(1, max_pages_per_cat + 1):
                if len(collected) >= max_collect:
                    break
                list_url = base_cat if pn == 1 else f"{base_cat}?page={pn}"
                logger.debug(
                    "SEB discovery list_url page=%s/%s visited=%s queue=%s url=%s",
                    pn,
                    max_pages_per_cat,
                    visited,
                    len(queue),
                    list_url[:120],
                )
                try:
                    self._goto(page, list_url, cfg.navigation_timeout_ms)
                    self._scroll_listing_page(page, cfg)
                    ch = get_stable_html(page, cfg.navigation_timeout_ms)
                except Exception as e:
                    logger.warning("SEB listing load failed %s: %s", list_url[:80], e)
                    break
                if page_contains_captcha(ch):
                    logger.error("CAPTCHA on SEB listing — stopping discovery.")
                    queue.clear()
                    break

                before = len(collected)
                consume(page.url, ch)
                per_page_products = len(collected) - before
                logger.debug(
                    "SEB listing stats: products_added=%s total_products=%s url=%s",
                    per_page_products,
                    len(collected),
                    list_url[:100],
                )
                added = len(collected) - before
                if added == 0:
                    empty_streak += 1
                    if pn > 1 and empty_streak >= 2:
                        break
                else:
                    empty_streak = 0

                sub_links = extract_category_urls(root, ch)
                if not cfg.seb_discovery_strict:
                    sub_links |= extract_discovery_listing_urls(root, page.url, ch, strict=False)
                for sub in sub_links:
                    try_enqueue_category(sub)

            if visited % 25 == 0:
                logger.info(
                    "SEB discovery progress: visited=%s queue=%s categories=%s products=%s dup_skipped=%s",
                    visited,
                    len(queue),
                    len(seen_cat),
                    len(collected),
                    duplicates_skipped,
                )
                persist_state(queue, visited)

        persist_state(queue, visited)
        self.discovery_stats = {
            "from_sitemap": min(len(seed_urls), len(collected)),
            "category_sitemap_seeds": category_sitemap_seed_count,
            "visited_category_pages": visited,
            "queued_category_pages_end": len(queue),
            "categories_discovered": len(seen_cat),
            "duplicates_skipped": duplicates_skipped,
            "discovery_strict": bool(cfg.seb_discovery_strict),
            "queue_cap_reached": queue_cap_warned,
            "max_discovery_queue": max_queue,
        }

        logger.info(
            "SEB discovery finished: %s unique PDP URLs (cap %s), categories=%s, dup_skipped=%s, queue_end=%s.",
            len(collected),
            max_collect,
            len(seen_cat),
            duplicates_skipped,
            len(queue),
        )
        return collected[:max_collect]

    def _discover_from_sitemaps(self, root: str) -> list[str]:
        """Load PDP URLs from sitemap index/content as a discovery accelerator."""
        urls: list[str] = []
        seen: set[str] = set()
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        idx_url = f"{root}/sitemap.xml"
        try:
            t = requests.get(idx_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=35).text
            rt = ET.fromstring(t)
            subs = [x.text for x in rt.findall(".//sm:loc", ns) if x.text]
        except Exception as e:
            logger.debug("SEB sitemap index read failed: %s", e)
            return []
        for sub in subs:
            try:
                t2 = requests.get(sub, headers={"User-Agent": "Mozilla/5.0"}, timeout=35).text
                r2 = ET.fromstring(t2)
                locs = [x.text for x in r2.findall(".//sm:loc", ns) if x.text]
            except Exception:
                continue
            for loc in locs:
                u = loc.strip()
                if "/p/" in u and "seb.fr" in u and u not in seen:
                    seen.add(u)
                    urls.append(u.split("#")[0])
        if urls:
            logger.info("SEB sitemap seed URLs: %s", len(urls))
        return urls

    def _category_hub_urls_from_sitemaps(self, root: str) -> list[str]:
        """
        Parse Category* content sitemaps from the index — adds listing hubs without
        crawling every shallow internal link on the storefront.
        """
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        idx_url = f"{root.rstrip('/')}/sitemap.xml"
        try:
            t = requests.get(idx_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=35).text
            rt = ET.fromstring(t)
            subs = [x.text for x in rt.findall(".//sm:loc", ns) if x.text]
        except Exception as e:
            logger.debug("SEB sitemap index read failed (category bootstrap): %s", e)
            return []
        out: list[str] = []
        seen_ids: set[str] = set()
        for sub in subs:
            if sub is None:
                continue
            if "category" not in sub.lower():
                continue
            try:
                t2 = requests.get(sub, headers={"User-Agent": "Mozilla/5.0"}, timeout=35).text
                r2 = ET.fromstring(t2)
                locs = [x.text for x in r2.findall(".//sm:loc", ns) if x.text]
            except Exception:
                continue
            for loc in locs:
                u = (loc or "").strip().split("#")[0]
                if not u or "seb.fr" not in u.lower():
                    continue
                cid = _category_identity(u)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                out.append(u)
        if out:
            logger.info("SEB Category sitemap hub URLs: %s", len(out))
        return out

    def scrape_product(
        self,
        page: Page,
        product_url: str,
        query: str,
        product_dir: Path,
        cfg: ScraperConfig,
    ) -> dict[str, Any]:
        root = self._root_url.rstrip("/")
        out_path = str(product_dir.resolve())
        extraction_stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        t0 = time.perf_counter()

        row: dict[str, Any] = {
            "country": "FR",
            "language": "fr",
            "site": self.site_label,
            "site_domain": site_domain_for_reports(self.site_label),
            "product_name": "",
            "product_reference": "",
            "product_url": product_url,
            "brand": "",
            "reference": "",
            "ean": "",
            "price": "",
            "currency": "EUR",
            "availability": "",
            "short_description": "",
            "breadcrumbs": "",
            "category": "",
            "specification_count": 0,
            "image_count": 0,
            "main_image_path": "",
            "seller_count": 0,
            "extraction_timestamp": extraction_stamp,
            "extraction_date": extraction_stamp,
            "status": "ERROR",
            "output_folder_path": out_path,
            "output_path": out_path,
            "query": query,
            "html_path": "",
            "raw_html_path": "",
            "clean_html_path": "",
            "json_path": "",
            "scrape_duration_seconds": 0.0,
            "image_download_failures": 0,
        }

        try:
            self._goto(page, product_url, cfg.navigation_timeout_ms)
            html = get_stable_html(page, cfg.navigation_timeout_ms)
            if page_contains_captcha(html):
                if html:
                    raw_path, clean_path = save_product_html_bundle(product_dir, html, cfg)
                    row["raw_html_path"] = raw_path
                    row["clean_html_path"] = clean_path or ""
                row["status"] = "BLOCKED"
                row["html_path"] = row["raw_html_path"] or str((product_dir / "raw.html").resolve())
                if cfg.save_screenshot_on_failure:
                    self._screenshot_safe(page, product_dir / "blocked.png")
                row["scrape_duration_seconds"] = round(time.perf_counter() - t0, 3)
                logger.warning("CAPTCHA / bot signals on SEB PDP — %s", product_url[:80])
                return row
            if not html:
                row["scrape_duration_seconds"] = round(time.perf_counter() - t0, 3)
                return row

            raw_path, clean_html_path = save_product_html_bundle(product_dir, html, cfg)
            row["html_path"] = raw_path
            row["raw_html_path"] = raw_path
            row["clean_html_path"] = clean_html_path or ""

            parsed = parse_seb_product(html, product_url, root)
            media_urls = filter_product_medias(parsed.pop("media_urls_ordered", []))
            role_entries = assign_image_roles(media_urls)

            if cfg.save_product_images:
                images_record, img_failed = download_seb_gallery(
                    role_entries,
                    product_dir / "images",
                    cfg,
                )
            else:
                images_record = [
                    {
                        "url": e.get("url") or "",
                        "local_path": "",
                        "image_role": (e.get("role") or "detail").lower().replace(" ", "_"),
                        "image_index": i + 1,
                        "image_hash": "",
                    }
                    for i, e in enumerate(role_entries)
                    if (e.get("url") or "").startswith("http")
                ]
                img_failed = 0

            price_amount = parsed.get("price_amount") or ""
            price_display = ""
            if price_amount:
                price_display = f"{price_amount} {parsed.get('currency') or 'EUR'}"

            specs = parsed.get("specifications") or {}
            row.update(
                {
                    "product_name": parsed.get("product_name") or "",
                    "brand": parsed.get("brand") or "",
                    "reference": parsed.get("reference") or "",
                    "product_reference": parsed.get("reference") or "",
                    "ean": parsed.get("ean") or "",
                    "price": price_display,
                    "currency": parsed.get("currency") or "EUR",
                    "short_description": parsed.get("short_description") or "",
                    "breadcrumbs": parsed.get("breadcrumbs") or "",
                    "category": parsed.get("category") or "",
                    "availability": parsed.get("availability") or "",
                    "specification_count": len(specs),
                    "image_download_failures": img_failed,
                },
            )

            main_path = ""
            for im in images_record:
                if im.get("image_role") == "main":
                    main_path = str(im.get("local_path") or "")
                    break
            if not main_path and images_record:
                main_path = str(images_record[0].get("local_path") or "")
            row["main_image_path"] = main_path

            payload = {
                "country": "FR",
                "site": "seb.fr",
                "site_domain": SITE_DOMAIN_LABEL,
                "language": "fr",
                "product_url": product_url,
                "brand": parsed.get("brand") or "",
                "product_name": parsed.get("product_name") or "",
                "reference": parsed.get("reference") or "",
                "ean": parsed.get("ean") or "",
                "price": {
                    "amount": price_amount,
                    "currency": parsed.get("currency") or "EUR",
                },
                "short_description": parsed.get("short_description") or "",
                "long_description": parsed.get("long_description") or "",
                "specifications": specs,
                "breadcrumbs": parsed.get("breadcrumbs") or "",
                "category": parsed.get("category") or "",
                "availability": parsed.get("availability") or "",
                "sellers": [],
                "images": images_record,
                "extraction_date": extraction_stamp,
                "status": "OK",
            }

            json_path = product_dir / "data.json"
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            row["json_path"] = str(json_path.resolve())
            row["image_count"] = len(images_record)
            row["status"] = "OK"
            row["scrape_duration_seconds"] = round(time.perf_counter() - t0, 3)
            return row

        except Exception as e:
            logger.error("SEB scrape error %s: %s", product_url[:120], e)
            row["status"] = "ERROR"
            row["scrape_duration_seconds"] = round(time.perf_counter() - t0, 3)
            if cfg.save_screenshot_on_failure:
                self._screenshot_safe(page, product_dir / "failure.png")
            return row

    def _screenshot_safe(self, page: Page, dest: Path) -> None:
        try:
            page.screenshot(path=str(dest), full_page=False)
            logger.debug("Saved failure screenshot %s", dest)
        except Exception as ex:
            logger.debug("Screenshot skipped: %s", ex)
