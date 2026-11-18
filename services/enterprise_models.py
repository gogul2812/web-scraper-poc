"""Typed payloads for enterprise reporting (dataclasses — Pydantic-ready swap later)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunIdentifiers:
    """One ingestion execution."""

    extraction_run_id: str
    """Logical run UUID or slug."""
    run_folder_name: str
    """Directory name e.g. run_20260506_120000."""
    validation_mode: str = "fallback"


@dataclass
class ErrorReportEntry:
    """Single failure suitable for error_report.json."""

    timestamp_utc: str
    site: str
    product_url: str
    failure_type: str
    detail: str
    status_before: str = ""


@dataclass
class CoverageStats:
    """Roll-up counts after a run."""

    discovered_urls: int = 0
    attempted: int = 0
    ok: int = 0
    blocked: int = 0
    errors: int = 0
    filtered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovered_urls": self.discovered_urls,
            "attempted": self.attempted,
            "ok": self.ok,
            "blocked": self.blocked,
            "errors": self.errors,
            "filtered": self.filtered,
        }
