"""Persist extraction artefacts (HTML bundles, path bookkeeping)."""

from __future__ import annotations

from pathlib import Path

from config import ScraperConfig
from utils.html_cleaner import clean_rendered_html
from utils.logger import get_logger

logger = get_logger()


def save_product_html_bundle(
    product_dir: Path,
    raw_html: str,
    cfg: ScraperConfig,
) -> tuple[str, str | None]:
    """
    Write raw.html and optionally clean.html.
    Returns (raw_path_str, clean_path_str_or_None).
    """
    raw_path = product_dir / "raw.html"
    raw_path.write_text(raw_html, encoding="utf-8", errors="replace")

    clean_str: str | None = None
    if cfg.save_clean_html:
        clean_str = clean_rendered_html(raw_html)
        clean_path = product_dir / "clean.html"
        clean_path.write_text(clean_str, encoding="utf-8", errors="replace")
        logger.debug("Saved clean HTML: %s", clean_path)
        return str(raw_path.resolve()), str(clean_path.resolve())

    return str(raw_path.resolve()), None


def paths_for_json(product_dir: Path) -> str:
    p = product_dir / "data.json"
    return str(p.resolve()) if p.exists() else ""
