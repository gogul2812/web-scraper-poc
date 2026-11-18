"""
Timestamped run folders and safe paths for multi-site output (no overwrites).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger()

_SAFE_DIR = re.compile(r"[^a-zA-Z0-9._\-]+")


def create_benchmark_run_folder(output_root: str | Path = "output") -> Path:
    """Create output/benchmark_run_YYYYMMDD_HHMMSS/ for load-test artifacts."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("benchmark_run_%Y%m%d_%H%M%S")
    run_path = root / base
    suffix = 0
    while run_path.exists():
        suffix += 1
        run_path = root / f"{base}_{suffix}"
    run_path.mkdir(parents=False, exist_ok=False)
    (run_path / "logs").mkdir(parents=True, exist_ok=True)
    (run_path / "excel").mkdir(parents=True, exist_ok=True)
    logger.info("Created benchmark run folder: %s", run_path)
    return run_path


def create_run_folder(output_root: str | Path = "output") -> Path:
    """Create output/run_YYYYMMDD_HHMMSS/ (add _1, _2, … if the name already exists)."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_path = root / base
    suffix = 0
    while run_path.exists():
        suffix += 1
        run_path = root / f"{base}_{suffix}"
    run_path.mkdir(parents=False, exist_ok=False)
    logger.info("Created run folder: %s", run_path)
    return run_path


def hostname_to_site_folder(url_or_host: str) -> str:
    """
    Map URL or host to a filesystem-safe folder name (e.g. www.amazon.in -> amazon.in).
    """
    from urllib.parse import urlparse

    s = url_or_host.strip()
    if "://" in s:
        host = urlparse(s).netloc or s
    else:
        host = s.split("/")[0]
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    host = _SAFE_DIR.sub("_", host)
    return host or "unknown_site"


def create_site_folder(run_path: Path, site_label: str) -> Path:
    """Create run_path/<site_label>/ (e.g. amazon.in)."""
    safe = _SAFE_DIR.sub("_", site_label.strip()) or "site"
    p = run_path / safe
    p.mkdir(parents=True, exist_ok=True)
    logger.debug("Site folder: %s", p)
    return p


def create_product_folder(site_path: Path, product_id: str, *, create_images_subdir: bool = False) -> Path:
    """Create site_path/product_XXX/; optional images/ subfolder when saving product images."""
    root = site_path / product_id
    root.mkdir(parents=True, exist_ok=True)
    if create_images_subdir:
        (root / "images").mkdir(parents=True, exist_ok=True)
    return root


def ensure_run_logs_dir(run_path: Path) -> Path:
    """Run-specific logs directory: output/run_.../logs/"""
    p = run_path / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_run_metadata(
    run_path: Path,
    query: str,
    site_hosts: list[str],
    validation_mode: str = "fallback",
    max_products_per_site: int | None = None,
) -> None:
    """Write metadata.json with ISO8601 UTC timestamp."""
    meta = {
        "query": query,
        "sites": site_hosts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "validation_mode": validation_mode,
        "max_products_per_site": max_products_per_site,
    }
    path = run_path / "metadata.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote metadata: %s", path)
