import subprocess
from pathlib import Path

import numpy as np
from pydub import AudioSegment


def _run_ffmpeg_decode(path: Path, target_sr: int, sample_fmt: str) -> bytes:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-ar",
        str(target_sr),
        "-ac",
        "1",
        "-f",
        sample_fmt,
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed decoding {path}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def load_mono_samples(path: Path, target_sr: int) -> np.ndarray:
    """Decode an audio file straight to mono float32 PCM at the given sample rate via a
    single ffmpeg subprocess pass, bypassing pydub's AudioSegment entirely.

    pydub's AudioSegment.from_file() decodes at the source's native sample rate/channel
    count first, then .set_channels()/.set_frame_rate() each produce another full copy
    via further ffmpeg/audioop passes - every intermediate copy alive at once. Measured
    on a real 6.5h episode, that peaked at ~12 GB even though the final downsampled
    mono array is only ~750 MB - almost certainly what was crashing the container on
    exceptionally long episodes. A single "decode + resample + downmix" ffmpeg
    invocation, with output already in the exact target format, avoids that inflation:
    peak memory is essentially just the output buffer itself.

    ffmpeg's f32le output is already normalized to [-1.0, 1.0], matching what pydub's
    manual "samples / (1 << (8*sample_width-1))" step was doing for integer PCM - no
    separate normalization needed here."""
    raw = _run_ffmpeg_decode(path, target_sr, "f32le")
    return np.frombuffer(raw, dtype=np.float32)


def load_audio_segment(path: Path, target_sr: int = 16000) -> AudioSegment:
    """Like load_mono_samples, but returns a pydub AudioSegment for callers that need
    pydub's own API (slicing, .dBFS, silence detection) rather than a raw numpy array.

    Built the same way - one ffmpeg pass straight to mono PCM at a modest sample rate -
    instead of pydub's AudioSegment.from_file(), which decodes at the source's full
    native sample rate/channel count (e.g. 44.1kHz stereo for a typical podcast) and is
    what was actually driving the multi-gigabyte peak on long episodes. The ad-detection
    signals this feeds (silence snapping, RMS jump, jingle correlation, which resamples
    further down anyway) don't need full fidelity - only the final cut, which re-reads
    the original file directly rather than reusing this object, does."""
    raw = _run_ffmpeg_decode(path, target_sr, "s16le")
    return AudioSegment(data=raw, sample_width=2, frame_rate=target_sr, channels=1)
