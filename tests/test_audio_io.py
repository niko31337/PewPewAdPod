import numpy as np
from pydub import AudioSegment
from pydub.generators import Sine

from app.services.audio_io import load_mono_samples


def _write_test_wav(path, seconds=1.0, freq=440, sample_rate=44100):
    tone = Sine(freq).to_audio_segment(duration=int(seconds * 1000)).set_frame_rate(sample_rate)
    tone.export(path, format="wav")
    return tone


def test_load_mono_samples_matches_expected_duration_and_range(tmp_path):
    wav_path = tmp_path / "tone.wav"
    _write_test_wav(wav_path, seconds=1.0, freq=440, sample_rate=44100)

    target_sr = 8000
    samples = load_mono_samples(wav_path, target_sr)

    expected_len = target_sr * 1  # 1 second at the target sample rate
    assert abs(len(samples) - expected_len) < target_sr * 0.05  # allow small encoder slack
    assert samples.dtype == np.float32
    assert np.max(np.abs(samples)) <= 1.0 + 1e-3
    assert np.max(np.abs(samples)) > 0.1  # a real tone, not silence


def test_load_mono_samples_downmixes_stereo_to_mono(tmp_path):
    left = Sine(440).to_audio_segment(duration=500)
    right = Sine(880).to_audio_segment(duration=500)
    stereo = AudioSegment.from_mono_audiosegments(left, right)
    wav_path = tmp_path / "stereo.wav"
    stereo.export(wav_path, format="wav")

    samples = load_mono_samples(wav_path, 8000)

    assert samples.ndim == 1  # mono, not interleaved stereo


def test_load_mono_samples_raises_a_clear_error_for_a_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.mp3"
    try:
        load_mono_samples(missing, 8000)
        assert False, "expected an error for a missing file"
    except RuntimeError as exc:
        assert "ffmpeg failed" in str(exc)
