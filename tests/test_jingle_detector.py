import numpy as np

from app.services.jingle_detector import _find_peaks, normalized_cross_correlation


def test_normalized_cross_correlation_finds_exact_match():
    rng = np.random.default_rng(42)
    episode = rng.uniform(-1, 1, 5000).astype(np.float32)
    jingle = episode[1200:1300].copy()

    ncc = normalized_cross_correlation(episode, jingle)

    assert ncc.size == len(episode) - len(jingle) + 1
    best_idx = int(np.argmax(ncc))
    assert abs(best_idx - 1200) <= 1
    assert ncc[best_idx] > 0.99


def test_normalized_cross_correlation_robust_to_amplitude_change():
    rng = np.random.default_rng(7)
    episode = rng.uniform(-1, 1, 5000).astype(np.float32)
    jingle = episode[2000:2150].copy()
    episode_quiet = episode.copy()
    episode_quiet[2000:2150] *= 0.3  # simulate a quieter re-encode of the same jingle

    ncc = normalized_cross_correlation(episode_quiet, jingle)
    best_idx = int(np.argmax(ncc))

    assert abs(best_idx - 2000) <= 1
    assert ncc[best_idx] > 0.95


def test_normalized_cross_correlation_no_match_stays_low():
    rng = np.random.default_rng(1)
    episode = rng.uniform(-1, 1, 5000).astype(np.float32)
    unrelated_jingle = rng.uniform(-1, 1, 200).astype(np.float32)

    ncc = normalized_cross_correlation(episode, unrelated_jingle)

    assert np.max(ncc) < 0.6


def test_find_peaks_clusters_nearby_indices_into_one():
    ncc = np.zeros(100)
    ncc[40:45] = [0.7, 0.9, 0.95, 0.88, 0.72]  # one true peak spread over a few samples
    ncc[80] = 0.65  # a second, separate peak

    peaks = _find_peaks(ncc, threshold=0.6, min_gap_samples=5)

    assert len(peaks) == 2
    idxs = sorted(p[0] for p in peaks)
    assert idxs[0] == 42
    assert idxs[1] == 80


def test_normalized_cross_correlation_chunked_matches_reference():
    import app.services.jingle_detector as detector
    rng = np.random.default_rng(123)
    episode = rng.normal(size=250_000).astype(np.float32)
    jingle = rng.normal(size=1_234).astype(np.float32)
    n = len(episode) + len(jingle) - 1
    fft_size = 1 << (n - 1).bit_length()
    ep_fft = np.fft.rfft(episode, fft_size)
    centered = jingle - jingle.mean()
    j_fft = np.fft.rfft(centered[::-1], fft_size)
    corr = np.fft.irfft(ep_fft * j_fft, fft_size)[:n]
    valid = corr[len(jingle) - 1 : len(jingle) - 1 + len(episode) - len(jingle) + 1]
    sq = episode.astype(np.float64) ** 2
    cs = np.cumsum(np.insert(sq, 0, 0.0))
    norms = np.sqrt(np.maximum(cs[len(jingle):] - cs[:-len(jingle)], 1e-12))
    reference = valid / (norms * float(np.linalg.norm(centered)))
    actual = detector.normalized_cross_correlation(episode, jingle)
    np.testing.assert_allclose(actual, reference, rtol=2e-5, atol=2e-5)
