"""
Snapdeal: search discovery + PDP parse + validation + seller heuristic + images.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from config import ScraperConfig, effective_product_cap
from scraper import storage
from scraper.base_scraper import BaseScraper
from services.image_service import download_product_images, extract_snapdeal_image_urls
from services.product_service import persist_product_artifacts
from services.validation_service import is_valid_product
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.rate_limit import pause_between_requests
from utils.retry import retry_with_backoff

logger = get_logger()


class SnapdealScraper(BaseScraper):
    def __init__(self) -> None:
        self._root_url: str = "https://www.snapdeal.com"

    def prepare(self, user_site_url: str) -> None:
        super().prepare(user_site_url)
        p = urlparse(user_site_url.strip() or "https://www.snapdeal.com")
        if not p.netloc and user_site_url:
            p = urlparse("https://" + user_site_url.strip())
        if p.netloc:
            host = p.netloc.lower()
            # Prefer desktop host for predictable markup
            if host.startswith("m."):
                host = host[2:]
                if not host.startswith("www."):
                    host = "www." + host
            scheme = p.scheme or "https"
            self._root_url = f"{scheme}://{host.rstrip('/')}"

    def _search_url(self, query: str, page_num: int) -> str:
        q = quote_plus(query)
        u = f"{self._root_url}/search?keyword={q}"
        if page_num > 1:
            u += f"&page={page_num}"
        return u

    def _normalize_pdp(self, href: str | None) -> str | None:
        if not href or href.startswith("#"):
            return None
        if href.strip().lower().startswith("javascript"):
            return None
        full = urljoin(self._root_url + "/", href)
        pu = urlparse(full)
        if "snapdeal.com" not in pu.netloc.lower():
            return None
        path = pu.path or ""
        if "/product/" not in path.lower():
            return None
        # Drop trailing slash noise
        clean_path = path.split("?")[0].rstrip("/")
        if len(clean_path) < len("/product/x"):
            return None
        return f"{pu.scheme}://{pu.netloc}{clean_path}"

    @retry_with_backoff(max_attempts=3, base_delay_sec=1.0)
    def _goto(self, page: Page, url: str, timeout_ms: int) -> None:
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_timeout(900)

    def discover_urls(self, page: Page, query: str, cfg: ScraperConfig) -> list[str]:
        cap = effective_product_cap(cfg)
        collected: list[str] = []
        seen: set[str] = set()
        page_num = 0

        while len(collected) < cap and page_num < cfg.max_search_pages:
            page_num += 1
            if page_num > 1:
                pause_between_requests(cfg)
            url = self._search_url(query, page_num)
            logger.info("Snapdeal discovery page %s — %s", page_num, url)
            try:
                self._goto(page, url, cfg.navigation_timeout_ms)
            except Exception as e:
                logger.error("Snapdeal search navigation failed: %s", e)
                continue
            try:
                html = get_stable_html(page, cfg.navigation_timeout_ms)
            except Exception as e:
                logger.error("Snapdeal search HTML read failed: %s", e)
                continue
            if page_contains_captcha(html):
                logger.error("CAPTCHA on Snapdeal search — stopping discovery.")
                break

            before = len(collected)
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select('a[href*="/product/"]'):
                norm = self._normalize_pdp(a.get("href"))
                if norm and norm not in seen:
                    seen.add(norm)
                    collected.append(norm)
                    if len(collected) >= cap:
                        return collected[:cap]

            if len(collected) == before:
                logger.info("Snapdeal: no new URLs on page %s — stopping.", page_num)
                break

            logger.info("Snapdeal page %s — URLs so far: %s", page_num, len(collected))

        return collected[:cap]

    def _parse_pdp(self, html: str, product_url: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")

        title = ""
        for sel in ("h1.pdp-e-mod-head", "h1[itemprop=name]", "h1", ".pdp-e-mod-head"):
            el = soup.select_one(sel)
            if el:
                title = " ".join(el.get_text().split())
                if title:
                    break

        price = ""
        for sel in ("span.payBlkBig", "span[itemprop=price]", ".payBlkBig", ".product-price"):
            el = soup.select_one(sel)
            if el:
                price = " ".join(el.get_text().split())
                if price:
                    break

        brand = ""
        for row in soup.select("table.specs-table tr, .spec-row, table tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2 and "brand" in cells[0].get_text(" ", strip=True).lower():
                brand = cells[1].get_text(" ", strip=True)[:120]
                break

        desc = ""
        hi = soup.select_one(".detail-desc-acordion, #productSpecs, .tab-container")
        if hi:
            desc = " ".join(hi.get_text().split())[:1200]

        ean = ""
        blob = soup.get_text(" ", strip=True)
        m = re.search(r"\b(\d{13})\b", blob)
        if m:
            ean = m.group(1)

        sellers = self._sellers_from_pdp(soup, price)

        return {
            "product_name": title,
            "product_url": product_url,
            "price": price,
            "brand": brand,
            "ean": ean,
            "description": desc,
            "sellers": sellers,
        }

    def _sellers_from_pdp(self, soup: BeautifulSoup, fallback_price: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for block in soup.select(".seller-name, #seller, .subSeller"):
            txt = " ".join(block.get_text().split())
            if txt and len(txt) > 2:
                out.append({"seller": txt[:200], "price": fallback_price})
                break
        if not out:
            sb = soup.find(string=re.compile(r"snapdeal\s*bestsellers|sold\s*by", re.I))
            if sb and sb.parent:
                out.append({"seller": sb.parent.get_text(" ", strip=True)[:200], "price": fallback_price})
        if not out:
            out.append({"seller": "Snapdeal marketplace", "price": fallback_price})
        return out

    def scrape_product(
        self,
        page: Page,
        product_url: str,
        query: str,
        product_dir: Path,
        cfg: ScraperConfig,
    ) -> dict[str, Any]:
        out_path = str(product_dir.resolve())
        row: dict[str, Any] = {
            "country": "",
            "site": self.site_label,
            "product_name": "",
            "product_url": product_url,
            "brand": "",
            "reference": "",
            "ean": "",
            "price": "",
            "image_count": 0,
            "extraction_date": "",
            "status": "ERROR",
            "output_path": out_path,
            "query": query,
            "seller_count": 0,
            "output_folder_path": out_path,
        }

        try:
            self._goto(page, product_url, cfg.navigation_timeout_ms)
            html = get_stable_html(page, cfg.navigation_timeout_ms)
            if page_contains_captcha(html):
                if html:
                    storage.save_page_html(product_dir, html)
                row["status"] = "BLOCKED"
                return row
            if not html:
                return row

            parsed = self._parse_pdp(html, product_url)
            row["product_name"] = parsed["product_name"]
            row["brand"] = parsed["brand"]
            row["ean"] = parsed["ean"]
            row["price"] = parsed["price"]

            if cfg.enforce_product_validation and not is_valid_product(
                parsed["ean"],
                parsed["brand"],
                parsed["product_name"],
                query,
            ):
                logger.info("Filtered by validation (Snapdeal): %s", product_url[:80])
                record = storage.MultiSiteProductRecord(
                    site=self.site_label,
                    query=query,
                    product_name=parsed["product_name"],
                    product_url=product_url,
                    brand=parsed["brand"],
                    ean=parsed["ean"],
                    price=parsed["price"],
                    description=parsed["description"],
                    sellers=[],
                    images=[],
                    status="FILTERED",
                )
                persist_product_artifacts(product_dir, html, record)
                row["status"] = "FILTERED"
                return row

            pause_between_requests(cfg)
            img_urls = extract_snapdeal_image_urls(html)
            if cfg.save_product_images and img_urls:
                images_map = download_product_images(img_urls, product_dir / "images", cfg)
            else:
                images_map = []

            sellers_list = parsed["sellers"]

            record = storage.MultiSiteProductRecord(
                site=self.site_label,
                query=query,
                product_name=parsed["product_name"],
                product_url=product_url,
                brand=parsed["brand"],
                ean=parsed["ean"],
                price=parsed["price"],
                description=parsed["description"],
                sellers=[{"seller": x["seller"], "price": x["price"]} for x in sellers_list],
                images=[{"url": x["url"], "local_path": x["local_path"]} for x in images_map],
                status="OK",
            )
            persist_product_artifacts(product_dir, html, record)

            row["seller_count"] = len(sellers_list)
            row["image_count"] = len(images_map)
            row["status"] = "OK"
            row["extraction_date"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            return row
        except Exception as e:
            logger.exception("Snapdeal scrape error %s: %s", product_url, e)
            row["status"] = "ERROR"
            return row
