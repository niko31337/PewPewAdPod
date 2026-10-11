import heapq
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.services.audio_io import load_mono_samples as _load_mono_samples
from app.services.jingle_detector import normalized_cross_correlation

log = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    start_a_s: float
    end_a_s: float
    start_b_s: float
    end_b_s: float
    support: int
    correlation: float = 0.0  # waveform-verified similarity in [0, 1]; see _verify_candidate


_SPECTROGRAM_CHUNK_FRAMES = 5000
_FINGERPRINT_PAIRINGS_PER_HASH = 50_000
_CANDIDATE_POOL_MULTIPLIER = 20


def _spectrogram_peaks(
    samples: np.ndarray, frame_size: int, hop_size: int, num_peaks: int, min_freq_bin: int = 2
) -> list[list[int]]:
    """Processes windowed STFT frames in bounded-size chunks rather than all at once:
    "as_strided(...) * window" over the whole signal allocates frame_count * frame_size
    * 4 bytes in one go - for a several-hours-long episode that's close to a gigabyte,
    per file compared. Chunking bounds peak memory regardless of episode length, with
    identical output (each frame's peaks only depend on that frame)."""
    n_frames = 1 + (len(samples) - frame_size) // hop_size
    if n_frames <= 0:
        return []

    window = np.hanning(frame_size).astype(np.float32)

    peaks_per_frame: list[list[int]] = []
    for chunk_start in range(0, n_frames, _SPECTROGRAM_CHUNK_FRAMES):
        chunk_end = min(n_frames, chunk_start + _SPECTROGRAM_CHUNK_FRAMES)
        count = chunk_end - chunk_start
        chunk_samples = samples[chunk_start * hop_size :]
        shape = (count, frame_size)
        strides = (chunk_samples.strides[0] * hop_size, chunk_samples.strides[0])
        frames = np.lib.stride_tricks.as_strided(chunk_samples, shape=shape, strides=strides) * window

        magnitude = np.abs(np.fft.rfft(frames, axis=1))
        magnitude[:, :min_freq_bin] = 0.0

        for i in range(count):
            mag = magnitude[i]
            peak_mag = mag.max()
            if peak_mag < 1e-6:
                peaks_per_frame.append([])
                continue
            top_idx = np.argpartition(mag, -num_peaks)[-num_peaks:]
            threshold = mag.mean() * 3
            peaks_per_frame.append(sorted(int(idx) for idx in top_idx if mag[idx] > threshold))
    return peaks_per_frame


def _build_fingerprints(
    peaks_per_frame: list[list[int]], fan_out: int, target_frames: int
) -> dict[int, list[int]]:
    """Shazam-style constellation hashing: pair each spectral peak with a handful of
    peaks in nearby subsequent frames, hash (freq1, freq2, frame_offset). A hash that
    occurs in two different recordings at a consistent time delta is strong evidence
    those recordings share the same audio at that point (e.g. a jingle)."""
    fingerprints: dict[int, list[int]] = defaultdict(list)
    n = len(peaks_per_frame)
    for anchor_frame, freqs in enumerate(peaks_per_frame):
        for f1 in freqs:
            paired = 0
            for offset in range(1, target_frames + 1):
                target_frame = anchor_frame + offset
                if target_frame >= n:
                    break
                for f2 in peaks_per_frame[target_frame]:
                    h = (f1 << 20) | (f2 << 8) | offset
                    fingerprints[h].append(anchor_frame)
                    paired += 1
                    if paired >= fan_out:
                        break
                if paired >= fan_out:
                    break
    return fingerprints


def find_repeated_segments(
    path_a: Path,
    path_b: Path,
    target_sample_rate: int = 5000,
    frame_size: int = 1024,
    hop_size: int = 512,
    num_peaks: int = 3,
    fan_out: int = 3,
    target_frames: int = 15,
    min_support: int = 12,
    merge_gap_frames: int = 20,
    max_candidates: int = 15,
    min_verify_correlation: float = 0.6,
) -> list[MatchCandidate]:
    """Find audio segments that occur in both files (independent of where in each file
    they appear) - a strong signal for jingles/bumpers/intros that are reused across
    episodes of the same show, as opposed to spoken content which won't repeat."""
    from app.services.memory_diagnostics import log_memory

    samples_a = _load_mono_samples(path_a, target_sample_rate)
    log_memory(log, f"fingerprint after load A n={len(samples_a)} bytes={samples_a.nbytes}")
    samples_b = _load_mono_samples(path_b, target_sample_rate)
    log_memory(log, f"fingerprint after load B n={len(samples_b)} bytes={samples_b.nbytes}")
    return find_repeated_segments_in_samples(
        samples_a,
        samples_b,
        target_sample_rate=target_sample_rate,
        frame_size=frame_size,
        hop_size=hop_size,
        num_peaks=num_peaks,
        fan_out=fan_out,
        target_frames=target_frames,
        min_support=min_support,
        merge_gap_frames=merge_gap_frames,
        max_candidates=max_candidates,
        min_verify_correlation=min_verify_correlation,
    )


