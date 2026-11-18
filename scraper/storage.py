"""
Persist HTML snapshots, multi-site JSON payloads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger()


@dataclass
class MultiSiteProductRecord:
    """Per-product JSON artifact (matches required schema incl. status)."""

    site: str
    query: str
    product_name: str
    product_url: str
    brand: str
    ean: str
    price: str
    description: str
    sellers: list[dict[str, str]]
    images: list[dict[str, str]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_page_html(product_dir: Path, html: str) -> None:
    path = product_dir / "page.html"
    path.write_text(html, encoding="utf-8", errors="replace")
    logger.debug("Saved HTML: %s", path)


def save_multisite_json(product_dir: Path, record: MultiSiteProductRecord) -> None:
    path = product_dir / "data.json"
    path.write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.debug("Saved JSON: %s", path)


def ensure_output_root(output_dir: str) -> Path:
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p
