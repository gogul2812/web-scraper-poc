"""
Product detail page: title, price, brand, description, buy-box hints.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from config import ScraperConfig
from scraper import selectors as sel
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.retry import retry_with_backoff

logger = get_logger()

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)


def extract_asin_from_url(url: str) -> str | None:
    m = ASIN_RE.search(url)
    return m.group(1) if m else None


def _clean_text(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _price_from_buybox_scopes(soup: BeautifulSoup) -> str:
    """
    Full price text inside buy-box only (avoids EMI lines and random page offscreens).
    """
    for scope in soup.select(
        "#corePrice_feature_div, #apex_desktop, #apex_offerDisplay_desktop, "
        "#mobileBuySection, #mobile-buybox",
    ):
        for off in scope.select("span.a-offscreen"):
            t = _clean_text(off.get_text())
            if "₹" not in t or not re.search(r"\d", t):
                continue
            low = t.lower()
            if any(x in low for x in ("emi", "/mo", "month", "coupon", "cashback")):
                continue
            return t
    return ""


def _first_price_from_offscreen(soup: BeautifulSoup) -> str:
    """Legacy fallback — may pick non-deal lines; prefer buy-box scopes first."""
    for node in soup.select(sel.PRICE_OFFSCREEN):
        t = _clean_text(node.get_text())
        if t and any(ch.isdigit() for ch in t):
            return t
    return ""


def _price_from_parts(soup: BeautifulSoup) -> str:
    """
    Amazon splits display price into whole + fraction spans.
    Must join with a decimal: 2,999 + 00 → 2,999.00 (not 2,99900).
    """
    whole = soup.select_one(sel.PRICE_WHOLE)
    frac_el = soup.select_one(sel.PRICE_FRACTION)
    if whole:
        w = _clean_text(whole.get_text()).rstrip(".")
        f = _clean_text(frac_el.get_text()) if frac_el else ""
        sym = "₹"
        if w.startswith("₹"):
            sym = ""
        if f:
            raw = f"{sym}{w}.{f}"
        else:
            raw = f"{sym}{w}"
        if raw:
            return raw
    return ""


def _brand_from_page(soup: BeautifulSoup) -> str:
    byline = soup.select_one(sel.BRAND_BYLINE)
    if byline:
        return _clean_text(byline.get_text())
    for row in soup.select("tr"):
        header = row.find("th")
        if header and "brand" in header.get_text("", strip=True).lower():
            data = row.find("td")
            if data:
                return _clean_text(data.get_text())
    return ""


def _ean_from_page(soup: BeautifulSoup) -> str:
    """Best-effort EAN/GTIN from technical detail tables or visible text."""
    for row in soup.select(
        "#productDetails_detailBullets_sections1 tr, "
        "#productDetails_techSpec_section_1 tr, "
        "table.prodDetTable tr, "
        "#detailBullets_feature_div li",
    ):
        cells = row.find_all(["th", "td", "span"])
        text_blob = " ".join(c.get_text(" ", strip=True).lower() for c in cells[:3])
        if "ean" in text_blob or "gtin" in text_blob or "article model" in text_blob:
            for c in cells:
                m = re.search(r"\b(\d{8,14})\b", c.get_text())
                if m:
                    return m.group(1).strip()
    chunk = soup.get_text(" ", strip=True)
    m = re.search(r"\b(\d{13})\b", chunk)
    if m:
        return m.group(1)
    return ""


def _description_from_page(soup: BeautifulSoup) -> str:
    bullets = soup.select_one(sel.FEATURE_BULLETS)
    if bullets:
        parts = [_clean_text(li.get_text()) for li in bullets.select("li")]
        parts = [p for p in parts if p]
        if parts:
            return " ".join(parts[:12])
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return _clean_text(meta["content"])
    return ""


def parse_product_html(html: str, product_url: str) -> dict[str, Any]:
    """
    Parse saved/live HTML into core product fields.
    Does not perform network I/O.
    """
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one(sel.PRODUCT_TITLE)
    product_name = _clean_text(title_el.get_text()) if title_el else ""

    # Prefer full buy-box price (correct ₹.xxx.xx); avoid glued whole+fraction before scopes
    price = ""
    bb = soup.select_one(sel.BUYBOX_PRICE)
    if bb:
        price = _clean_text(bb.get_text())
    if not price:
        price = _price_from_buybox_scopes(soup)
    if not price:
        price = _price_from_parts(soup)
    if not price:
        price = _first_price_from_offscreen(soup)

    brand = _brand_from_page(soup)
    description = _description_from_page(soup)
    ean = _ean_from_page(soup)

    buybox_seller = ""
    seller_trigger = soup.select_one("#sellerProfileTriggerId, a#sellerProfileTriggerId")
    if seller_trigger:
        buybox_seller = _clean_text(seller_trigger.get_text())
    if not buybox_seller:
        mi = soup.select_one("#merchant-info, #availability_feature_div")
        if mi:
            buybox_seller = _clean_text(mi.get_text())[:200]

    return {
        "product_name": product_name,
        "product_url": product_url,
        "price": price,
        "brand": brand,
        "ean": ean,
        "description": description,
        "buybox_seller_hint": buybox_seller,
    }


@retry_with_backoff(max_attempts=3, base_delay_sec=1.0)
def _goto_product(page: Page, url: str, timeout_ms: int) -> None:
    page.goto(url, wait_until="load", timeout=timeout_ms)
    # Allow lazy scripts / buy box to populate
    page.wait_for_timeout(1200)


def load_product_page(page: Page, product_url: str, cfg: ScraperConfig) -> tuple[str, str]:
    """
    Navigate to product URL and return (html, status).
    status: 'ok' | 'blocked' | 'error'
    """
    try:
        _goto_product(page, product_url, cfg.navigation_timeout_ms)
    except Exception as e:
        logger.warning("Navigation failed for %s: %s", product_url, e)
        return "", "error"

    try:
        html = get_stable_html(page, cfg.navigation_timeout_ms)
    except Exception as e:
        logger.warning("Could not read product HTML (nav race/timeout) for %s: %s", product_url, e)
        return "", "error"
    if page_contains_captcha(html):
        logger.error("CAPTCHA detected on product page: %s", product_url)
        return html, "blocked"
    return html, "ok"
