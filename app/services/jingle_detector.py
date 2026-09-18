import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlmodel import Session, select

from app.models import FeedJingleMatch
from app.services.audio_io import load_mono_samples

log = logging.getLogger(__name__)

_template_cache: dict[str, tuple[float, "JingleTemplate"]] = {}  # path -> (mtime, template)


@dataclass
class JingleTemplate:
    filename: str
    role: str  # "start" | "end" | "marker"
    samples: np.ndarray
    duration_s: float
    sample_rate: int


@dataclass
class JingleHit:
    jingle_filename: str
    role: str
    time_s: float
    score: float


def _infer_role(stem: str) -> str:
    name = stem.lower()
    if "start" in name or "beginn" in name:
        return "start"
    if "end" in name or "ende" in name:
        return "end"
    return "marker"


def _load_mono_samples(path: Path, target_sr: int) -> np.ndarray:
    seg = AudioSegment.from_file(path).set_channels(1).set_frame_rate(target_sr)
    samples = np.array(seg.get_array_of_samples()).astype(np.float32)
    max_val = float(1 << (8 * seg.sample_width - 1))
    return samples / max_val


def load_jingle_templates(jingles_dir: Path, target_sr: int) -> list[JingleTemplate]:
    templates: list[JingleTemplate] = []
    if not jingles_dir.exists():
        return templates

    for path in sorted(jingles_dir.glob("*.mp3")):
        try:
            mtime = path.stat().st_mtime
            cache_key = str(path)
            cached = _template_cache.get(cache_key)
            if cached and cached[0] == mtime and cached[1].sample_rate == target_sr:
                templates.append(cached[1])
                continue

            samples = _load_mono_samples(path, target_sr)
            template = JingleTemplate(
                filename=path.name,
                role=_infer_role(path.stem),
                samples=samples,
                duration_s=len(samples) / target_sr,
                sample_rate=target_sr,
            )
            _template_cache[cache_key] = (mtime, template)
            templates.append(template)
        except Exception:
            log.warning("Could not load jingle template %s", path, exc_info=True)
    return templates


_NCC_CHUNK_SAMPLES = 1_000_000


def _sliding_window_norm(signal: np.ndarray, window_len: int) -> np.ndarray:
    """Return RMS denominators in bounded chunks.

    The previous implementation created a float64 copy of the entire episode and a
    float64 cumulative-sum array of the same size. For a multi-hour episode this can
    consume multiple gigabytes temporarily.
    """
    if window_len <= 0 or len(signal) < window_len:
        return np.array([], dtype=np.float64)

    valid_len = len(signal) - window_len + 1
    result = np.empty(valid_len, dtype=np.float64)
    for start in range(0, valid_len, _NCC_CHUNK_SAMPLES):
        count = min(_NCC_CHUNK_SAMPLES, valid_len - start)
        segment = signal[start : start + count + window_len - 1].astype(np.float64, copy=False)
        sq = segment * segment
        cumsum = np.empty(len(sq) + 1, dtype=np.float64)
        cumsum[0] = 0.0
        np.cumsum(sq, out=cumsum[1:])
        result[start : start + count] = np.sqrt(
            np.maximum(cumsum[window_len : window_len + count] - cumsum[:count], 1e-12)
        )
    return result


def _iter_normalized_cross_correlation(episode: np.ndarray, jingle: np.ndarray):
    """Yield NCC chunks, keeping FFT and normalization temporaries bounded."""
    if len(episode) < len(jingle) or len(jingle) == 0:
        return
    jingle = jingle.astype(np.float32, copy=False)
    jingle = jingle - jingle.mean()
    jingle_norm = float(np.linalg.norm(jingle))
    if jingle_norm < 1e-9:
        return

    valid_len = len(episode) - len(jingle) + 1
    reversed_jingle = jingle[::-1]
    for start in range(0, valid_len, _NCC_CHUNK_SAMPLES):
        count = min(_NCC_CHUNK_SAMPLES, valid_len - start)
        segment = episode[start : start + count + len(jingle)].astype(np.float32, copy=False)
        n = len(segment) + len(jingle) - 1
        fft_size = 1 << (n - 1).bit_length()
        episode_fft = np.fft.rfft(segment, fft_size)
        jingle_fft = np.fft.rfft(reversed_jingle, fft_size)
        corr_full = np.fft.irfft(episode_fft * jingle_fft, fft_size)
        corr = corr_full[len(jingle) - 1 : len(jingle) - 1 + count]

        seg64 = segment.astype(np.float64, copy=False)
        sq = seg64 * seg64
        cumsum = np.empty(len(sq) + 1, dtype=np.float64)
        cumsum[0] = 0.0
        np.cumsum(sq, out=cumsum[1:])
        norms = np.sqrt(np.maximum(cumsum[len(jingle) : len(jingle) + count] - cumsum[:count], 1e-12))
        yield corr.astype(np.float32, copy=False) / (norms * jingle_norm)


