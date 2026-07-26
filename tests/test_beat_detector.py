import numpy as np

import app.services.beat_detector as beat_detector_module
from app.services.beat_detector import _bass_envelope, find_beat_windows_in_samples

SR = 8000


def _white_noise(seconds: float, seed: int, amplitude: float = 0.3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-amplitude, amplitude, int(seconds * SR)).astype(np.float32)


def _bass_beat(seconds: float, bpm: float = 120.0, pulse_hz: float = 90.0, pulse_ms: float = 120.0) -> np.ndarray:
    """A steady low-frequency pulse train, like a kick drum under an ad read."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    period_s = 60.0 / bpm
    pulse_len_s = pulse_ms / 1000.0
    phase = np.mod(t, period_s)
    envelope = np.where(phase < pulse_len_s, np.exp(-phase / (pulse_len_s / 3)), 0.0)
    tone = np.sin(2 * np.pi * pulse_hz * t)
    return (0.9 * envelope * tone).astype(np.float32)


def test_steady_bass_pulse_scores_high_periodicity():
    signal = _bass_beat(10.0) + _white_noise(10.0, seed=1, amplitude=0.05)

    windows = find_beat_windows_in_samples(signal, target_sample_rate=SR, window_seconds=4.0, step_seconds=2.0)

    assert windows
    assert max(w.periodicity for w in windows) > 0.5


def test_plain_speech_like_noise_scores_low_periodicity():
    # Broadband noise has no steady low-frequency pulse - stands in for spoken content
    # without a music bed underneath it.
    signal = _white_noise(10.0, seed=7, amplitude=0.3)

    windows = find_beat_windows_in_samples(signal, target_sample_rate=SR, window_seconds=4.0, step_seconds=2.0)

    assert windows
    assert all(w.periodicity < 0.4 for w in windows)


def test_beat_only_in_part_of_the_signal_is_localized():
    quiet = _white_noise(4.0, seed=3, amplitude=0.05)
    beat = _bass_beat(6.0) + _white_noise(6.0, seed=4, amplitude=0.05)
    silence_after = _white_noise(4.0, seed=5, amplitude=0.05)
    signal = np.concatenate([quiet, beat, silence_after])

    windows = find_beat_windows_in_samples(signal, target_sample_rate=SR, window_seconds=4.0, step_seconds=1.0)
    by_start = {round(w.start_s): w for w in windows}

    before = by_start[0]  # fully within the quiet lead-in [0, 4)
    during = by_start[6]  # fully within the beat region [4, 10)
    after = by_start[10]  # fully within the trailing silence [10, 14)

    assert during.periodicity > 0.5
    assert during.periodicity > before.periodicity
    assert during.periodicity > after.periodicity


def test_bass_envelope_chunking_matches_a_single_unchunked_pass(monkeypatch):
    # A multi-hour episode makes _bass_envelope allocate one gigabytes-sized array if
    # it windows every frame at once - this is what actually crashed the container.
    # Chunking must produce bit-for-bit the same envelope regardless of chunk size.
    signal = _bass_beat(3.0) + _white_noise(3.0, seed=9, amplitude=0.05)

    monkeypatch.setattr(beat_detector_module, "_ENVELOPE_CHUNK_FRAMES", 10_000_000)
    unchunked = _bass_envelope(signal, SR, frame_size=512, hop_size=128, low_hz=50.0, high_hz=150.0)

    monkeypatch.setattr(beat_detector_module, "_ENVELOPE_CHUNK_FRAMES", 37)
    chunked = _bass_envelope(signal, SR, frame_size=512, hop_size=128, low_hz=50.0, high_hz=150.0)

    assert len(chunked) == len(unchunked) > 100  # sanity: actually spans several chunk boundaries
    np.testing.assert_allclose(chunked, unchunked, rtol=1e-5, atol=1e-6)


def test_bass_envelope_peak_memory_does_not_scale_with_episode_length():
    import tracemalloc

    def _peak_memory_for(duration_s: float) -> int:
        signal = np.zeros(int(duration_s * SR), dtype=np.float32)
        tracemalloc.start()
        _bass_envelope(signal, SR, frame_size=512, hop_size=128, low_hz=50.0, high_hz=150.0)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    peak_1h = _peak_memory_for(3600)
    peak_6h = _peak_memory_for(6 * 3600)

    # Unchunked, this would scale with duration (~1h*8000/128 frames * 512 * 4 bytes
    # =~ 737 MB for a 1h episode's frames array alone, ~6x that at 6h). Chunked, peak
    # memory is bounded by chunk size regardless of how many chunks there are.
    assert peak_6h < peak_1h * 1.5
