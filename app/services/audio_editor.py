import logging
from pathlib import Path

from pydub import AudioSegment

log = logging.getLogger(__name__)


def compute_keep_ranges(total_duration_ms: int, cut_ranges_ms: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not cut_ranges_ms:
        return [(0, total_duration_ms)]

    clamped = sorted(
        (max(0, min(s, total_duration_ms)), max(0, min(e, total_duration_ms)))
        for s, e in cut_ranges_ms
        if e > s
    )
    merged: list[list[int]] = []
    for start, end in clamped:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    keep: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total_duration_ms:
        keep.append((cursor, total_duration_ms))
    return keep


def get_duration_seconds(path: Path) -> float | None:
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio and audio.info and getattr(audio.info, "length", None):
            return float(audio.info.length)
    except Exception:
        log.warning("Could not read duration via mutagen for %s, falling back to pydub", path, exc_info=True)
    try:
        return len(AudioSegment.from_file(path)) / 1000.0
    except Exception:
        log.warning("Could not determine duration for %s", path, exc_info=True)
        return None


def detect_source_bitrate(path: Path) -> str:
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio and audio.info and getattr(audio.info, "bitrate", None):
            return f"{audio.info.bitrate // 1000}k"
    except Exception:
        log.warning("Could not detect bitrate for %s, falling back to 128k", path, exc_info=True)
    return "128k"


def cut_and_export(
    source_path: Path,
    dest_path: Path,
    cut_ranges_ms: list[tuple[int, int]],
    crossfade_ms: int = 50,
    bitrate: str | None = None,
) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    audio = AudioSegment.from_file(source_path)
    export_bitrate = bitrate or detect_source_bitrate(source_path)

    if not cut_ranges_ms:
        audio.export(dest_path, format="mp3", bitrate=export_bitrate)
        return dest_path

    keep_ranges = compute_keep_ranges(len(audio), cut_ranges_ms)
    if not keep_ranges:
        raise ValueError("Cut ranges remove the entire episode; nothing left to export")

    start, end = keep_ranges[0]
    result = audio[start:end]
    for start, end in keep_ranges[1:]:
        clip = audio[start:end]
        cf = min(crossfade_ms, len(result) // 2, len(clip) // 2)
        result = result.append(clip, crossfade=max(cf, 0))

    result.export(dest_path, format="mp3", bitrate=export_bitrate)
    log.info("Exported cut audio to %s (%d keep ranges)", dest_path, len(keep_ranges))
    return dest_path