def find_repeated_segments_in_samples(
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    target_sample_rate: int = 5000,
    frame_size: int = 1024,
    hop_size: int = 512,
    num_peaks: int = 3,
    fan_out: int = 3,
    target_frames: int = 15,
    min_support: int = 12,
    merge_gap_frames: int = 20,
    max_candidates: int = 15,
    min_verify_correlation: float = 0.6,
) -> list[MatchCandidate]:
    """Pure-numpy core of find_repeated_segments, decoupled from audio file I/O."""
    from app.services.memory_diagnostics import log_memory

    peaks_a = _spectrogram_peaks(samples_a, frame_size, hop_size, num_peaks)
    log_memory(log, f"fingerprint after peaks A frames={len(peaks_a)}")
    peaks_b = _spectrogram_peaks(samples_b, frame_size, hop_size, num_peaks)
    log_memory(log, f"fingerprint after peaks B frames={len(peaks_b)}")

    fp_a = _build_fingerprints(peaks_a, fan_out, target_frames)
    log_memory(log, f"fingerprint after build fp_a keys={len(fp_a)} values={sum(len(v) for v in fp_a.values())}")
    fp_b = _build_fingerprints(peaks_b, fan_out, target_frames)
    log_memory(log, f"fingerprint after build fp_b keys={len(fp_b)} values={sum(len(v) for v in fp_b.values())}")

    hop_s = hop_size / target_sample_rate
    frame_span_s = frame_size / target_sample_rate

    # The old implementation materialized every (fa, fb) pair in delta_hits.
    # For common fingerprint hashes this can explode combinatorially; Episode 688
    # produced 1.2 billion stored values and exhausted the container. We instead
    # process A-frames in ascending order, keep only the current cluster per delta,
    # collapse duplicate observations for the same frame, and retain only a small
    # top-N candidate pool. Extremely common hashes are skipped because they are
    # not discriminative enough to be useful evidence of reused audio.
    max_pairings_per_hash = _FINGERPRINT_PAIRINGS_PER_HASH
    candidate_pool_limit = max(100, max_candidates * _CANDIDATE_POOL_MULTIPLIER)

    max_frame_a = max((frame for frames in fp_a.values() for frame in frames), default=-1)
    frame_hashes_a: list[list[int]] = [[] for _ in range(max_frame_a + 1)]
    for h, frames_a in fp_a.items():
        for fa in frames_a:
            frame_hashes_a[fa].append(h)

    # delta -> [last_fa, cluster_count, cluster_start]
    delta_state: dict[int, list[int]] = {}
    candidate_heap: list[tuple[int, int, MatchCandidate]] = []
    heap_counter = 0
    skipped_common_hashes = 0

    def add_candidate(delta: int, cluster_start: int, cluster_end: int, support: int) -> None:
        nonlocal heap_counter
        if support < min_support:
            return
        candidate = MatchCandidate(
            start_a_s=max(0.0, cluster_start * hop_s),
            end_a_s=cluster_end * hop_s + frame_span_s,
            start_b_s=max(0.0, cluster_start * hop_s + delta * hop_s),
            end_b_s=cluster_end * hop_s + frame_span_s + delta * hop_s,
            support=support,
        )
        heap_counter += 1
        item = (support, heap_counter, candidate)
        if len(candidate_heap) < candidate_pool_limit:
            heapq.heappush(candidate_heap, item)
        elif support > candidate_heap[0][0]:
            heapq.heapreplace(candidate_heap, item)

    for fa, hashes in enumerate(frame_hashes_a):
        if not hashes:
            continue
        hash_counts_at_fa: dict[int, int] = {}
        for h in hashes:
            hash_counts_at_fa[h] = hash_counts_at_fa.get(h, 0) + 1

        for h, occurrences_at_fa in hash_counts_at_fa.items():
            frames_b = fp_b.get(h)
            if not frames_b:
                continue
            if len(frames_b) * occurrences_at_fa > max_pairings_per_hash:
                skipped_common_hashes += 1
                continue
            for fb in frames_b:
                delta = fb - fa
                state = delta_state.get(delta)
                if state is None:
                    delta_state[delta] = [fa, 1, fa]
                    continue
                last_fa, count, cluster_start = state
                if last_fa == fa:
                    continue
                if fa - last_fa <= merge_gap_frames:
                    state[0] = fa
                    state[1] = count + 1
                else:
                    add_candidate(delta, cluster_start, last_fa, count)
                    delta_state[delta] = [fa, 1, fa]

    for delta, state in delta_state.items():
        last_fa, count, cluster_start = state
        add_candidate(delta, cluster_start, last_fa, count)

    if skipped_common_hashes:
        log.debug(
            "Skipped %d overly common fingerprint-hash occurrences during duplicate detection",
            skipped_common_hashes,
        )

    log_memory(
        log,
        f"fingerprint after delta_hits candidates={len(candidate_heap)} "
        f"states={len(delta_state)} skipped_common={skipped_common_hashes}",
    )

    candidates = [item[2] for item in candidate_heap]
    candidates.sort(key=lambda c: -c.support)
    deduped: list[MatchCandidate] = []
    for c in candidates:
        if any(not (c.end_a_s < d.start_a_s or c.start_a_s > d.end_a_s) for d in deduped):
            continue
        deduped.append(c)
        if len(deduped) >= max_candidates:
            break

    verified: list[MatchCandidate] = []
    for c in deduped:
        c.correlation = _verify_candidate(samples_a, samples_b, c, target_sample_rate)
        if c.correlation >= min_verify_correlation:
            verified.append(c)
        else:
            log.debug(
                "Rejected duplicate candidate %.1fs-%.1fs (support %d): waveform correlation "
                "%.2f below %.2f - coincidental fingerprint collisions, not reused audio",
                c.start_a_s,
                c.end_a_s,
                c.support,
                c.correlation,
                min_verify_correlation,
            )
    verified.sort(key=lambda c: c.start_a_s)
    return verified


