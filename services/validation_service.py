"""
Product validation: pluggable EAN dataset (future) + POC fallback rules.

To enable dataset mode later, drop a file path into config (`ean_dataset_path`)
and call `load_ean_dataset_from_file(path)` before scraping — no scraper code changes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.logger import get_logger

logger = get_logger()

# Populated by load_ean_dataset_from_file(); empty = dataset mode inactive.
VALID_EANS: set[str] = set()

# SEB-group / kitchen brands (expand via config file later if needed)
SEB_BRANDS: frozenset[str] = frozenset(
    {
        "tefal",
        "rowenta",
        "moulinex",
        "krups",
        "wmf",
        "lagostina",
        "emeril",
        "all-clad",
        "supor",
        "calphalon",
    },
)


def normalize_ean(raw: str | None) -> str:
    """Keep digits only for barcode comparison."""
    if not raw:
        return ""
    return re.sub(r"\D", "", raw)


def load_ean_dataset_from_file(path: str | Path | None) -> int:
    """
    Load allowed EAN/GTIN codes from:
      - JSON list: ["123...", ...]
      - JSON object: {"eans": [...]} or {"gtins": [...]}
      - Plain text: one code per line
      - CSV: first column (header line skipped if non-numeric)

    Returns number of codes loaded. Missing path is a no-op (POC fallback only).
    """
    global VALID_EANS
    if not path:
        return 0
    p = Path(path)
    if not p.is_file():
        logger.info("EAN dataset file not found (fallback validation only): %s", p)
        return 0

    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    loaded: set[str] = set()

    if p.suffix.lower() == ".json":
        data = json.loads(raw)
        if isinstance(data, list):
            for x in data:
                loaded.add(normalize_ean(str(x)))
        elif isinstance(data, dict):
            for key in ("eans", "gtins", "codes"):
                if key in data and isinstance(data[key], list):
                    for x in data[key]:
                        loaded.add(normalize_ean(str(x)))
        loaded.discard("")
        VALID_EANS |= loaded
        logger.info("Loaded %s EAN(s) from JSON dataset: %s", len(loaded), p)
        return len(loaded)

    if p.suffix.lower() == ".csv":
        for i, line in enumerate(raw.splitlines()):
            cell = line.split(",")[0].strip().strip('"')
            if i == 0 and not normalize_ean(cell):
                continue  # skip likely header row
            if normalize_ean(cell):
                loaded.add(normalize_ean(cell))
        loaded.discard("")
        VALID_EANS |= loaded
        logger.info("Loaded %s EAN(s) from CSV dataset: %s", len(loaded), p)
        return len(loaded)

    # Plain text
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        loaded.add(normalize_ean(line.split()[0]))
    loaded.discard("")
    VALID_EANS |= loaded
    logger.info("Loaded %s EAN(s) from text dataset: %s", len(loaded), p)
    return len(loaded)


_STOPWORDS = frozenset({"the", "a", "an", "for", "and", "or", "of", "with", "by", "to"})


def _normalize_hyphen_brands(text: str) -> str:
    """
    Amazon uses stylised spellings (e.g. T-Fal) that don't substring-match 'tefal'.
    Fold common variants before token / brand checks.
    """
    t = (text or "").lower()
    for old, new in (
        ("t-fal", "tefal"),
        ("t fal", "tefal"),
        ("t_fal", "tefal"),
    ):
        t = t.replace(old, new)
    return t


def _title_matches_query(name: str, query: str) -> bool:
    """
    Match search intent without requiring the exact phrase order.
    Example: query "Tefal pan" passes if title contains both "tefal" and "pan".
    """
    q = (query or "").strip().lower()
    n = _normalize_hyphen_brands(name or "")
    if not q:
        return True
    if q in n:
        return True
    tokens = [t for t in re.split(r"\s+", q) if t]
    significant = [t for t in tokens if t not in _STOPWORDS and len(t) >= 2]
    if not significant:
        significant = tokens
    significant = [_normalize_hyphen_brands(t) for t in significant]
    return all(t in n for t in significant)


def is_valid_product(ean: str, brand: str, name: str, query: str) -> bool:
    """
    POC rules:
      1) EAN in loaded dataset (when dataset is configured and loaded)
      2) Brand substring matches a known brand token (kitchen / SEB-style list)
      3) Title matches query (full phrase OR all significant words present — see above)
    """
    ean_n = normalize_ean(ean)
    if ean_n and ean_n in VALID_EANS:
        return True

    if brand:
        bl = _normalize_hyphen_brands(brand)
        for token in SEB_BRANDS:
            if token in bl:
                return True

    # Title often includes brand when #bylineInfo is missing on mobile / variants
    if name:
        nl = _normalize_hyphen_brands(name)
        for token in SEB_BRANDS:
            if token in nl:
                return True

    if _title_matches_query(name, query):
        return True

    return False
