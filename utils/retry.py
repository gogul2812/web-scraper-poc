"""
Retry helpers with exponential backoff (max 3 attempts by default).
"""

from __future__ import annotations

import functools
import random
import time
from typing import Any, Callable, TypeVar

from utils.logger import get_logger

T = TypeVar("T")
logger = get_logger()


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay_sec: float = 1.0,
    max_delay_sec: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator: retry callable up to max_attempts with exponential backoff + jitter.
    Last exception is re-raised if all attempts fail.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            last_exc: BaseException | None = None
            while attempt < max_attempts:
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:  # type: ignore[misc]
                    last_exc = e
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.warning(
                            "%s failed after %s attempts: %s",
                            fn.__name__,
                            max_attempts,
                            e,
                        )
                        break
                    delay = min(base_delay_sec * (2 ** (attempt - 1)), max_delay_sec)
                    jitter = random.uniform(0, delay * 0.2)
                    sleep_for = delay + jitter
                    logger.debug(
                        "Retry %s/%s for %s in %.2fs (error: %s)",
                        attempt,
                        max_attempts,
                        fn.__name__,
                        sleep_for,
                        e,
                    )
                    time.sleep(sleep_for)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
