"""
Amazon marketplace scraper: discovery, PDP parse, validation gate, sellers, images.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page

from config import ScraperConfig
from scraper import storage
from scraper.base_scraper import BaseScraper
from scraper.discovery import discover_with_audit
from services.discovery_audit import DiscoveryAuditSession
from scraper.images import extract_image_urls
from scraper.product import load_product_page, parse_product_html
from scraper.sellers import collect_sellers
from services.image_service import download_product_images
from services.product_service import persist_product_artifacts
from services.validation_service import is_valid_product
from utils.logger import get_logger
from utils.rate_limit import pause_between_requests

logger = get_logger()


class AmazonScraper(BaseScraper):
    def __init__(self) -> None:
        self._root_url: str = "https://www.amazon.in"
        self.discovery_stats: dict[str, object] = {}
        self.discovery_audit: DiscoveryAuditSession | None = None

    def prepare(self, user_site_url: str) -> None:
        super().prepare(user_site_url)
        p = urlparse(user_site_url.strip() or "https://www.amazon.in")
        if not p.netloc and user_site_url:
            p = urlparse("https://" + user_site_url.strip())
        if p.netloc:
            self._root_url = f"{p.scheme or 'https'}://{p.netloc.rstrip('/')}"

    def _cfg_for_query(self, query: str, base: ScraperConfig) -> ScraperConfig:
        return replace(
            base,
            base_url=self._root_url,
            search_query=query,
        )

    def discover_urls(self, page: Page, query: str, cfg: ScraperConfig) -> list[str]:
        c = self._cfg_for_query(query, cfg)
        result = discover_with_audit(
            page,
            c,
            site_label=self.site_label or "amazon.in",
            full_pagination=cfg.discovery_audit_full_pagination,
        )
        self.discovery_audit = result.audit
        self.discovery_stats = result.audit.to_summary_dict()
        self.discovery_stats["extraction_urls_queued"] = len(result.extraction_urls)
        return result.extraction_urls

    def scrape_product(
        self,
        page: Page,
        product_url: str,
        query: str,
        product_dir: Path,
        cfg: ScraperConfig,
    ) -> dict[str, Any]:
        c = self._cfg_for_query(query, cfg)
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
            html, load_status = load_product_page(page, product_url, c)
            if load_status == "blocked":
                if html:
                    storage.save_page_html(product_dir, html)
                row["status"] = "BLOCKED"
                return row
            if load_status == "error" or not html:
                return row

            parsed = parse_product_html(html, product_url)
            row["product_name"] = parsed.get("product_name", "") or ""
            row["brand"] = parsed.get("brand", "") or ""
            row["ean"] = parsed.get("ean", "") or ""
            row["price"] = parsed.get("price", "") or ""

            if c.enforce_product_validation and not is_valid_product(
                parsed.get("ean", "") or "",
                parsed.get("brand", "") or "",
                parsed.get("product_name", "") or "",
                query,
            ):
                logger.info("Filtered by validation rules: %s", product_url[:80])
                record = storage.MultiSiteProductRecord(
                    site=self.site_label,
                    query=query,
                    product_name=parsed.get("product_name", "") or "",
                    product_url=product_url,
                    brand=parsed.get("brand", "") or "",
                    ean=parsed.get("ean", "") or "",
                    price=parsed.get("price", "") or "",
                    description=parsed.get("description", "") or "",
                    sellers=[],
                    images=[],
                    status="FILTERED",
                )
                persist_product_artifacts(product_dir, html, record)
                row["status"] = "FILTERED"
                row["seller_count"] = 0
                row["image_count"] = 0
                return row

            pause_between_requests(c)
            sellers_list = collect_sellers(
                page,
                html,
                product_url,
                parsed.get("price", "") or "",
                parsed.get("buybox_seller_hint", "") or "",
                c,
            )

            pause_between_requests(c)
            img_urls = extract_image_urls(html)
            if c.save_product_images and img_urls:
                images_map = download_product_images(img_urls, product_dir / "images", c)
            else:
                images_map = []

            record = storage.MultiSiteProductRecord(
                site=self.site_label,
                query=query,
                product_name=parsed.get("product_name", "") or "",
                product_url=product_url,
                brand=parsed.get("brand", "") or "",
                ean=parsed.get("ean", "") or "",
                price=parsed.get("price", "") or "",
                description=parsed.get("description", "") or "",
                sellers=[
                    {"seller": x.get("seller", ""), "price": x.get("price", "")}
                    for x in sellers_list
                ],
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
            logger.exception("Amazon scrape error %s: %s", product_url, e)
            row["status"] = "ERROR"
            return row
