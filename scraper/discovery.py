"""
Search-result discovery with full audit trail (Layer 1 counts + Layer 2 unique URLs).

Paginates until empty SERP page, CAPTCHA, or max_search_pages — not until unique cap.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from config import ScraperConfig, build_search_url, effective_product_cap
from scraper import selectors as sel
from services.discovery_audit import DiscoveryAuditSession, DiscoveryResult
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.rate_limit import pause_between_requests
from utils.retry import retry_with_backoff

logger = get_logger()

_DP_PATH = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)
_ASIN_STRICT = re.compile(r"^[A-Z0-9]{10}$")
_DP_IN_HTML = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?\"]|>|\\s|$)", re.I)
_RESULT_COUNT_RE = re.compile(
    r"([\d][\d,]*)\s+results?",
    re.I,
)


def _normalize_product_url(marketplace_root: str, href: str | None) -> str | None:
    if not href or href.startswith("#"):
        return None
    if href.strip().lower().startswith("javascript"):
        return None

    root_base = marketplace_root.rstrip("/")
    if href.startswith("http"):
        full = href
    else:
        full = urljoin(root_base + "/", href.lstrip("/"))

    parsed = urlparse(full)
    if "amazon." not in (parsed.netloc or "").lower():
        return None
    m = _DP_PATH.search(parsed.path or "")
    if not m:
        return None
    asin = m.group(1)
    return f"{root_base}/dp/{asin}"


def _url_from_asin(marketplace_root: str, asin: str) -> str | None:
    asin = (asin or "").strip().upper()
    if not _ASIN_STRICT.match(asin):
        return None
    return f"{marketplace_root.rstrip('/')}/dp/{asin}"


def parse_amazon_reported_result_count(html: str) -> int | None:
    """Best-effort parse of '1-16 of 60,000 results' style headline."""
    soup = BeautifulSoup(html, "lxml")
    chunks: list[str] = []
    for sel_ in (
        ".s-breadcrumb-header-text",
        "[data-component-type='s-result-info-bar']",
        ".a-section.a-spacing-small.a-spacing-top-small",
        "span[data-component-type='s-result-info-bar']",
    ):
        for el in soup.select(sel_):
            t = el.get_text(" ", strip=True)
            if t:
                chunks.append(t)
    if not chunks:
        chunks.append(soup.get_text(" ", strip=True)[:8000])
    for text in chunks:
        m = _RESULT_COUNT_RE.search(text)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def extract_all_listing_urls(soup: BeautifulSoup, html: str, base: str, page_url: str) -> list[str]:
    """Collect every valid PDP URL occurrence on a SERP (duplicates within page allowed)."""
    found: list[str] = []

    def add(href: str | None) -> None:
        u = _normalize_product_url(base, href)
        if u:
            found.append(u)

    for card in soup.select(sel.SEARCH_RESULT_ITEM):
        for a in card.select(sel.SEARCH_TITLE_LINK):
            add(a.get("href"))

    for a in soup.select('a[href*="/dp/"]'):
        add(a.get("href"))

    for node in soup.select("[data-asin]"):
        asin = (node.get("data-asin") or "").strip()
        u = _url_from_asin(base, asin)
        if u:
            found.append(u)

    for m in _DP_IN_HTML.finditer(html):
        u = _url_from_asin(base, m.group(1))
        if u:
            found.append(u)

    return found


@retry_with_backoff(max_attempts=3, base_delay_sec=1.0)
def _goto_search_page(page: Page, url: str, timeout_ms: int) -> None:
    page.goto(url, wait_until="load", timeout=timeout_ms)
    page.wait_for_timeout(1500)
    if "amazon." in url.lower():
        try:
            page.wait_for_selector("[data-asin]", timeout=8000)
        except Exception:
            logger.debug("Amazon SERP: no [data-asin] within timeout — continuing parse anyway.")


def discover_with_audit(
    page: Page,
    cfg: ScraperConfig,
    *,
    site_label: str = "amazon.in",
    full_pagination: bool = True,
) -> DiscoveryResult:
    """
    Audited Amazon SERP crawl.

    Layer 1: count every listing record and page visit.
    Layer 2: maintain unique product keys for extraction queue.

    When ``full_pagination`` is True (default), pagination is NOT stopped by URL list cap.
    """
    base = cfg.base_url.rstrip("/")
    audit = DiscoveryAuditSession(
        site=site_label,
        search_query=cfg.search_query,
        marketplace_root=base,
        max_search_pages_limit=cfg.max_search_pages,
    )

    page_num = 0
    stop_reason = ""
    reported_count_set = False

    while page_num < cfg.max_search_pages:
        page_num += 1
        if page_num > 1:
            pause_between_requests(cfg)

        search_url = build_search_url(page_num, cfg)
        logger.info("Discovery: loading search page %s — %s", page_num, search_url)

        try:
            _goto_search_page(page, search_url, cfg.navigation_timeout_ms)
        except Exception as e:
            logger.error("Discovery failed on page %s: %s", page_num, e)
            audit.failed_pages.append(page_num)
            audit.finish_page(
                page_num,
                search_url,
                records_found=0,
                unique_added=0,
                duplicates_on_page=0,
                status="failed",
            )
            continue

        audit.mark_listing_page_visited(page_num, search_url)

        try:
            html = get_stable_html(page, cfg.navigation_timeout_ms)
        except Exception as e:
            logger.error("Could not read search page HTML (page %s): %s", page_num, e)
            audit.failed_pages.append(page_num)
            audit.finish_page(
                page_num,
                search_url,
                records_found=0,
                unique_added=0,
                duplicates_on_page=0,
                status="failed",
            )
            continue

        if page_contains_captcha(html):
            stop_reason = "captcha_or_bot_page"
            audit.captcha_pages.append(page_num)
            audit.failed_pages.append(page_num)
            audit.finish_page(
                page_num,
                search_url,
                records_found=0,
                unique_added=0,
                duplicates_on_page=0,
                status="captcha",
            )
            logger.error("CAPTCHA on search page %s — stopping discovery.", page_num)
            break

        if not reported_count_set:
            rc = parse_amazon_reported_result_count(html)
            if rc is not None:
                audit.set_site_reported_count(rc)
                reported_count_set = True
                logger.info("Amazon site-reported result count: %s", rc)

        soup = BeautifulSoup(html, "lxml")
        page_urls = extract_all_listing_urls(soup, html, base, page.url)
        records_on_page = len(page_urls)

        if records_on_page == 0:
            stop_reason = "no_listing_records_on_serp_page"
            audit.finish_page(
                page_num,
                search_url,
                records_found=0,
                unique_added=0,
                duplicates_on_page=0,
                status="empty",
            )
            logger.warning(
                "Discovery stopped: %s — page %s had zero listing records.",
                stop_reason,
                page_num,
            )
            break

        unique_added, dup_on_page = audit.ingest_listing_urls(page_urls)
        audit.finish_page(
            page_num,
            search_url,
            records_found=records_on_page,
            unique_added=unique_added,
            duplicates_on_page=dup_on_page,
            status="ok",
        )

        progress_pct = ""
        if audit.site_reported_result_count and audit.site_reported_result_count > 0:
            progress_pct = f" ({100.0 * audit.scanned_records_counter / audit.site_reported_result_count:.1f}% of site-reported count)"

        logger.info(
            "[Page %s] Records found: %s | Unique added: %s | Duplicates: %s | "
            "Total scanned: %s | Unique total: %s | Dup total: %s%s",
            page_num,
            records_on_page,
            unique_added,
            dup_on_page,
            audit.scanned_records_counter,
            audit.unique_products_counter,
            audit.duplicate_products_counter,
            progress_pct,
        )

        if not full_pagination:
            cap = effective_product_cap(cfg)
            if audit.scanned_records_counter >= cap:
                stop_reason = "url_cap_reached"
                logger.info("Discovery URL list cap reached (%s).", cap)
                break

    if not stop_reason:
        if page_num >= cfg.max_search_pages:
            stop_reason = "max_search_pages_reached"
        else:
            stop_reason = "pagination_exhausted"

    audit.complete(stop_reason)

    extraction_urls = audit.get_extraction_urls()
    cap = effective_product_cap(cfg)
    if len(extraction_urls) > cap:
        extraction_urls = extraction_urls[:cap]
        logger.info(
            "Extraction queue trimmed to cap %s (audit scanned %s records, %s unique).",
            cap,
            audit.scanned_records_counter,
            audit.unique_products_counter,
        )

    logger.info(
        "Amazon discovery audit finished — status=%s | pages=%s | scanned=%s | unique=%s | "
        "duplicates=%s (%s) | site_reported=%s | stop=%s",
        audit.crawl_status,
        len(audit.visited_listing_pages),
        audit.scanned_records_counter,
        audit.unique_products_counter,
        audit.duplicate_products_counter,
        audit.duplicate_ratio,
        audit.site_reported_result_count,
        stop_reason,
    )

    return DiscoveryResult(audit=audit, extraction_urls=extraction_urls)


def discover_product_urls(
    page: Page,
    cfg: ScraperConfig,
    *,
    out_stats: dict | None = None,
    site_label: str = "amazon.in",
) -> list[str]:
    """Backward-compatible entry: returns Layer-2 unique URLs for extraction."""
    result = discover_with_audit(page, cfg, site_label=site_label, full_pagination=True)
    if out_stats is not None:
        out_stats.clear()
        out_stats.update(result.audit.to_summary_dict())
        out_stats["extraction_urls_queued"] = len(result.extraction_urls)
        out_stats["dedupe_urls"] = True
    return result.extraction_urls
