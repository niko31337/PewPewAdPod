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
