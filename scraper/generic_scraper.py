"""
Placeholder scraper for unsupported hosts — demonstrates plugin boundary.

Extend this class or register a dedicated scraper in `factory.get_scraper`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from config import ScraperConfig
from scraper.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger()


class GenericScraper(BaseScraper):
    """No-op discovery / stub PDP — logs guidance for implementing a new marketplace."""

    def prepare(self, user_site_url: str) -> None:
        super().prepare(user_site_url)
        logger.warning(
            "GenericScraper active for %s — implement a dedicated scraper for production.",
            self.site_label,
        )

    def discover_urls(self, page: Page, _query: str, cfg: ScraperConfig) -> list[str]:
        del page, cfg
        logger.info("GenericScraper.discover_urls: returning empty (not implemented).")
        return []

    def scrape_product(
        self,
        page: Page,
        product_url: str,
        query: str,
        product_dir: Path,
        cfg: ScraperConfig,
    ) -> dict[str, Any]:
        del page, query, cfg
        out_path = str(product_dir.resolve())
        return {
            "country": "",
            "language": "en",
            "site": self.site_label,
            "site_domain": self.site_label,
            "product_name": "",
            "product_reference": "",
            "brand": "",
            "ean": "",
            "product_url": product_url,
            "category": "",
            "breadcrumbs": "",
            "price": "",
            "currency": "",
            "availability": "",
            "short_description": "",
            "specification_count": 0,
            "image_count": 0,
            "main_image_path": "",
            "seller_count": 0,
            "extraction_timestamp": "",
            "status": "ERROR",
            "output_folder_path": out_path,
            "output_path": out_path,
            "html_path": "",
            "json_path": "",
            "scrape_duration_seconds": 0.0,
            "reference": "",
            "query": query,
            "image_download_failures": 0,
        }
