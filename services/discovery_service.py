"""
Thin wrapper around scraper-specific discovery (keeps orchestration readable).
"""

from __future__ import annotations

from playwright.sync_api import Page

from config import ScraperConfig
from scraper.base_scraper import BaseScraper


def discover_products(
    scraper: BaseScraper,
    page: Page,
    query: str,
    cfg: ScraperConfig,
) -> list[str]:
    """Delegate to the site scraper's discovery implementation."""
    return scraper.discover_urls(page, query, cfg)
