import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(60.0, read=300.0)


def _replace_with_retry(tmp_path: Path, dest_path: Path, attempts: int = 6, delay_s: float = 0.5) -> None:
    """On Windows, os.replace() can raise PermissionError if the destination is
    momentarily open elsewhere (antivirus/indexer scan, or - if the same episode was
    ever queued for download twice at once, e.g. the background scheduler and a manual
    Jingle-Finder download racing each other - another download finishing at the same
    time). The lock is normally released within milliseconds, so a short retry loop
    resolves it without surfacing a 500 to the user."""
    for attempt in range(1, attempts + 1):
        try:
            tmp_path.replace(dest_path)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            log.warning(
                "Could not replace %s (attempt %d/%d) - target is locked, retrying shortly",
                dest_path,
                attempt,
                attempts,
            )
            time.sleep(delay_s)


def download_file(url: str, dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=65536):
                f.write(chunk)
    _replace_with_retry(tmp_path, dest_path)
    log.info("Downloaded %s -> %s", url, dest_path)
    return dest_path


def download_image(url: str, dest_path: Path) -> Path | None:
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, follow_redirects=True, timeout=TIMEOUT) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    f.write(chunk)
        return dest_path
    except Exception:
        log.warning("Failed to download image %s", url, exc_info=True)
        return None
