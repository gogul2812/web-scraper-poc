"""
Central configuration for multi-site enterprise ingestion POC.

Adjust domains, limits, timing, and enterprise export behaviour here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScraperConfig:
    """Immutable runtime configuration."""

    base_url: str = "https://www.amazon.in"
    search_query: str = "Samsung"

    # Target PDP count per site. None or <=0 = "full catalog" mode (uses catalog_discovery_hard_cap).
    max_products: int | None = None
    # Safety ceiling when max_products is unlimited (runaway guard).
    # Raise (e.g. 70_000) only if you intentionally harvest very large keyword/category runs.
    catalog_discovery_hard_cap: int = 70_000

    # Discovery: max listing/category pages per site (Amazon SERP pagination depth).
    # Amazon often stops returning *new* ASINs long before this; raising helps only if pages stay fresh.
    max_search_pages: int = 2_000

    min_search_pages: int = 2

    delay_min_sec: float = 1.0
    delay_max_sec: float = 3.5

    navigation_timeout_ms: int = 75_000
    max_retries: int = 3
    image_download_timeout_sec: float = 60.0

    output_dir: str = "output"
    logs_dir: str = "logs"
    log_file: str = "logs/scraper.log"

    validation_mode: str = "fallback"
    ean_dataset_path: str | None = None
    enforce_product_validation: bool = False

    # When True, create product_*/images/ and download gallery bytes. Default off for faster runs / less disk.
    save_product_images: bool = False

    # Discovery audit: paginate all SERP pages for coverage metrics (not stop on URL cap during listing scan).
    discovery_audit_full_pagination: bool = True

    # --- Enterprise export ---
    product_id_padding: int = 6
    save_clean_html: bool = True
    save_screenshot_on_failure: bool = True
    excel_subdir: str = "excel"

    # SEB-specific discovery (breadth-first category crawl + pagination)
    seb_max_category_visits: int = 8000
    seb_max_pages_per_category: int = 40
    # Practical discovery: strict `/c/` hubs only (no shallow-path keyword heuristic)
    seb_discovery_strict: bool = True
    # Stop enqueue growth — keeps RAM bounded (raise for very large full-catalog crawls)
    seb_max_discovery_queue: int = 2500
    # Listing pages often lazy-load grids; brief scroll helps before HTML capture
    seb_listing_scroll_passes: int = 4
    seb_listing_scroll_wait_ms: int = 450
    # Pull category hub URLs from the Category sitemap (alongside Product PDP sitemap)
    seb_bootstrap_category_sitemap: bool = True


def build_search_url(page_number: int = 1, cfg: ScraperConfig | None = None) -> str:
    """Build Amazon search URL for the configured query."""
    from urllib.parse import quote_plus

    c = cfg or ScraperConfig()
    q = quote_plus(c.search_query)
    if page_number <= 1:
        return f"{c.base_url}/s?k={q}"
    return f"{c.base_url}/s?k={q}&page={page_number}"


def get_config() -> ScraperConfig:
    return ScraperConfig()


def effective_product_cap(cfg: ScraperConfig) -> int:
    """
    Integer cap used in discovery loops.

    ``max_products`` None or <= 0 means full-catalog intent and maps to ``catalog_discovery_hard_cap``.
    Positive ``max_products`` is clamped to the hard cap.
    """
    m = cfg.max_products
    hard = max(1, int(cfg.catalog_discovery_hard_cap))
    if m is None or m <= 0:
        return hard
    return min(int(m), hard)
