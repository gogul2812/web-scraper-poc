"""
Parse Hybris-style markup from www.seb.fr product detail pages.
Selectors evolve — prefer resilient patterns (meta tags, /medias/?context= URLs, analytics snippets).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Product detail URLs end with /p/<numeric_id>
_PDP_PATH = re.compile(r"/p/(\d{6,})(?:/|$|\?)")
# Reference codes often appear in titles (e.g. FZ760800)
_REF_LIKE = re.compile(r"\b([A-Z]{1,4}\d{4,10})\b")
# Analytics / dataLayer price in embedded scripts
_PRICE_SNIPPET = re.compile(r'"price"\s*:\s*(\d+(?:\.\d+)?)', re.I)
_EUR_TEXT = re.compile(r"(\d+(?:[.,]\d{2}))\s*€")
_EAN = re.compile(r"\b(3\d{12}|0\d{12,13})\b")
_MEDIA_CTX = re.compile(r"/medias/\?context=[A-Za-z0-9+/=_-]+")
_PDP_IN_HTML = re.compile(r'(?:"|\')((?:https?://[^"\']+)?/[^"\']*/p/\d{6,}(?:\?[^"\']*)?)(?:"|\')', re.I)
_STATIC_EXT = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
    ".pdf",
    ".xml",
    ".zip",
)
_LISTING_KEYWORDS = (
    "actifry",
    "friteuses",
    "autocuiseur",
    "cuisson",
    "vapeur",
    "fours",
    "desserts",
    "boissons",
    "preparation",
    "aliment",
    "mixeur",
    "hachoir",
    "yaourt",
    "cocotte",
    "tireuse",
    "sorbet",
    "boutique-accessoires",
)


def _path_is_valid_pdp(path: str) -> bool:
    """Hybris PDP paths include category/slug segments before `/p/<numeric_id>`."""
    path = path.split("?")[0]
    if not _PDP_PATH.search(path):
        return False
    segments = [x for x in path.split("/") if x]
    # e.g. /Family/Subfamily/Product-slug/p/1510001371 → ≥4 segments before trailing /p/id
    if len(segments) < 4 or segments[-2] != "p":
        return False
    return True


def normalize_pdp_url(root: str, current_page_url: str, href: str | None) -> str | None:
    """Resolve listing links to canonical https://www.seb.fr/.../p/<id> URLs."""
    if not href or href.startswith("#") or "javascript" in href.lower():
        return None
    full = urljoin(current_page_url, href.split("#")[0])
    p = urlparse(full)
    path = (p.path or "").split("?")[0]
    # Some listings emit bogus hosts (e.g. product slug as hostname); still carry a valid /p/<id> path.
    if _path_is_valid_pdp(path) and "seb.fr" not in (p.netloc or "").lower():
        return f"{root.rstrip('/')}{path}"
    if "seb.fr" not in (p.netloc or "").lower():
        full = urljoin(root.rstrip("/") + "/", href.lstrip("/"))
        p = urlparse(full)
        path = (p.path or "").split("?")[0]
    if "seb.fr" not in (p.netloc or "").lower():
        return None
    if not _path_is_valid_pdp(path):
        return None
    return f"{p.scheme}://{p.netloc}{path}"


def extract_pdp_urls_from_page(site_root: str, page_url: str, html: str) -> set[str]:
    """Collect product URLs visible in HTML (listings + homepage modules)."""
    out: set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        u = normalize_pdp_url(site_root, page_url, a.get("href"))
        if u:
            out.add(u)
    for u in extract_pdp_urls_from_raw_html(site_root, page_url, html):
        out.add(u)
    return out


def extract_pdp_url_occurrences(site_root: str, page_url: str, html: str) -> list[str]:
    """
    Every PDP link occurrence in markup (duplicates allowed across anchors / regex hits).
    Used for benchmark load estimation — not for production deduplication.
    """
    occ: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        u = normalize_pdp_url(site_root, page_url, a.get("href"))
        if u:
            occ.append(u)
    for m in _PDP_IN_HTML.finditer(html):
        u = normalize_pdp_url(site_root, page_url, m.group(1))
        if u:
            occ.append(u)
    return occ


def extract_category_url_occurrences(root: str, html: str) -> list[str]:
    """Category `/c/` link occurrences (duplicates allowed) for benchmark metrics."""
    occ: list[str] = []
    base_root = root.rstrip("/")
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        h = a.get("href") or ""
        if "/c/" not in h:
            continue
        full = urljoin(base_root + "/", h.split("#")[0])
        p = urlparse(full)
        if "seb.fr" not in (p.netloc or "").lower():
            continue
        path = (p.path or "").split("?")[0]
        if "/c/" in path:
            occ.append(f"{p.scheme}://{p.netloc}{path}")
    return occ


