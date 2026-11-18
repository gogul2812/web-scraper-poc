"""
Aggregated metrics for benchmark runs (thread-unsafe; single Playwright thread).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


@dataclass
class BenchmarkMetrics:
    # --- Timestamps (monotonic for phases) ---
    wall_start: float = 0.0
    wall_end: float = 0.0
    discovery_start: float = 0.0
    discovery_end: float = 0.0
    extraction_start: float = 0.0
    extraction_end: float = 0.0

    # --- Discovery duplication (URL hit counts; unique count derived in reports) ---
    total_pdp_discovery_events: int = 0
    # Category enqueue attempts
    category_enqueue_events: int = 0
    duplicate_category_enqueues: int = 0

    # --- HTTP / Playwright ---
    page_load_times_sec: list[float] = field(default_factory=list)
    retry_count: int = 0  # recovery attempts after a failed navigation
    timeout_events: int = 0
    captcha_hits: int = 0
    blocked_pages: int = 0

    # --- Extraction ---
    products_processed: int = 0
    product_extraction_times_sec: list[float] = field(default_factory=list)
    image_download_seconds_total: float = 0.0
    images_downloaded: int = 0
    extraction_failures: int = 0
    non_ok_status: int = 0

    # --- Memory (optional) ---
    memory_samples_mb: list[float] = field(default_factory=list)

    def start_wall_clock(self) -> None:
        self.wall_start = time.perf_counter()

    def end_wall_clock(self) -> None:
        self.wall_end = time.perf_counter()

    def start_discovery(self) -> None:
        self.discovery_start = time.perf_counter()

    def end_discovery(self) -> None:
        self.discovery_end = time.perf_counter()

    def start_extraction(self) -> None:
        self.extraction_start = time.perf_counter()

    def end_extraction(self) -> None:
        self.extraction_end = time.perf_counter()

    def record_page_load(self, seconds: float) -> None:
        if seconds > 0:
            self.page_load_times_sec.append(seconds)

    def sample_memory_mb(self) -> float | None:
        if psutil is None:
            return None
        try:
            m = psutil.Process().memory_info().rss / (1024 * 1024)
            self.memory_samples_mb.append(m)
            return m
        except Exception:
            return None

    def average_page_load_sec(self) -> float:
        if not self.page_load_times_sec:
            return 0.0
        return sum(self.page_load_times_sec) / len(self.page_load_times_sec)

    def average_product_extraction_sec(self) -> float:
        if not self.product_extraction_times_sec:
            return 0.0
        return sum(self.product_extraction_times_sec) / len(self.product_extraction_times_sec)

    def discovery_duration_sec(self) -> float:
        if self.discovery_end <= self.discovery_start:
            return 0.0
        return self.discovery_end - self.discovery_start

    def extraction_phase_sec(self) -> float:
        if self.extraction_end <= self.extraction_start:
            return 0.0
        return self.extraction_end - self.extraction_start

    def total_crawl_sec(self) -> float:
        if self.wall_end <= self.wall_start:
            return 0.0
        return self.wall_end - self.wall_start

    def peak_memory_mb(self) -> float | None:
        if not self.memory_samples_mb:
            return None
        return max(self.memory_samples_mb)