def _verify_candidate(
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    candidate: MatchCandidate,
    sample_rate: int,
    excerpt_seconds: float = 5.0,
    search_seconds: float = 1.0,
) -> float:
    """Landmark hashing only *proposes* matches - colliding spectral-peak pairs at a
    consistent time delta. Long stretches of merely similar-sounding audio (same voices,
    same music bed, same recording chain) can pile up such collisions without the audio
    actually being identical. This confirms or refutes a candidate on the raw waveform:
    an excerpt from the middle of the span in A is cross-correlated against B around the
    predicted aligned position. Genuinely reused audio (same source clip in both
    episodes) correlates near 1.0 there; coincidental hash pile-ups stay near 0."""
    a_start = int(candidate.start_a_s * sample_rate)
    a_end = int(candidate.end_a_s * sample_rate)
    span = a_end - a_start
    if span <= 0:
        return 0.0

    excerpt_len = min(span, int(excerpt_seconds * sample_rate))
    excerpt_start = a_start + (span - excerpt_len) // 2
    template = samples_a[excerpt_start : excerpt_start + excerpt_len]

    delta_s = candidate.start_b_s - candidate.start_a_s
    predicted_b = int(excerpt_start + delta_s * sample_rate)
    search = int(search_seconds * sample_rate)
    region_start = max(0, predicted_b - search)
    region_end = min(len(samples_b), predicted_b + excerpt_len + search)
    region = samples_b[region_start:region_end]

    ncc = normalized_cross_correlation(region, template)
    if ncc.size == 0:
        return 0.0
    return float(min(1.0, max(0.0, np.max(ncc))))


def _cluster_to_candidate(cluster: list[int], delta: int, hop_s: float, frame_span_s: float) -> MatchCandidate:
    start_a = cluster[0] * hop_s
    end_a = cluster[-1] * hop_s + frame_span_s
    return MatchCandidate(
        start_a_s=max(0.0, start_a),
        end_a_s=end_a,
        start_b_s=max(0.0, start_a + delta * hop_s),
        end_b_s=end_a + delta * hop_s,
        support=len(cluster),
    )