def extract_category_urls(root: str, html: str) -> set[str]:
    """Category hubs contain `/c/` in the path."""
    out: set[str] = set()
    base_root = root.rstrip("/")
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        h = a.get("href") or ""
        if "/c/" not in h:
            continue
        full = urljoin(base_root + "/", h.split("#")[0])
        p = urlparse(full)
        if "seb.fr" not in (p.netloc or "").lower():
            continue
        path = (p.path or "").split("?")[0]
        if "/c/" in path:
            out.add(f"{p.scheme}://{p.netloc}{path}")
    return out


def extract_pdp_urls_from_raw_html(site_root: str, page_url: str, html: str) -> set[str]:
    """Fallback pass for product URLs embedded in scripts/JSON blobs."""
    out: set[str] = set()
    for m in _PDP_IN_HTML.finditer(html):
        u = normalize_pdp_url(site_root, page_url, m.group(1))
        if u:
            out.add(u)
    return out


def extract_discovery_listing_urls(
    root: str,
    page_url: str,
    html: str,
    *,
    strict: bool = True,
) -> set[str]:
    """
    Internal listing/category pages to enqueue during discovery.

    ``strict=True`` (default): only Hybris-style `/c/` hubs — matches production crawlers
    that avoid exploding the queue with shallow internal links.

    ``strict=False``: legacy broader heuristic (keywords + short paths). Use only when
    `/c/` coverage is insufficient and you accept a much larger queue.
    """
    if strict:
        return extract_category_urls(root, html)

    out: set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    base = root.rstrip("/") + "/"
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:")):
            continue
        if href.lower().startswith("javascript"):
            continue
        full = urljoin(page_url or base, href.split("#")[0])
        p = urlparse(full)
        if "seb.fr" not in (p.netloc or "").lower():
            continue
        path = (p.path or "").split("?")[0]
        if not path or path == "/":
            continue
        if path.lower().endswith(_STATIC_EXT):
            continue
        if _path_is_valid_pdp(path):
            continue
        low = path.lower()
        segs = [x for x in path.split("/") if x]
        if "/c/" in low or any(k in low for k in _LISTING_KEYWORDS) or len(segs) <= 3:
            out.add(f"{p.scheme}://{p.netloc}{path}")
    return out


