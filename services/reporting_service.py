"""Coverage and error reporting for enterprise runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataclasses import asdict

from services.enterprise_models import CoverageStats, ErrorReportEntry
from utils.logger import get_logger

logger = get_logger()


def aggregate_coverage(rows: list[dict[str, Any]], discovered_count: int) -> dict[str, Any]:
    stats = CoverageStats(discovered_urls=discovered_count)
    for r in rows:
        stats.attempted += 1
        st = (r.get("status") or "").upper()
        if st == "OK":
            stats.ok += 1
        elif st == "BLOCKED":
            stats.blocked += 1
        elif st == "FILTERED":
            stats.filtered += 1
        else:
            stats.errors += 1

    d = stats.to_dict()
    if discovered_count:
        d["coverage_ratio_attempted_vs_discovered"] = round(
            stats.attempted / max(discovered_count, 1),
            4,
        )
    if stats.attempted:
        d["success_ratio"] = round(stats.ok / stats.attempted, 4)
    return d


def write_coverage_report(run_path: Path, payload: dict[str, Any]) -> None:
    path = run_path / "coverage_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote coverage_report.json")


def write_error_report(run_path: Path, entries: list[ErrorReportEntry | dict]) -> None:
    serializable = []
    for e in entries:
        if isinstance(e, ErrorReportEntry):
            serializable.append(asdict(e))
        else:
            serializable.append(e)
    path = run_path / "error_report.json"
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote error_report.json (%s events)", len(serializable))


def append_error(
    bucket: list[ErrorReportEntry],
    *,
    site: str,
    product_url: str,
    failure_type: str,
    detail: str,
    status_before: str = "",
) -> None:
    bucket.append(
        ErrorReportEntry(
            timestamp_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            site=site,
            product_url=product_url,
            failure_type=failure_type,
            detail=detail[:2000],
            status_before=status_before,
        ),
    )
