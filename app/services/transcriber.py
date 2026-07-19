import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_model = None


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


def transcribe_audio(audio_path: Path) -> list[TranscriptSegment]:
    model = _get_model()
    segments_iter, _info = model.transcribe(str(audio_path), word_timestamps=True, beam_size=1)

    segments: list[TranscriptSegment] = []
    for seg in segments_iter:
        words = [Word(start=w.start, end=w.end, text=w.word) for w in (seg.words or [])]
        segments.append(TranscriptSegment(start=seg.start, end=seg.end, text=seg.text, words=words))
    return segments


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
