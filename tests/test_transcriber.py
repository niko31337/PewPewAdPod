import threading
import time
from types import SimpleNamespace

import numpy as np
from pydub.generators import Sine

import app.services.transcriber as transcriber


def _write_test_wav(path, seconds=25):
    Sine(440).to_audio_segment(duration=int(seconds * 1000)).set_frame_rate(16000).set_channels(1).export(
        path, format="wav"
    )


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, samples, **kwargs):
        self.calls.append((len(samples), kwargs))
        duration = len(samples) / 16000.0
        word = SimpleNamespace(start=0.5, end=1.0, word=" hello")
        segment = SimpleNamespace(start=0.0, end=min(1.0, duration), text=" hello", words=[word])
        info = SimpleNamespace(language="de")
        return iter([segment]), info


def test_transcribe_audio_chunks_long_input_and_offsets_timestamps(monkeypatch, tmp_path):
    wav_path = tmp_path / "long.wav"
    _write_test_wav(wav_path, seconds=125)

    model = _FakeModel()
    monkeypatch.setattr(transcriber, "_get_model", lambda: model)
    monkeypatch.setattr(transcriber.settings, "whisper_chunk_seconds", 60)
    monkeypatch.setattr(transcriber, "_trim_memory", lambda: None)
    monkeypatch.setattr(transcriber, "_rss_mb", lambda: 100.0)

    result = transcriber.transcribe_audio(wav_path)

    assert len(model.calls) == 3
    assert all(call[0] <= 60 * 16000 for call in model.calls)
    assert [round(seg.start, 1) for seg in result] == [0.0, 60.0, 120.0]
    assert [round(seg.words[0].start, 1) for seg in result] == [0.5, 60.5, 120.5]
    assert all(call[1]["word_timestamps"] is True for call in model.calls)


def test_iter_audio_chunks_emits_float32_chunks(tmp_path):
    wav_path = tmp_path / "audio.wav"
    _write_test_wav(wav_path, seconds=2.5)

    chunks = list(transcriber._iter_audio_chunks(wav_path, chunk_seconds=1))

    assert len(chunks) == 3
    assert all(chunk.dtype == np.float32 for chunk in chunks)
    assert sum(len(chunk) for chunk in chunks) == 2.5 * 16000


def test_transcriptions_are_serialized(monkeypatch, tmp_path):
    calls = []
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    class SerializingFakeModel:
        def transcribe(self, samples, **kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                calls.append(len(samples))
            time.sleep(0.05)
            with state_lock:
                active -= 1
            info = SimpleNamespace(language="de")
            segment = SimpleNamespace(
                start=0.0, end=min(1.0, len(samples) / 16000.0), text=" hello", words=[]
            )
            return iter([segment]), info

    model = SerializingFakeModel()
    monkeypatch.setattr(transcriber, "_get_model", lambda: model)
    monkeypatch.setattr(transcriber.settings, "whisper_chunk_seconds", 60)
    monkeypatch.setattr(transcriber, "_trim_memory", lambda: None)
    monkeypatch.setattr(transcriber, "_rss_mb", lambda: 100.0)
    monkeypatch.setattr(
        transcriber,
        "_iter_audio_chunks",
        lambda path, chunk_seconds: iter([np.zeros(16000, dtype=np.float32)]),
    )

    start = threading.Barrier(3)
    results = []

    def run():
        start.wait()
        results.append(transcriber.transcribe_audio(tmp_path / "episode.mp3"))

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert max_active == 1
    assert len(calls) == 2