def normalized_cross_correlation(episode: np.ndarray, jingle: np.ndarray) -> np.ndarray:
    """Normalized cross-correlation with bounded working memory.

    API-compatible array result; production matching uses the streaming helper below
    so it does not need to retain this full array.
    """
    chunks = list(_iter_normalized_cross_correlation(episode, jingle))
    if not chunks:
        return np.array([])
    return np.concatenate(chunks)


def _find_peaks_chunks(chunks, threshold: float, min_gap_samples: int):
    """Find peaks from NCC chunks without retaining the full NCC array."""
    peaks: list[tuple[int, float]] = []
    cluster_best_idx: int | None = None
    cluster_best_score = 0.0
    prev_idx: int | None = None
    offset = 0
    for chunk in chunks:
        idx = np.where(chunk >= threshold)[0]
        for local_idx in idx:
            absolute_idx = offset + int(local_idx)
            score = float(chunk[local_idx])
            if prev_idx is None or absolute_idx - prev_idx > max(min_gap_samples, 1):
                if cluster_best_idx is not None:
                    peaks.append((cluster_best_idx, cluster_best_score))
                cluster_best_idx = absolute_idx
                cluster_best_score = score
            elif score > cluster_best_score:
                cluster_best_idx = absolute_idx
                cluster_best_score = score
            prev_idx = absolute_idx
        offset += len(chunk)
    if cluster_best_idx is not None:
        peaks.append((cluster_best_idx, cluster_best_score))
    return peaks


def _find_peaks(ncc: np.ndarray, threshold: float, min_gap_samples: int) -> list[tuple[int, float]]:
    idx = np.where(ncc >= threshold)[0]
    if len(idx) == 0:
        return []

    peaks: list[tuple[int, float]] = []
    cluster_start = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i - prev > max(min_gap_samples, 1):
            cluster = np.arange(cluster_start, prev + 1)
            best = cluster[np.argmax(ncc[cluster])]
            peaks.append((int(best), float(ncc[best])))
            cluster_start = i
        prev = i
    cluster = np.arange(cluster_start, prev + 1)
    best = cluster[np.argmax(ncc[cluster])]
    peaks.append((int(best), float(ncc[best])))
    return peaks


def match_template(episode_samples: np.ndarray, template: JingleTemplate, match_threshold: float) -> list[JingleHit]:
    min_gap_samples = int(template.duration_s * template.sample_rate * 0.5)
    peaks = _find_peaks_chunks(
        _iter_normalized_cross_correlation(episode_samples, template.samples),
        match_threshold,
        min_gap_samples,
    )
    return [
        JingleHit(
            jingle_filename=template.filename,
            role=template.role,
            time_s=idx / template.sample_rate,
            score=float(min(1.0, max(0.0, score))),
        )
        for idx, score in peaks
    ]


def order_templates_for_feed(
    session: Session, feed_id: int, templates: list[JingleTemplate]
) -> tuple[list[JingleTemplate], list[JingleTemplate]]:
    """Returns (known, unknown): jingles previously matched for this feed (ordered by
    match count, most frequent first) and jingles never matched for this feed."""
    stats = session.exec(select(FeedJingleMatch).where(FeedJingleMatch.feed_id == feed_id)).all()
    counts = {s.jingle_filename: s.match_count for s in stats}
    known = sorted((t for t in templates if t.filename in counts), key=lambda t: counts[t.filename], reverse=True)
    unknown = [t for t in templates if t.filename not in counts]
    return known, unknown


def find_jingle_hits(
    session: Session,
    feed_id: int,
    audio_path: Path,
    jingles_dir: Path,
    target_sample_rate: int,
    match_threshold: float,
    confident_threshold: float,
) -> list[JingleHit]:
    templates = load_jingle_templates(jingles_dir, target_sample_rate)
    if not templates:
        return []

    # Decode directly to the small matching sample rate. Do not retain the large
    # 16-kHz pydub AudioSegment used later for silence/RMS scoring.
    episode_samples = load_mono_samples(audio_path, target_sample_rate)

    known, unknown = order_templates_for_feed(session, feed_id, templates)

    hits: list[JingleHit] = []
    found_confident_known = False
    for template in known:
        template_hits = match_template(episode_samples, template, match_threshold)
        hits.extend(template_hits)
        if any(h.score >= confident_threshold for h in template_hits):
            found_confident_known = True

    if not found_confident_known:
        for template in unknown:
            hits.extend(match_template(episode_samples, template, match_threshold))

    return hits

def record_jingle_match(session: Session, feed_id: int, jingle_filename: str) -> None:
    row = session.exec(
        select(FeedJingleMatch)
        .where(FeedJingleMatch.feed_id == feed_id)
        .where(FeedJingleMatch.jingle_filename == jingle_filename)
    ).first()
    now = datetime.now(timezone.utc)
    if row:
        row.match_count += 1
        row.last_matched_at = now
    else:
        row = FeedJingleMatch(feed_id=feed_id, jingle_filename=jingle_filename, match_count=1, last_matched_at=now)
    session.add(row)
