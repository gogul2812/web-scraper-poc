"""
Abstract base for marketplace scrapers (Amazon, Flipkart, …).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from config import ScraperConfig


class BaseScraper(ABC):
    """One scraper instance per marketplace domain."""

    #: Short label for JSON/Excel (e.g. "amazon.in") — set in prepare()
    site_label: str = ""
    #: Width for `product_{index:0{N}d}` folder names (overridden from config in main).
    product_id_padding: int = 6

    def prepare(self, user_site_url: str) -> None:
        """Call once per run with the user-provided site URL; sets `site_label`."""
        from utils.folder_manager import hostname_to_site_folder

        self.site_label = hostname_to_site_folder(user_site_url)

    @abstractmethod
    def discover_urls(self, page: Page, query: str, cfg: ScraperConfig) -> list[str]:
        """Return canonical product URLs up to the configured discovery cap."""

    def format_product_id(self, index_1based: int) -> str:
        """Filesystem folder name for the Nth product (override per site if needed)."""
        w = min(max(int(getattr(self, "product_id_padding", 6)), 3), 9)
        return "product_" + format(index_1based, f"0{w}d")

    @abstractmethod
    def scrape_product(
        self,
        page: Page,
        product_url: str,
        query: str,
        product_dir: Path,
        cfg: ScraperConfig,
    ) -> dict[str, Any]:
        """
        Fetch PDP, persist HTML/JSON/images under product_dir.
        Returns one Excel row dict: query, site, product_name, product_url, price,
        seller_count, image_count, status.
        Must not raise — failures return status ERROR/BLOCKED.
        """
