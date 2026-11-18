"""Download product images from www.seb.fr — enterprise filenames by gallery role."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from config import ScraperConfig
from utils.image_hash import sha256_bytes
from utils.logger import get_logger
from utils.retry import retry_with_backoff

logger = get_logger()


def _guess_extension(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"


@retry_with_backoff(max_attempts=3, base_delay_sec=0.9)
def _download_one(url: str, dest: Path, timeout: float) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.seb.fr/",
    }
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return r.content


def _normalize_image_url(url: str) -> str:
    """
    Canonicalize URL for dedupe:
    - sort query params
    - drop empty fragment
    """
    s = urlsplit(url)
    q = urlencode(sorted(parse_qsl(s.query, keep_blank_values=True)))
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path, q, ""))


def download_seb_gallery(
    image_entries: list[dict[str, str]],
    images_dir: Path,
    cfg: ScraperConfig,
) -> tuple[list[dict[str, str | int]], int]:
    """
    image_entries: [{"url", "role"}, …]
    Writes main_01.jpg, secondary_01.jpg, … under images/.
    Returns (records for JSON, failed_download_count).
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    timeout = cfg.image_download_timeout_sec
    mapping: list[dict[str, str | int]] = []
    failed = 0
    role_counters: dict[str, int] = {}
    seen_url: set[str] = set()
    seen_hash: set[str] = set()

    for entry in image_entries:
        url = entry.get("url") or ""
        role = (entry.get("role") or "detail").lower().replace(" ", "_")
        if role not in ("main", "secondary", "detail", "lifestyle"):
            role = "detail"
        if not url.startswith("http"):
            continue
        canon = _normalize_image_url(url)
        if canon in seen_url:
            continue
        seen_url.add(canon)

        role_counters[role] = role_counters.get(role, 0) + 1
        idx = role_counters[role]
        ext = _guess_extension(url)
        fname = f"{role}_{idx:02d}{ext}"
        dest = images_dir / fname
        try:
            blob = _download_one(url, dest, timeout)
            digest = sha256_bytes(blob)
            if digest in seen_hash:
                # Duplicate binary under another URL; remove duplicate file.
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                role_counters[role] -= 1
                continue
            seen_hash.add(digest)
            mapping.append(
                {
                    "url": url,
                    "local_path": str(dest.resolve().as_posix()),
                    "image_role": role,
                    "image_index": idx,
                    "image_hash": digest,
                },
            )
        except Exception as e:
            failed += 1
            logger.warning("SEB image download failed (%s): %s", url[:100], e)

    return mapping, failed
