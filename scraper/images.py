"""
Amazon PDP image URL extraction (downloads handled by `services.image_service`).
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from scraper import selectors as sel

_AMAZON_IMG_HOST = re.compile(
    r"https?://(?:m\.media-amazon\.com|images-na\.ssl-images-amazon\.com)[^\s\"'<>]+",
    re.I,
)


def _unique_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip()
        if not u or u in seen:
            continue
        if not u.startswith("http"):
            continue
        seen.add(u)
        out.append(u)
    return out


def _urls_from_dynamic_image_attr(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return list(data.keys())
    except json.JSONDecodeError:
        pass
    return _unique_preserve_order(_AMAZON_IMG_HOST.findall(raw))


def extract_image_urls(html: str) -> list[str]:
    """Collect main + gallery image URLs from Amazon product HTML."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []

    main = soup.select_one(sel.MAIN_IMAGE)
    if main:
        for attr in ("data-old-hires", "src"):
            v = main.get(attr)
            if v:
                found.append(v)
        dyn = main.get("data-a-dynamic-image")
        found.extend(_urls_from_dynamic_image_attr(dyn))

    container = soup.select_one(sel.IMAGE_BLOCK) or soup
    for img in container.select(f"{sel.ALT_IMAGES_CONTAINER} img, #imageBlock img"):
        for attr in ("data-old-hires", "src", "data-src"):
            v = img.get(attr)
            if v:
                found.append(v)
        dyn = img.get("data-a-dynamic-image")
        found.extend(_urls_from_dynamic_image_attr(dyn))

    block_html = ""
    ib = soup.select_one(sel.IMAGE_BLOCK)
    if ib:
        block_html = str(ib)
    found.extend(_AMAZON_IMG_HOST.findall(block_html))

    cleaned: list[str] = []
    for u in _unique_preserve_order(found):
        ul = u.lower()
        if "play-button" in ul:
            continue
        cleaned.append(u.split(" ")[0])
    return _unique_preserve_order(cleaned)
