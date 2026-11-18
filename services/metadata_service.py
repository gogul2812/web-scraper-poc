"""Run-level metadata JSON for stakeholder audits."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger()


def write_enhanced_metadata(
    run_path: Path,
    *,
    extraction_run_id: str,
    query: str,
    site_hosts: list[str],
    validation_mode: str,
    max_products_per_site: int | None,
    catalog_discovery_hard_cap: int | None,
    cli_args_summary: str,
    coverage: dict[str, Any],
    started_at_utc: str,
    finished_at_utc: str,
    duration_seconds: float,
) -> None:
    """metadata.json — execution envelope + coverage snapshot."""
    meta = {
        "schema_version": "1.1",
        "extraction_run_id": extraction_run_id,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "duration_seconds": round(duration_seconds, 3),
        "query": query,
        "sites": site_hosts,
        "validation_mode": validation_mode,
        "max_products_per_site": max_products_per_site,
        "catalog_discovery_hard_cap": catalog_discovery_hard_cap,
        "cli": cli_args_summary,
        "coverage": coverage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = run_path / "metadata.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote metadata: %s", path)
