"""
Multi-seller extraction: offer-listing page and fallbacks.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from config import ScraperConfig
from scraper import selectors as sel
from utils.html_checks import page_contains_captcha
from utils.logger import get_logger
from utils.playwright_html import get_stable_html
from utils.retry import retry_with_backoff

logger = get_logger()

OFFER_LISTING_PATH = "/gp/offer-listing/"


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _seller_link_from_product_soup(soup: BeautifulSoup, base_host: str) -> str | None:
    """Find 'See all buying options' / 'Other sellers' / offer-listing href."""
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.strip().lower().startswith("javascript"):
            continue
        h_low = href.lower()
        text = _clean(a.get_text()).lower()
        if OFFER_LISTING_PATH in h_low or "offer-listing" in h_low:
            candidates.append(href)
            continue
        if "olp" in h_low and "offer" in h_low:
            candidates.append(href)
            continue
        if sel.TEXT_SEE_ALL_BUYING in text or sel.TEXT_OTHER_SELLERS in text:
            candidates.append(href)
    if not candidates:
        return None
    return urljoin(base_host, candidates[0])


def _parse_offer_listing(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select(sel.OFFER_ROW)
    if not rows:
        rows = soup.select("#olpOfferList div.olpOffer, .olpOfferWrapper")
    out: list[dict[str, str]] = []
    for row in rows:
        price_el = row.select_one(sel.OFFER_PRICE) or row.select_one("span.a-color-price")
        name_el = row.select_one(sel.OFFER_SELLER_NAME) or row.select_one(".a-spacing-none .a-link-normal")
        price = _clean(price_el.get_text()) if price_el else ""
        seller = _clean(name_el.get_text()) if name_el else ""
        if not seller:
            ign = row.select_one('img[alt*="seller"]')
            if ign and ign.get("alt"):
                seller = _clean(ign.get("alt"))
        if price or seller:
            out.append({"seller": seller, "price": price})
    return out


@retry_with_backoff(max_attempts=3, base_delay_sec=1.0)
def _goto(page: Page, url: str, timeout_ms: int) -> None:
    # "load" reduces races where `content()` runs while a redirect is still in flight
    page.goto(url, wait_until="load", timeout=timeout_ms)
    page.wait_for_timeout(900)


def collect_sellers(
    page: Page,
    product_html: str,
    product_url: str,
    buybox_price: str,
    buybox_seller_hint: str,
    cfg: ScraperConfig,
) -> list[dict[str, str]]:
    """
    Open seller listing when available; otherwise return single-seller list from buy box.
    """
    # Import here to avoid circular imports at module load
    from scraper.product import extract_asin_from_url

    parsed = urlparse(product_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    soup = BeautifulSoup(product_html, "lxml")

    offer_url = _seller_link_from_product_soup(soup, base)
    asin = extract_asin_from_url(product_url)
    if not offer_url and asin:
        offer_url = f"{base.rstrip('/')}{OFFER_LISTING_PATH}{asin}"

    if not offer_url:
        logger.info("No offer-listing URL; using buy-box only for %s", product_url)
        return _single_seller_fallback(buybox_price, buybox_seller_hint)

    full_offer_url = offer_url if offer_url.startswith("http") else urljoin(base, offer_url)

    try:
        _goto(page, full_offer_url, cfg.navigation_timeout_ms)
    except Exception as e:
        logger.warning("Offer listing navigation failed (%s): %s", full_offer_url, e)
        return _single_seller_fallback(buybox_price, buybox_seller_hint)

    try:
        ol_html = get_stable_html(page, cfg.navigation_timeout_ms)
    except Exception as e:
        logger.warning(
            "Could not read offer-listing HTML (nav race) for %s — using buy box. %s",
            product_url,
            e,
        )
        return _single_seller_fallback(buybox_price, buybox_seller_hint)
    if page_contains_captcha(ol_html):
        logger.error("CAPTCHA on offer listing — falling back to buy box for %s", product_url)
        return _single_seller_fallback(buybox_price, buybox_seller_hint)

    sellers = _parse_offer_listing(ol_html)
    if not sellers:
        logger.debug("No seller rows on offer listing; using buy-box fallback for %s", product_url)
        return _single_seller_fallback(buybox_price, buybox_seller_hint)

    return sellers


def _single_seller_fallback(price: str, seller: str) -> list[dict[str, str]]:
    s = seller or "Unknown"
    p = price or ""
    return [{"seller": s, "price": p}]
