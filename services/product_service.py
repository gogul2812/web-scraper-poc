"""
Persist canonical product artifacts (HTML snapshot + structured JSON).
"""

from __future__ import annotations

from pathlib import Path

from scraper import storage


def persist_product_artifacts(
    product_dir: Path,
    html: str,
    record: storage.MultiSiteProductRecord,
) -> None:
    """Write page.html and data.json under product_dir."""
    storage.save_page_html(product_dir, html)
    storage.save_multisite_json(product_dir, record)
