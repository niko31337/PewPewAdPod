from pathlib import Path

from app.config import settings


def cross_platform_basename(path: str | None) -> str:
    """Extract the filename from a stored path, regardless of whether it was written
    by a Windows or Linux process (e.g. a DB row created by a local Windows run, now
    read inside the Linux Docker container - plain pathlib.Path.name doesn't split on
    backslashes there since PosixPath only treats "/" as a separator)."""
    if not path:
        return ""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def resolve_cover_path(path_str: str | None, canonical: Path) -> Path | None:
    """Resolve a DB-stored cover-image path to a Path that actually exists on this
    machine right now. Same cross-OS problem as resolve_stored_path() (a path written
    by a process on a different OS - e.g. a pre-Docker Windows run - doesn't parse
    correctly as-is here), but cover images always live at a single deterministic
    location by convention (covers_dir/{feed_id}/cover.jpg or .../episodes/{episode_id}.jpg),
    so that canonical path is the fallback rather than a directory search."""
    if path_str:
        candidate = Path(path_str)
        if candidate.exists():
            return candidate
    if canonical.exists():
        return canonical
    return None


def resolve_stored_path(path_str: str | None) -> Path | None:
    """Resolve a DB-stored absolute audio path to a Path that actually exists on this
    machine right now. Paths written by a process on a different OS (e.g. a Windows
    dev run, now read inside the Linux Docker container) don't parse correctly as-is,
    so this falls back to looking the filename up in the known audio directories -
    every stored path is, by convention, "{originals,processed}_dir / {episode_id}.mp3"
    (or "..._correction.mp3"), so the filename alone is enough to relocate it.
    Returns None if the file can't be found under any of those names."""
    if not path_str:
        return None
    candidate = Path(path_str)
    if candidate.exists():
        return candidate
    name = cross_platform_basename(path_str)
    if not name:
        return None
    for directory in (settings.originals_dir, settings.processed_dir):
        alt = directory / name
        if alt.exists():
            return alt
    return None
