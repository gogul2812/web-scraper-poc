"""
Structured logging to console and logs/scraper.log.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_file: str = "logs/scraper.log") -> logging.Logger:
    """
    Configure root logging: INFO to console, full detail to file.
    Creates the log directory if missing.
    """
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger("enterprise_ingestion")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    log.addHandler(fh)
    log.addHandler(ch)
    return log


def get_logger(name: str = "enterprise_ingestion") -> logging.Logger:
    return logging.getLogger(name)
