import numpy as np

import app.services.fingerprint as fingerprint_module
from app.services.fingerprint import (
    MatchCandidate,
    _spectrogram_peaks,
    _verify_candidate,
    find_repeated_segments_in_samples,
)

SR = 5000


def _white_noise(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-0.8, 0.8, int(seconds * SR)).astype(np.float32)


def _tone_burst(seconds: float, freqs=(440, 880, 1320, 220)) -> np.ndarray:
    # Landmark fingerprinting keys off stable spectral peaks, which sustained tones
    # (like a musical jingle) have and broadband noise does not - white noise's "peaks"
    # are just random bin-to-bin fluctuation and won't survive a few samples of shift.
    t = np.arange(int(seconds * SR)) / SR
    signal = np.zeros_like(t, dtype=np.float32)
    for f in freqs:
        signal += 0.2 * np.sin(2 * np.pi * f * t)
    return signal.astype(np.float32)


def test_finds_shared_segment_at_different_positions_in_each_file():
    shared = _tone_burst(1.5)

    episode_a = _white_noise(8.0, seed=1)
    episode_b = _white_noise(6.0, seed=2)

    a_start_s = 2.0
    b_start_s = 1.0
    a_start = int(a_start_s * SR)
    b_start = int(b_start_s * SR)
    episode_a[a_start : a_start + len(shared)] = shared
    episode_b[b_start : b_start + len(shared)] = shared

    candidates = find_repeated_segments_in_samples(
        episode_a,
        episode_b,
        target_sample_rate=SR,
        frame_size=256,
        hop_size=128,
        num_peaks=3,
        fan_out=3,
        target_frames=15,
        min_support=5,
        merge_gap_frames=5,
    )

    assert candidates, "expected at least one repeated-segment candidate"
    best = max(candidates, key=lambda c: c.support)

    # the detected window in A should overlap the true shared region
    assert best.start_a_s < a_start_s + 1.5
    assert best.end_a_s > a_start_s
    # and the corresponding window in B should line up with where the shared audio
    # actually sits in B (same offset applied)
    implied_offset = best.start_b_s - best.start_a_s
    true_offset = b_start_s - a_start_s
    assert abs(implied_offset - true_offset) < 0.3


def test_unrelated_files_produce_no_high_support_candidates():
    episode_a = _white_noise(8.0, seed=11)
    episode_b = _white_noise(6.0, seed=22)

    candidates = find_repeated_segments_in_samples(
        episode_a,
        episode_b,
        target_sample_rate=SR,
        frame_size=256,
        hop_size=128,
        num_peaks=3,
        fan_out=3,
        target_frames=15,
        min_support=5,
        merge_gap_frames=5,
    )

    # unrelated noise can produce occasional coincidental low-support matches,
    # but never the kind of large, sustained cluster a genuine shared segment yields
    assert all(c.support < 15 for c in candidates)


def test_common_fingerprint_hash_is_memory_bounded(monkeypatch):
    # A very common hash can otherwise create a Cartesian product with millions or
    # billions of (frame_a, frame_b) pairs. The duplicate detector must skip such
    # non-discriminative hashes instead of materializing the pair list.
    monkeypatch.setattr(fingerprint_module, "_spectrogram_peaks", lambda *args, **kwargs: [])
    common_frames = list(range(60_000))
    monkeypatch.setattr(
        fingerprint_module,
        "_build_fingerprints",
        lambda *args, **kwargs: {123: common_frames},
    )

    a = np.zeros(10, dtype=np.float32)
    b = np.zeros(10, dtype=np.float32)
    candidates = find_repeated_segments_in_samples(a, b)

    assert candidates == []


def test_genuine_shared_segment_passes_waveform_verification():
    shared = _tone_burst(1.5)
    episode_a = _white_noise(8.0, seed=1)
    episode_b = _white_noise(6.0, seed=2)
    episode_a[int(2.0 * SR) : int(2.0 * SR) + len(shared)] = shared
    episode_b[int(1.0 * SR) : int(1.0 * SR) + len(shared)] = shared

    candidate = MatchCandidate(start_a_s=2.0, end_a_s=3.5, start_b_s=1.0, end_b_s=2.5, support=50)
    correlation = _verify_candidate(episode_a, episode_b, candidate, SR)

    assert correlation > 0.8


def test_coincidental_candidate_over_unrelated_audio_fails_verification():
    # Simulates the false-positive case: the hash histogram proposed a span, but the
    # underlying audio in the two episodes is actually different content.
    episode_a = _white_noise(8.0, seed=31)
    episode_b = _white_noise(6.0, seed=32)

    candidate = MatchCandidate(start_a_s=2.0, end_a_s=6.0, start_b_s=1.0, end_b_s=5.0, support=500)
    correlation = _verify_candidate(episode_a, episode_b, candidate, SR)

    assert correlation < 0.3


