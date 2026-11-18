"""
Image downloads via `requests`.
Amazon URL extraction: `scraper.images`. Flipkart / Snapdeal helpers below.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import ScraperConfig
from utils.logger import get_logger
from utils.retry import retry_with_backoff

logger = get_logger()

_FLIP_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:flipkart|flixcart|rukmini)[^\s\"'<>]+",
    re.I,
)
_SD_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:snapdeal|sdcdn|snapdealassets)[^\s\"'<>]+",
    re.I,
)


def _unique_ordered(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip().split(" ")[0]
        if not u or u in seen or not u.startswith("http"):
            continue
        seen.add(u)
        out.append(u)
    return out


def _is_flipkart_real_product_image(url: str) -> bool:
    """
    Flipkart serves product gallery JPEGs/WebPs from rukminim…/image/… .
    Ignore promos, header chrome (batman-returns SVGs), and other CDNs picked up by loose regex.
    """
    u = url.lower()
    host = (urlparse(url).netloc or "").lower()
    path = (urlparse(url).path or "").lower()

    if "/promos/" in u or "/promo/" in u or "/www/0/0/promos/" in u:
        return False
    if "static-assets-web.flixcart.com" in host or "batman-returns" in u:
        return False
    if u.endswith(".svg"):
        return False
    # Gallery assets use this path prefix on rukmini CDNs
    if "rukminim" in host and "/image/" in path:
        return True
    if "rukmini" in u and "/image/" in u and "/promos/" not in u:
        return True
    return False


def extract_flipkart_image_urls(html: str) -> list[str]:
    """Gallery / hero images on Flipkart PDP (promo/badge URLs filtered out)."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    # Do not use `[src*="flixcart"]` — it matches static-assets-web.flixcart.com (nav SVGs).
    for img in soup.select(
        'img[src*="rukminim"], img[src*="rukmini1"], img[src*="flipkartcdn"], img[src*="img1a.flixcart"]',
    ):
        for attr in ("src", "data-src"):
            v = img.get(attr)
            if v and v.startswith("http") and _is_flipkart_real_product_image(v):
                found.append(v)
    # Narrow blocks — avoid div._1AtVbE (page-wide) which pulls nav/footer URLs into _FLIP_RE
    for block in soup.select(
        "div._3kidJX, div._2FN7qH, div.CXW8mj, div._2eMuZq, "
        "div._1yVuTs, div.VCNdH6, li._3QTJsI",
    ):
        for m in _FLIP_RE.findall(str(block)):
            if _is_flipkart_real_product_image(m):
                found.append(m)
    return _unique_ordered([u for u in found if _is_flipkart_real_product_image(u)])


def extract_snapdeal_image_urls(html: str) -> list[str]:
    """Product images on Snapdeal PDP (CDN patterns vary)."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for img in soup.select(
        'img[src*="snapdeal"], img[src*="sdcdn"], img[data-src*="snapdeal"], #bx-slider img',
    ):
        for attr in ("src", "data-src", "data-lazy"):
            v = img.get(attr)
            if v and v.startswith("http"):
                found.append(v)
    wrap = soup.select_one("#bx-slider, .product-slider, .product-detail, #productCarousel")
    if wrap:
        found.extend(_SD_RE.findall(str(wrap)))
    return _unique_ordered(found)


def _guess_extension(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"


@retry_with_backoff(max_attempts=3, base_delay_sec=0.8)
def _download_one(url: str, dest: Path, timeout: float) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    # Flipkart CDN sometimes expects a referer for non-promo image paths
    if "flipkart" in url.lower() or "flixcart" in url.lower() or "rukmini" in url.lower():
        headers["Referer"] = "https://www.flipkart.com/"

    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def download_product_images(
    image_urls: list[str],
    images_dir: Path,
    cfg: ScraperConfig,
) -> list[dict[str, str]]:
    """
    Download images sequentially; filenames img1.jpg, img2.png, … (per spec).
    Returns [{"url", "local_path"}, …].
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    mapping: list[dict[str, str]] = []
    timeout = cfg.image_download_timeout_sec

    for idx, url in enumerate(image_urls, start=1):
        ext = _guess_extension(url)
        fname = f"img{idx}{ext}"
        dest = images_dir / fname
        try:
            _download_one(url, dest, timeout)
            mapping.append(
                {"url": url, "local_path": str(dest.resolve().as_posix())},
            )
        except Exception as e:
            logger.warning("Image download failed (%s): %s", url[:90], e)

    return mapping
