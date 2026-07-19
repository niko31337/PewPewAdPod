import numpy as np

from app.services.fingerprint import MatchCandidate, _verify_candidate, find_repeated_segments_in_samples

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
