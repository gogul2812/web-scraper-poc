"""
SEB benchmark discovery — relaxed category revisits, PDP occurrence counting, CAPTCHA retry-once.
"""

from __future__ import annotations

import random
import time
from playwright.sync_api import Page

from benchmark.config import BenchmarkLimits
from benchmark.metrics import BenchmarkMetrics
from config import ScraperConfig, effective_product_cap
from scraper.seb_parse import (
    extract_category_urls,
    extract_discovery_listing_urls,
    extract_pdp_url_occurrences,
    extract_pdp_urls_from_page,
)
from scraper.seb_scraper import SebScraper, _category_identity
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.rate_limit import pause_between_requests

logger = get_logger()


class BenchmarkSebScraper(SebScraper):
    """SEB scraper with instrumented navigation (metrics + backoff), no decorator."""

    def __init__(self, metrics: BenchmarkMetrics) -> None:
        super().__init__()
        self._bm = metrics

    def _goto(self, page: Page, url: str, timeout_ms: int) -> None:  # type: ignore[override]
        max_attempts = 3
        attempt = 0
        last_exc: BaseException | None = None
        while attempt < max_attempts:
            try:
                t0 = time.perf_counter()
                page.goto(url, wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(650)
                self._bm.record_page_load(time.perf_counter() - t0)
                return
            except BaseException as e:
                last_exc = e
                attempt += 1
                self._bm.retry_count += 1
                el = str(e).lower()
                if "timeout" in el:
                    self._bm.timeout_events += 1
                if attempt >= max_attempts:
                    break
                delay = min(1.2 * (2 ** (attempt - 1)), 30.0)
                time.sleep(delay + random.uniform(0, delay * 0.2))
        if last_exc:
            raise last_exc


def _load_page_with_captcha_retry(
    scraper: BenchmarkSebScraper,
    page: Page,
    url: str,
    cfg: ScraperConfig,
    metrics: BenchmarkMetrics,
    *,
    scroll_listing: bool,
) -> str | None:
    """CAPTCHA: log, count, retry once with polite pause; then count blocked."""
    for attempt in range(2):
        try:
            scraper._goto(page, url, cfg.navigation_timeout_ms)
            if scroll_listing:
                scraper._scroll_listing_page(page, cfg)
            html = get_stable_html(page, cfg.navigation_timeout_ms)
        except BaseException as e:
            metrics.retry_count += 1
            if "timeout" in str(e).lower():
                metrics.timeout_events += 1
            logger.warning("Navigation failed %s: %s", url[:90], e)
            if attempt == 0:
                pause_between_requests(cfg)
                continue
            return None

        if page_contains_captcha(html):
            metrics.captcha_hits += 1
            logger.warning("CAPTCHA-like response — retry once after delay (%s)", url[:80])
            if attempt == 0:
                pause_between_requests(cfg)
                continue
            metrics.blocked_pages += 1
            return None
        return html
    return None


def discover_urls_benchmark(
    scraper: BenchmarkSebScraper,
    page: Page,
    cfg: ScraperConfig,
    metrics: BenchmarkMetrics,
    limits: BenchmarkLimits,
) -> tuple[list[str], set[str]]:
    """
    Breadth-first listing crawl with benchmark metrics.

    Returns (ordered unique PDP URLs for extraction, set of unique PDP URLs for duplication stats).
    """
    collected: list[str] = []
    seen_product: set[str] = set()
    cat_enqueue_count: dict[str, int] = {}

    root = scraper._root_url.rstrip("/")
    max_collect = effective_product_cap(cfg)
    max_pages_per_cat = cfg.seb_max_pages_per_category
    page_budget = cfg.seb_max_category_visits

    queue: list[str] = []
    listing_pages_visited = 0
    wall_t0 = time.perf_counter()

    def wall_exceeded() -> bool:
        if limits.max_wall_seconds is None:
            return False
        return (time.perf_counter() - wall_t0) > limits.max_wall_seconds

    def consume_metrics_and_collect(page_url: str, html: str) -> None:
        nonlocal collected
        occ = extract_pdp_url_occurrences(root, page_url, html)
        metrics.total_pdp_discovery_events += len(occ)
        for u in extract_pdp_urls_from_page(root, page_url, html):
            if u not in seen_product:
                seen_product.add(u)
                collected.append(u)
                if len(collected) >= max_collect:
                    return

    def try_enqueue_category(raw: str) -> None:
        raw = raw.strip().split("#")[0]
        if not raw:
            return
        cid = _category_identity(raw)
        cnt = cat_enqueue_count.get(cid, 0)
        if cnt >= limits.category_revisit_limit:
            return
        metrics.category_enqueue_events += 1
        if cnt > 0:
            metrics.duplicate_category_enqueues += 1
        cat_enqueue_count[cid] = cnt + 1
        if len(queue) >= limits.max_discovery_queue:
            logger.warning("Benchmark discovery queue cap %s", limits.max_discovery_queue)
            return
        queue.append(raw)

    # Sitemap PDP seeds (count each loc as one discovery event)
    seed_urls = scraper._discover_from_sitemaps(root)
    for u in seed_urls:
        metrics.total_pdp_discovery_events += 1
        if u not in seen_product:
            seen_product.add(u)
            collected.append(u)
            if len(collected) >= max_collect:
                return collected[:max_collect], seen_product

    # Homepage
    try:
        html_home = _load_page_with_captcha_retry(
            scraper,
            page,
            root + "/",
            cfg,
            metrics,
            scroll_listing=False,
        )
    except BaseException as e:
        logger.error("Benchmark homepage failed: %s", e)
        return collected[:max_collect], seen_product

    if not html_home:
        return collected[:max_collect], seen_product

    consume_metrics_and_collect(page.url, html_home)
    categories = extract_category_urls(root, html_home)
    if not cfg.seb_discovery_strict:
        categories |= extract_discovery_listing_urls(root, page.url, html_home, strict=False)
    if cfg.seb_bootstrap_category_sitemap:
        categories.update(scraper._category_hub_urls_from_sitemaps(root))
    for c in sorted(categories):
        try_enqueue_category(c)

    visited = 0
    while queue and len(collected) < max_collect and visited < page_budget:
        if limits.max_listing_pages_total is not None and listing_pages_visited >= limits.max_listing_pages_total:
            logger.info("Benchmark discovery stopped: listing page budget (%s).", limits.max_listing_pages_total)
            break
        if wall_exceeded():
            logger.info("Benchmark discovery stopped: wall clock limit.")
            break
        if collected:
            pause_between_requests(cfg)

        cat_entry = queue.pop(0)
        base_cat = cat_entry.split("?")[0]
        visited += 1

        empty_streak = 0
        for pn in range(1, max_pages_per_cat + 1):
            if len(collected) >= max_collect or wall_exceeded():
                break
            if limits.max_listing_pages_total is not None:
                if listing_pages_visited >= limits.max_listing_pages_total:
                    break
            list_url = base_cat if pn == 1 else f"{base_cat}?page={pn}"
            listing_pages_visited += 1

            ch = _load_page_with_captcha_retry(
                scraper,
                page,
                list_url,
                cfg,
                metrics,
                scroll_listing=True,
            )
            if not ch:
                break

            before = len(collected)
            consume_metrics_and_collect(page.url, ch)
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

            if visited % 40 == 0:
                logger.info(
                    "Benchmark discovery: visited=%s listing_pages=%s queue=%s products=%s dup_cat=%s",
                    visited,
                    listing_pages_visited,
                    len(queue),
                    len(collected),
                    metrics.duplicate_category_enqueues,
                )

    logger.info(
        "Benchmark discovery done: unique PDPs=%s discovery_events=%s category_pages=%s",
        len(collected),
        metrics.total_pdp_discovery_events,
        visited,
    )
    return collected[:max_collect], seen_product


def patch_image_download_timing(metrics: BenchmarkMetrics):
    """Returns (restore_fn) context helper."""
    import services.seb_image_service as sis

    orig = sis.download_seb_gallery

    def wrapped(entries, images_dir, cfg):
        t0 = time.perf_counter()
        mapping, failed = orig(entries, images_dir, cfg)
        metrics.image_download_seconds_total += time.perf_counter() - t0
        metrics.images_downloaded += len(mapping)
        return mapping, failed

    sis.download_seb_gallery = wrapped  # type: ignore[assignment]

    def restore() -> None:
        sis.download_seb_gallery = orig  # type: ignore[assignment]

    return restore