def test_find_repeated_segments_attaches_correlation_to_survivors():
    shared = _tone_burst(1.5)
    episode_a = _white_noise(8.0, seed=1)
    episode_b = _white_noise(6.0, seed=2)
    episode_a[int(2.0 * SR) : int(2.0 * SR) + len(shared)] = shared
    episode_b[int(1.0 * SR) : int(1.0 * SR) + len(shared)] = shared

    candidates = find_repeated_segments_in_samples(
        episode_a,
        episode_b,
        target_sample_rate=SR,
        frame_size=256,
        hop_size=128,
        num_peaks=3,
        fan_out=3,
        target_frames=15,
        min_support=5,
        merge_gap_frames=5,
    )

    assert candidates
    assert all(c.correlation >= 0.6 for c in candidates)


def test_spectrogram_peaks_chunking_matches_a_single_unchunked_pass(monkeypatch):
    # A multi-hour episode makes _spectrogram_peaks allocate a huge array if it windows
    # every frame at once - this is what actually crashed the container when comparing
    # two multi-hour episodes for duplicate segments. Chunking must produce the exact
    # same per-frame peaks regardless of chunk size.
    signal = _tone_burst(3.0) + _white_noise(3.0, seed=13) * 0.05

    monkeypatch.setattr(fingerprint_module, "_SPECTROGRAM_CHUNK_FRAMES", 10_000_000)
    unchunked = _spectrogram_peaks(signal, frame_size=256, hop_size=128, num_peaks=3)

    monkeypatch.setattr(fingerprint_module, "_SPECTROGRAM_CHUNK_FRAMES", 29)
    chunked = _spectrogram_peaks(signal, frame_size=256, hop_size=128, num_peaks=3)

    assert len(chunked) == len(unchunked) > 100  # sanity: spans several chunk boundaries
    assert chunked == unchunked


def test_spectrogram_peaks_memory_does_not_scale_with_episode_length():
    import tracemalloc

    def _peak_memory_for(duration_s: float) -> int:
        signal = np.zeros(int(duration_s * SR), dtype=np.float32)
        tracemalloc.start()
        _spectrogram_peaks(signal, frame_size=1024, hop_size=512, num_peaks=3)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    peak_1h = _peak_memory_for(3600)
    peak_6h = _peak_memory_for(6 * 3600)

    # Unchunked, peak memory is proportional to frame_count (i.e. to duration), so 6h
    # would need ~6x the memory of 1h. Chunked, peak memory is bounded by chunk size
    # regardless of how many chunks there are - it should barely move.
    assert peak_6h < peak_1h * 1.5


def _reference_best_cluster_support(a, b, *, frame_size, hop_size, num_peaks, fan_out, target_frames, merge_gap_frames):
    """The original, memory-hungry algorithm, kept here only as an oracle: materialise
    every (fa, fb) pairing per delta and count cluster members. Fine on test-sized input."""
    from collections import defaultdict

    from app.services.fingerprint import _build_fingerprints

    fp_a = _build_fingerprints(_spectrogram_peaks(a, frame_size, hop_size, num_peaks), fan_out, target_frames)
    fp_b = _build_fingerprints(_spectrogram_peaks(b, frame_size, hop_size, num_peaks), fan_out, target_frames)
    delta_hits = defaultdict(list)
    for h in fp_a.keys() & fp_b.keys():
        for fa in fp_a[h]:
            for fb in fp_b[h]:
                delta_hits[fb - fa].append(fa)

    best = 0
    for hits in delta_hits.values():
        hits.sort()
        cluster_len = 1
        for prev, cur in zip(hits, hits[1:]):
            if cur - prev <= merge_gap_frames:
                cluster_len += 1
            else:
                best = max(best, cluster_len)
                cluster_len = 1
        best = max(best, cluster_len)
    return best


def test_support_counts_every_hash_pairing_like_the_original_algorithm():
    # The streaming rewrite stopped storing the pairings (that is what bounds memory) but
    # must keep counting them: ad_keywords.yaml's min_support / support_normalize are
    # calibrated to that scale. Counting distinct frames instead made short recurring
    # stingers (2-3 s) fall below min_support and silently stop being detected - on real
    # episodes the original found 9 matches where the distinct-frame count found 3.
    shared = _tone_burst(1.5)
    episode_a = _white_noise(8.0, seed=1)
    episode_b = _white_noise(6.0, seed=2)
    episode_a[int(2.0 * SR) : int(2.0 * SR) + len(shared)] = shared
    episode_b[int(1.0 * SR) : int(1.0 * SR) + len(shared)] = shared

    params = dict(frame_size=256, hop_size=128, num_peaks=3, fan_out=3, target_frames=15, merge_gap_frames=5)
    candidates = find_repeated_segments_in_samples(
        episode_a, episode_b, target_sample_rate=SR, min_support=5, **params
    )
    expected = _reference_best_cluster_support(episode_a, episode_b, **params)

    assert candidates
    assert max(c.support for c in candidates) == expected
    # guard against the test passing vacuously: sustained tones hit many hashes per frame,
    # so the pairing count must clearly exceed the number of frames the segment spans
    assert expected > 1.5 * (1.5 * SR / 128)
