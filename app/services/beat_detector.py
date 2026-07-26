from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.services.audio_io import load_mono_samples as _load_mono_samples


@dataclass
class BeatWindow:
    start_s: float
    end_s: float
    periodicity: float  # 0..1, how clearly the bass band pulses at a steady tempo
    bass_rms: float  # raw linear amplitude of the bass band in this window
    period_s: float = 0.0  # seconds between pulses at the strongest autocorrelation peak


_ENVELOPE_CHUNK_FRAMES = 5000


def _bass_envelope(
    samples: np.ndarray, sample_rate: int, frame_size: int, hop_size: int, low_hz: float, high_hz: float
) -> np.ndarray:
    """Short-time energy in a low-frequency band (typically where a kick drum / bass
    line sits), one value per hop - a 1D signal that pulses in time with a music beat
    but stays comparatively flat under plain speech.

    Processes the windowed STFT frames in bounded-size chunks rather than
    materializing all of them at once: for a multi-hour episode, "as_strided(...) *
    window" over every frame at once allocates gigabytes (frame_count * frame_size *
    4 bytes - for a 6.5h episode at the default frame_size/hop_size that's ~3 GB),
    which was enough to crash the process on very long episodes. Chunking bounds peak
    memory to chunk_size * frame_size regardless of episode length, with identical
    output since each chunk is windowed/FFT'd independently."""
    n_frames = 1 + (len(samples) - frame_size) // hop_size
    if n_frames <= 0:
        return np.array([])

    window = np.hanning(frame_size).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    band = (freqs >= low_hz) & (freqs <= high_hz)
    if not band.any():
        return np.zeros(n_frames, dtype=np.float32)

    envelope = np.empty(n_frames, dtype=np.float32)
    for chunk_start in range(0, n_frames, _ENVELOPE_CHUNK_FRAMES):
        chunk_end = min(n_frames, chunk_start + _ENVELOPE_CHUNK_FRAMES)
        count = chunk_end - chunk_start
        chunk_samples = samples[chunk_start * hop_size :]
        shape = (count, frame_size)
        strides = (chunk_samples.strides[0] * hop_size, chunk_samples.strides[0])
        frames = np.lib.stride_tricks.as_strided(chunk_samples, shape=shape, strides=strides) * window
        magnitude = np.abs(np.fft.rfft(frames, axis=1))
        envelope[chunk_start:chunk_end] = magnitude[:, band].sum(axis=1)
    return envelope


def _periodicity_score(
    envelope_segment: np.ndarray, hop_s: float, bpm_min: float, bpm_max: float
) -> tuple[float, float]:
    """Autocorrelation of the bass envelope, normalized by its own zero-lag energy, so
    the result is independent of overall loudness. A steady beat produces one dominant
    peak at the beat period; unstructured energy (or none at all) does not. Returns
    (peak height, period in seconds) - the period is what lets the caller later check
    that consecutive windows agree on the *same* tempo, not just that each one has
    *some* periodicity (a rhythmic laugh or a repeated word can look periodic for one
    window in isolation, but won't hold the same period window after window the way an
    actual looping music bed does)."""
    x = envelope_segment - envelope_segment.mean()
    zero_lag = float(np.dot(x, x))
    if zero_lag < 1e-9:
        return 0.0, 0.0

    autocorr = np.correlate(x, x, mode="full")[len(x) - 1 :]
    autocorr = autocorr / zero_lag

    lag_min = max(1, int((60.0 / bpm_max) / hop_s))
    lag_max = min(len(autocorr) - 1, int((60.0 / bpm_min) / hop_s))
    if lag_max <= lag_min:
        return 0.0, 0.0

    window = autocorr[lag_min : lag_max + 1]
    best_offset = int(np.argmax(window))
    peak = float(max(0.0, min(1.0, window[best_offset])))
    period_s = (lag_min + best_offset) * hop_s
    return peak, period_s


def find_beat_windows(
    path: Path,
    target_sample_rate: int = 8000,
    frame_size: int = 512,
    hop_size: int = 128,
    low_hz: float = 50.0,
    high_hz: float = 150.0,
    window_seconds: float = 4.0,
    step_seconds: float = 2.0,
    bpm_min: float = 60.0,
    bpm_max: float = 180.0,
) -> list[BeatWindow]:
    """Slides a window across the episode and scores how clearly a steady bass pulse
    (an ad-read's background music bed) is present at each point, independent of what's
    being said - a signal that survives even when the transcript/keyword list misses
    a sponsor read entirely."""
    samples = _load_mono_samples(path, target_sample_rate)
    return find_beat_windows_in_samples(
        samples,
        target_sample_rate=target_sample_rate,
        frame_size=frame_size,
        hop_size=hop_size,
        low_hz=low_hz,
        high_hz=high_hz,
        window_seconds=window_seconds,
        step_seconds=step_seconds,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
    )


def find_beat_windows_in_samples(
    samples: np.ndarray,
    target_sample_rate: int = 8000,
    frame_size: int = 512,
    hop_size: int = 128,
    low_hz: float = 50.0,
    high_hz: float = 150.0,
    window_seconds: float = 4.0,
    step_seconds: float = 2.0,
    bpm_min: float = 60.0,
    bpm_max: float = 180.0,
) -> list[BeatWindow]:
    envelope = _bass_envelope(samples, target_sample_rate, frame_size, hop_size, low_hz, high_hz)
    if envelope.size == 0:
        return []

    hop_s = hop_size / target_sample_rate
    frames_per_window = max(4, int(window_seconds / hop_s))
    frames_per_step = max(1, int(step_seconds / hop_s))

    results: list[BeatWindow] = []
    for start_frame in range(0, len(envelope) - frames_per_window + 1, frames_per_step):
        segment = envelope[start_frame : start_frame + frames_per_window]
        periodicity, period_s = _periodicity_score(segment, hop_s, bpm_min, bpm_max)
        start_s = start_frame * hop_s
        results.append(
            BeatWindow(
                start_s=start_s,
                end_s=start_s + window_seconds,
                periodicity=periodicity,
                bass_rms=float(np.sqrt(np.mean(segment.astype(np.float64) ** 2))),
                period_s=period_s,
            )
        )
    return results
