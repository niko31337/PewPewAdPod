import ctypes
import gc
import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)

_model = None
_transcription_lock = Lock()

_AUDIO_SAMPLE_RATE = 16000
_SAMPLE_BYTES = 4  # float32 little-endian PCM
_READ_BLOCK_BYTES = 8 * 1024 * 1024


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        log.info(
            "Loading faster-whisper model=%s compute_type=%s",
            settings.whisper_model_size,
            settings.whisper_compute_type,
        )
        _model = WhisperModel(
            settings.whisper_model_size,
            device="cpu",
            compute_type=settings.whisper_compute_type,
        )
    return _model


def _read_chunk(stdout, target_bytes: int) -> bytearray:
    """Read up to target_bytes from a pipe without assuming one read() fills it."""
    buf = bytearray()
    while len(buf) < target_bytes:
        block = stdout.read(min(_READ_BLOCK_BYTES, target_bytes - len(buf)))
        if not block:
            break
        buf.extend(block)
    return buf


def _iter_audio_chunks(audio_path: Path, chunk_seconds: int):
    """Decode audio once with ffmpeg and yield bounded float32 chunks.

    faster-whisper's normal path decodes the *entire* input file into one NumPy array
    before feature extraction. On a 5h38m episode that is about 1.3 GiB just for mono
    float32 samples, followed by much larger STFT temporaries. Streaming the same PCM
    through ffmpeg keeps the Python-side waveform bounded to one chunk.
    """
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")

    chunk_samples = int(chunk_seconds * _AUDIO_SAMPLE_RATE)
    if chunk_samples <= 0:
        raise ValueError("chunk_seconds is too small")
    target_bytes = chunk_samples * _SAMPLE_BYTES

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-map",
        "0:a:0",
        "-ar",
        str(_AUDIO_SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "f32le",
        "-",
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        while True:
            raw = _read_chunk(process.stdout, target_bytes)
            if not raw:
                break
            if len(raw) % _SAMPLE_BYTES:
                raise RuntimeError(
                    f"ffmpeg returned incomplete float32 PCM block for {audio_path}: {len(raw)} bytes"
                )
            yield np.frombuffer(raw, dtype=np.float32)

        stderr = process.stderr.read().decode(errors="replace").strip()
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"ffmpeg failed decoding {audio_path}: {stderr}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _trim_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def transcribe_audio(audio_path: Path) -> list[TranscriptSegment]:
    if not _transcription_lock.acquire(blocking=False):
        log.info(
            "Waiting for transcription lock: episode=%s",
            audio_path.stem,
        )
        _transcription_lock.acquire()

    log.info("Acquired transcription lock: episode=%s", audio_path.stem)
    try:
        return _transcribe_audio_locked(audio_path)
    finally:
        _transcription_lock.release()
        log.info("Released transcription lock: episode=%s", audio_path.stem)


def _transcribe_audio_locked(audio_path: Path) -> list[TranscriptSegment]:
    from app.services.memory_diagnostics import log_memory

    model = _get_model()
    chunk_seconds = max(60, int(settings.whisper_chunk_seconds))
    segments: list[TranscriptSegment] = []
    detected_language = None

    for chunk_index, samples in enumerate(_iter_audio_chunks(audio_path, chunk_seconds), start=1):
        chunk_start_s = (chunk_index - 1) * chunk_seconds
        log_memory(log, f"episode={audio_path.stem} whisper chunk={chunk_index} BEFORE")
        chunk_duration_s = len(samples) / _AUDIO_SAMPLE_RATE
        rss_before = _rss_mb()
        log.info(
            "Transcribing chunk %d: %.1fs-%.1fs (%0.1fs), RSS before=%.1f MB",
            chunk_index,
            chunk_start_s,
            chunk_start_s + chunk_duration_s,
            chunk_duration_s,
            rss_before if rss_before is not None else -1.0,
        )

        transcribe_kwargs = {
            "word_timestamps": True,
            "beam_size": 1,
        }
        if detected_language:
            transcribe_kwargs["language"] = detected_language

        segments_iter, info = model.transcribe(samples, **transcribe_kwargs)
        if detected_language is None:
            detected_language = getattr(info, "language", None)
            if detected_language:
                log.info("Detected language '%s' for episode %s", detected_language, audio_path.stem)

        for seg in segments_iter:
            words = [
                Word(
                    start=w.start + chunk_start_s,
                    end=w.end + chunk_start_s,
                    text=w.word,
                )
                for w in (seg.words or [])
            ]
            segments.append(
                TranscriptSegment(
                    start=seg.start + chunk_start_s,
                    end=seg.end + chunk_start_s,
                    text=seg.text,
                    words=words,
                )
            )

        del segments_iter
        del samples
        _trim_memory()
        log_memory(log, f"episode={audio_path.stem} whisper chunk={chunk_index} AFTER")
        rss_after = _rss_mb()
        log.info(
            "Finished transcription chunk %d, segments=%d, RSS after=%.1f MB",
            chunk_index,
            len(segments),
            rss_after if rss_after is not None else -1.0,
        )

    return segments


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        return None
    return None


def save_transcript_json(episode_id: int | str, segments: list[TranscriptSegment]) -> Path:
    settings.transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = settings.transcripts_dir / f"{episode_id}.json"
    payload = [asdict(s) for s in segments]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_transcript_json(episode_id: int | str) -> list[TranscriptSegment]:
    path = settings.transcripts_dir / f"{episode_id}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        TranscriptSegment(
            start=s["start"],
            end=s["end"],
            text=s["text"],
            words=[Word(**w) for w in s.get("words", [])],
        )
        for s in data
    ]