def _meta_content(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        tag = soup.select_one(f'meta[property="{prop}"]')
    else:
        tag = soup.select_one(f'meta[name="{name}"]')
    if tag:
        return (tag.get("content") or "").strip()
    return ""


def _extract_price(html: str) -> tuple[str, str]:
    """Return (amount_string, currency). Currency defaults to EUR."""
    m = _PRICE_SNIPPET.search(html)
    if m:
        return m.group(1).replace(".", ","), "EUR"
    m2 = _EUR_TEXT.search(html)
    if m2:
        return m2.group(1).replace(".", ","), "EUR"
    return "", "EUR"


def _extract_reference(html: str, title: str) -> str:
    for blob in (title, html[:120000]):
        m = _REF_LIKE.search(blob)
        if m:
            return m.group(1)
    return ""


def _collect_specs(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                k = cells[0].get_text(" ", strip=True)
                v = cells[1].get_text(" ", strip=True)
                if k and v and len(k) < 240:
                    specs[k] = v
    for dl in soup.select("dl"):
        for dt, dd in zip(dl.select("dt"), dl.select("dd")):
            k = dt.get_text(" ", strip=True)
            v = dd.get_text(" ", strip=True)
            if k and v:
                specs[k] = v
    return specs


def _long_description(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for sel in (
        "div.product-description",
        "div#productDetailDescription",
        "section.product-details",
        "div.tab-pane",
        "div.product-usps",
    ):
        for block in soup.select(sel):
            txt = block.get_text(" ", strip=True)
            if len(txt) > 40:
                parts.append(txt)
    if parts:
        return "\n\n".join(parts)[:8000]
    # Fallback: main prose after H1
    h1 = soup.select_one("h1")
    if h1 and h1.parent:
        return " ".join(h1.parent.get_text().split())[:4000]
    return ""


def extract_media_urls(html: str, base: str) -> list[str]:
    """Ordered unique product imagery URLs (Hybris `context` blob URLs)."""
    seen: set[str] = set()
    ordered: list[str] = []
    root = base.rstrip("/")

    for m in _MEDIA_CTX.finditer(html):
        rel = m.group(0)
        full = urljoin(root + "/", rel.lstrip("/"))
        if full not in seen:
            seen.add(full)
            ordered.append(full)

    soup = BeautifulSoup(html, "lxml")
    og = _meta_content(soup, prop="og:image")
    if og:
        full = urljoin(root + "/", og.lstrip("/"))
        if full not in seen and "/medias/" in full:
            seen.add(full)
            ordered.insert(0, full)

    return ordered


def _junk_media(url: str) -> bool:
    u = url.lower()
    if "/medias/?context=" in u:
        return False
    junk = (
        "seb.png",
        "logo",
        "faq.png",
        "marker.png",
        "newsletter",
        "picto-",
        "icone-reparation",
        "batman-returns",
        "contact-us",
        "garantie.png",
        "manual.png",
        "sav.png",
    )
    return any(j in u for j in junk)


def filter_product_medias(urls: list[str]) -> list[str]:
    """Drop obvious chrome icons; keep Hybris `context` gallery assets."""
    out: list[str] = []
    for u in urls:
        if _junk_media(u):
            continue
        out.append(u)
    return out


def assign_image_roles(urls: list[str]) -> list[dict[str, str]]:
    """
    Label gallery positions — site does not always expose semantic roles in DOM.
    Heuristic: first = main, next few = secondary, mid = detail, tail = lifestyle.
    """
    roles: list[dict[str, str]] = []
    n = len(urls)
    for i, u in enumerate(urls):
        if i == 0:
            role = "main"
        elif i <= 3:
            role = "secondary"
        elif i <= min(12, max(4, n // 2)):
            role = "detail"
        else:
            role = "lifestyle"
        roles.append({"url": u, "role": role})
    return roles


def _normalize_brand(raw: str) -> str:
    r = (raw or "").strip()
    if re.match(r"^seb\b", r, re.I):
        return "SEB"
    return r


def extract_breadcrumbs_and_category(html: str, product_url: str) -> tuple[str, str]:
    """Return (breadcrumb trail, best-effort leaf category label)."""
    soup = BeautifulSoup(html, "lxml")
    crumbs: list[str] = []
    for sel in ('nav[aria-label*="breadcrumb"] li', ".breadcrumb li", "ol.breadcrumb li"):
        for li in soup.select(sel):
            t = li.get_text(" ", strip=True)
            if t and t not in crumbs:
                crumbs.append(t)
        if crumbs:
            break
    trail = " > ".join(crumbs[:15])
    cat = ""
    if len(crumbs) >= 2:
        cat = crumbs[-2]
    elif crumbs:
        cat = crumbs[-1]
    if not cat:
        cat = category_from_product_url(product_url)
    return trail, cat


def category_from_product_url(url: str) -> str:
    """Derive a coarse category label from URL path segments before `/p/`."""
    p = urlparse(url)
    parts = [x for x in (p.path or "").split("/") if x]
    if len(parts) >= 4 and parts[-2] == "p":
        slug = parts[-3]
        return re.sub(r"[%\-\_\+]", " ", slug)[:160].strip()
    return ""


def heuristic_availability(html: str, soup: BeautifulSoup) -> str:
    blob = (soup.get_text(" ", strip=True) or "").lower()
    if any(x in blob for x in ("rupture de stock", "indisponible", "non disponible")):
        return "unavailable"
    if any(x in blob for x in ("en stock", "disponible", "ajouter au panier")):
        return "in_stock"
    meta = soup.select_one('meta[property="product:availability"]')
    if meta and meta.get("content"):
        return (meta.get("content") or "").strip()
    return "unknown"


def parse_seb_product(html: str, product_url: str, site_root: str) -> dict:
    """
    Return flat fields for JSON assembly (not the final envelope).
    """
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("h1")
    product_name = ""
    if title_el:
        product_name = " ".join(title_el.get_text().split())
    if not product_name:
        product_name = _meta_content(soup, prop="og:title") or ""
    if "|" in product_name:
        product_name = product_name.split("|")[0].strip()

    brand = _meta_content(soup, prop="product:brand") or _meta_content(soup, prop="og:site_name") or "SEB"
    brand = _normalize_brand(brand)
    if brand.lower() in ("seb", "groupe seb"):
        brand = "SEB"

    short_description = _meta_content(soup, name="description") or _meta_content(soup, prop="og:description")
    long_description = _long_description(soup)
    specifications = _collect_specs(soup)

    amount, currency = _extract_price(html)
    if not amount and specifications:
        for k, v in specifications.items():
            if k.strip().lower().startswith("prix"):
                m = _EUR_TEXT.search(v)
                if m:
                    amount = m.group(1).replace(".", ",")
                    break
    reference = _extract_reference(html, product_name)

    blob = soup.get_text(" ", strip=True)
    ean = ""
    m_e = _EAN.search(blob)
    if m_e:
        ean = m_e.group(1)

    media_raw = extract_media_urls(html, site_root)
    media = filter_product_medias(media_raw)

    breadcrumbs, category_guess = extract_breadcrumbs_and_category(html, product_url)
    availability = heuristic_availability(html, soup)

    return {
        "product_name": product_name,
        "brand": brand,
        "reference": reference,
        "ean": ean,
        "short_description": short_description[:2000],
        "long_description": long_description,
        "specifications": specifications,
        "price_amount": amount,
        "currency": currency,
        "media_urls_ordered": media,
        "breadcrumbs": breadcrumbs,
        "category": category_guess,
        "availability": availability,
    }


