import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pydub import AudioSegment
from sqlmodel import Session, select

from app.models import FeedJingleMatch

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


def _sliding_window_norm(signal: np.ndarray, window_len: int) -> np.ndarray:
    sq = signal.astype(np.float64) ** 2
    cumsum = np.cumsum(np.insert(sq, 0, 0.0))
    window_sums = cumsum[window_len:] - cumsum[:-window_len]
    window_sums = np.maximum(window_sums, 1e-12)
    return np.sqrt(window_sums)


def normalized_cross_correlation(episode: np.ndarray, jingle: np.ndarray) -> np.ndarray:
    """Normalized cross-correlation of `jingle` against every offset in `episode`.
    Returns an array of scores in roughly [-1, 1] (1.0 = perfect match), indexed by
    the sample offset in `episode` where the jingle window starts."""
    if len(episode) < len(jingle) or len(jingle) == 0:
        return np.array([])

    jingle = jingle - jingle.mean()
    jingle_norm = float(np.linalg.norm(jingle))
    if jingle_norm < 1e-9:
        return np.array([])

    n = len(episode) + len(jingle) - 1
    fft_size = 1 << (n - 1).bit_length()

    episode_fft = np.fft.rfft(episode, fft_size)
    jingle_fft = np.fft.rfft(jingle[::-1], fft_size)
    corr_full = np.fft.irfft(episode_fft * jingle_fft, fft_size)[:n]

    start_idx = len(jingle) - 1
    valid_len = len(episode) - len(jingle) + 1
    corr_valid = corr_full[start_idx : start_idx + valid_len]

    local_norms = _sliding_window_norm(episode, len(jingle))
    return corr_valid / (local_norms * jingle_norm)


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
    ncc = normalized_cross_correlation(episode_samples, template.samples)
    if ncc.size == 0:
        return []
    min_gap_samples = int(template.duration_s * template.sample_rate * 0.5)
    peaks = _find_peaks(ncc, match_threshold, min_gap_samples)
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
    audio: AudioSegment,
    jingles_dir: Path,
    target_sample_rate: int,
    match_threshold: float,
    confident_threshold: float,
) -> list[JingleHit]:
    templates = load_jingle_templates(jingles_dir, target_sample_rate)
    if not templates:
        return []

    episode_seg = audio.set_channels(1).set_frame_rate(target_sample_rate)
    episode_samples = np.array(episode_seg.get_array_of_samples()).astype(np.float32)
    max_val = float(1 << (8 * episode_seg.sample_width - 1))
    episode_samples = episode_samples / max_val

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
