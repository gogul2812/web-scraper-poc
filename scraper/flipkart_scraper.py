"""
Flipkart: search discovery + PDP parse + validation + sellers heuristic + images.
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
from services.image_service import download_product_images, extract_flipkart_image_urls
from services.product_service import persist_product_artifacts
from services.validation_service import is_valid_product
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.rate_limit import pause_between_requests
from utils.retry import retry_with_backoff

logger = get_logger()

_ITM = re.compile(r"/p/itm[a-z0-9]+", re.I)


class FlipkartScraper(BaseScraper):
    def __init__(self) -> None:
        self._root_url: str = "https://www.flipkart.com"

    def prepare(self, user_site_url: str) -> None:
        super().prepare(user_site_url)
        p = urlparse(user_site_url.strip() or "https://www.flipkart.com")
        if not p.netloc and user_site_url:
            p = urlparse("https://" + user_site_url.strip())
        if p.netloc:
            self._root_url = f"{p.scheme or 'https'}://{p.netloc.rstrip('/')}"

    def _search_url(self, query: str, page_num: int) -> str:
        q = quote_plus(query)
        if page_num <= 1:
            return f"{self._root_url}/search?q={q}"
        return f"{self._root_url}/search?q={q}&page={page_num}"

    def _normalize_pdp(self, href: str | None) -> str | None:
        if not href or href.startswith("#"):
            return None
        full = urljoin(self._root_url + "/", href)
        pu = urlparse(full)
        if "flipkart.com" not in pu.netloc.lower():
            return None
        path = pu.path or ""
        if not _ITM.search(path):
            return None
        return f"{pu.scheme}://{pu.netloc}{path.split('?')[0]}"

    @retry_with_backoff(max_attempts=3, base_delay_sec=1.0)
    def _goto(self, page: Page, url: str, timeout_ms: int) -> None:
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_timeout(700)

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
            logger.info("Flipkart discovery page %s — %s", page_num, url)
            try:
                self._goto(page, url, cfg.navigation_timeout_ms)
            except Exception as e:
                logger.error("Flipkart search navigation failed: %s", e)
                continue
            try:
                html = get_stable_html(page, cfg.navigation_timeout_ms)
            except Exception as e:
                logger.error("Flipkart search HTML read failed: %s", e)
                continue
            if page_contains_captcha(html):
                logger.error("CAPTCHA on Flipkart search — stopping discovery.")
                break

            before = len(collected)
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select('a[href*="/p/itm"]'):
                norm = self._normalize_pdp(a.get("href"))
                if norm and norm not in seen:
                    seen.add(norm)
                    collected.append(norm)
                    if len(collected) >= cap:
                        return collected[:cap]

            if len(collected) == before:
                logger.info("Flipkart: no new URLs on page %s — stopping.", page_num)
                break

            logger.info("Flipkart page %s — URLs so far: %s", page_num, len(collected))

        return collected[:cap]

    def _parse_pdp(self, html: str, product_url: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        title = ""
        for sel in ("span.B_NuCI", "h1 span", "h1"):
            el = soup.select_one(sel)
            if el:
                title = " ".join(el.get_text().split())
                if title:
                    break

        price = ""
        for sel in ("div._30jeq3._16Jk6d", "div._30jeq3", "div._25b18c"):
            el = soup.select_one(sel)
            if el:
                price = " ".join(el.get_text().split())
                if price:
                    break

        brand = ""
        el = soup.select_one("#productSpecifications_table td, div._3dtsFj")
        if el:
            brand = " ".join(el.get_text().split())[:160]

        desc = ""
        bullets = soup.select("div._2pnIZ_ li, ul._1-qLlW li")
        if bullets:
            desc = " ".join(" ".join(li.get_text().split()) for li in bullets[:10])

        sellers = self._sellers_from_pdp(soup, price)

        return {
            "product_name": title,
            "product_url": product_url,
            "price": price,
            "brand": brand,
            "ean": "",
            "description": desc,
            "sellers": sellers,
        }

    def _sellers_from_pdp(self, soup: BeautifulSoup, fallback_price: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for block in soup.select("div._1SLlWc, div._2_I_eb, #sellerName"):
            txt = " ".join(block.get_text().split())
            if txt and len(txt) > 2:
                out.append({"seller": txt[:200], "price": fallback_price})
                break
        if not out:
            sold = soup.find(string=re.compile(r"sold\s+by", re.I))
            if sold and sold.parent:
                seg = sold.parent.get_text(" ", strip=True)
                if seg:
                    out.append({"seller": seg[:200], "price": fallback_price})
        if not out:
            out.append({"seller": "Flipkart seller network", "price": fallback_price})
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
                logger.info("Filtered by validation (Flipkart): %s", product_url[:80])
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
            img_urls = extract_flipkart_image_urls(html)
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
            logger.exception("Flipkart scrape error %s: %s", product_url, e)
            row["status"] = "ERROR"
            return row
