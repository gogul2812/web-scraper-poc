"""
Read full page HTML after navigation settles.

Playwright raises if `page.content()` is called while a navigation is in flight
(e.g. Amazon redirects or client-side routing). We wait for `load` and retry
with backoff on that error.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page

from utils.logger import get_logger

logger = get_logger()

_NAV_RACE_PHRASES = ("navigating", "navigation", "changing the content")


def get_stable_html(
    page: Page,
    navigation_timeout_ms: int = 60_000,
    max_attempts: int = 8,
) -> str:
    """
    Return `page.content()` after the document is in a stable state.
    Retries when Playwright reports a navigation race.
    """
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            page.wait_for_load_state(
                "load",
                timeout=min(navigation_timeout_ms, 45_000),
            )
        except Exception:
            pass
        # Brief pause so late redirects / SPA transitions can finish
        page.wait_for_timeout(300 + attempt * 75)
        try:
            return page.content()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if any(p in msg for p in _NAV_RACE_PHRASES) and attempt < max_attempts - 1:
                logger.debug(
                    "page.content() raced navigation (attempt %s/%s)",
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(0.35 + attempt * 0.15)
                continue
            raise
    assert last_err is not None
    raise last_err
