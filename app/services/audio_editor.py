import logging
import subprocess
from pathlib import Path


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


def _probe_duration_seconds(path: Path) -> float | None:
    """Get duration without decoding the whole audio file into RAM."""
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio and audio.info and getattr(audio.info, "length", None):
            return float(audio.info.length)
    except Exception:
        log.warning("Could not read duration via mutagen for %s, falling back to ffprobe", path, exc_info=True)

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        log.warning("Could not determine duration for %s", path, exc_info=True)
        return None


def get_duration_seconds(path: Path) -> float | None:
    # Kept as a public helper for callers that only need metadata. Never fall back to
    # AudioSegment.from_file(), because that would decode the complete episode into
    # uncompressed PCM and can consume gigabytes for long podcasts.
    return _probe_duration_seconds(path)


def detect_source_bitrate(path: Path) -> str:
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio and audio.info and getattr(audio.info, "bitrate", None):
            return f"{audio.info.bitrate // 1000}k"
    except Exception:
        log.warning("Could not detect bitrate for %s, falling back to 128k", path, exc_info=True)
    return "128k"


def _run_ffmpeg_filter_export(
    source_path: Path,
    dest_path: Path,
    keep_ranges: list[tuple[int, int]],
    bitrate: str,
    crossfade_ms: int,
) -> None:
    """Cut/recombine audio in ffmpeg without materialising the complete PCM stream in Python."""
    if not keep_ranges:
        raise ValueError("Cut ranges remove the entire episode; nothing left to export")

    # Split the input into independent filter branches, trim each branch, and then
    # concatenate them with the same 50-ms-style crossfade used by the old pydub path.
    n = len(keep_ranges)
    labels = "".join(f"[a{i}]" for i in range(n))
    filters = [f"[0:a]asplit={n}{labels}"]

    for i, (start_ms, end_ms) in enumerate(keep_ranges):
        start_s = start_ms / 1000.0
        end_s = end_ms / 1000.0
        filters.append(
            f"[a{i}]atrim=start={start_s:.3f}:end={end_s:.3f},asetpts=PTS-STARTPTS[s{i}]"
        )

    current = "s0"
    accumulated_ms = keep_ranges[0][1] - keep_ranges[0][0]
    for i in range(1, n):
        clip_ms = keep_ranges[i][1] - keep_ranges[i][0]
        cf = min(crossfade_ms, accumulated_ms // 2, clip_ms // 2)
        if cf > 0:
            out = f"x{i}"
            filters.append(
                f"[{current}][s{i}]acrossfade=d={cf / 1000.0:.3f}:c1=tri:c2=tri[{out}]"
            )
            current = out
            accumulated_ms += clip_ms - cf
        else:
            out = f"x{i}"
            filters.append(f"[{current}][s{i}]concat=n=2:v=0:a=1[{out}]")
            current = out
            accumulated_ms += clip_ms

    filters.append(f"[{current}]anull[outa]")
    filter_complex = ";".join(filters)

    tmp_path = dest_path.with_suffix(".part.mp3")
    tmp_path.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[outa]",
                "-c:a",
                "libmp3lame",
                "-b:a",
                bitrate,
                str(tmp_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        tmp_path.replace(dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _run_ffmpeg_transcode(source_path: Path, dest_path: Path, bitrate: str) -> None:
    """Transcode without loading the complete source into a Python AudioSegment."""
    tmp_path = dest_path.with_suffix(".part.mp3")
    tmp_path.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-map",
                "0:a:0",
                "-c:a",
                "libmp3lame",
                "-b:a",
                bitrate,
                str(tmp_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        tmp_path.replace(dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def cut_and_export(
    source_path: Path,
    dest_path: Path,
    cut_ranges_ms: list[tuple[int, int]],
    crossfade_ms: int = 50,
    bitrate: str | None = None,
) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    export_bitrate = bitrate or detect_source_bitrate(source_path)

    duration_s = get_duration_seconds(source_path)
    if duration_s is None:
        raise RuntimeError(f"Could not determine duration for {source_path}")
    total_duration_ms = max(1, int(round(duration_s * 1000)))

    if not cut_ranges_ms:
        _run_ffmpeg_transcode(source_path, dest_path, export_bitrate)
        log.info("Exported audio to %s without cuts", dest_path)
        return dest_path

    keep_ranges = compute_keep_ranges(total_duration_ms, cut_ranges_ms)
    _run_ffmpeg_filter_export(source_path, dest_path, keep_ranges, export_bitrate, crossfade_ms)
    log.info("Exported cut audio to %s (%d keep ranges)", dest_path, len(keep_ranges))
    return dest_path
