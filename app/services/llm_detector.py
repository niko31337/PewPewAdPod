import logging
import re
from dataclasses import dataclass

from app.config import settings
from app.services.transcriber import TranscriptSegment

log = logging.getLogger(__name__)

_model = None

_PROMPT_TEMPLATE = (
    "Du bist ein Klassifikator fuer Podcast-Transkripte. Dir wird ein Ausschnitt aus "
    "einem Podcast-Transkript gegeben. Bestimme, wie wahrscheinlich es ist, dass dieser "
    "Ausschnitt eine bezahlte Werbe- oder Sponsoreneinsprechung ist (Produktnennung, "
    "Rabattcode, \"gesponsert von\", Call-to-Action fuer ein Produkt) statt "
    "redaktioneller Inhalt.\n"
    "Antworte NUR mit einer ganzen Zahl von 0 bis 100 (0 = sicher keine Werbung, "
    "100 = sicher Werbung). Keine Erklaerung, kein Text, nur die Zahl.\n\n"
    'Transkript-Ausschnitt:\n"{text}"\n\nZahl:'
)


@dataclass
class LlmHit:
    start_s: float
    end_s: float
    confidence: float  # 0..1
    text: str


def _get_model():
    """Lazily downloads (once, cached under the shared Hugging Face cache dir) and
    loads the local classification model. Nothing is imported or downloaded unless a
    caller actually invokes classify_text/classify_transcript_windows - importing this
    module has no cost, matching the opt-in feature flag on AppConfig."""
    global _model
    if _model is None:
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        log.info("Downloading/loading local LLM %s/%s", settings.llm_model_repo, settings.llm_model_filename)
        model_path = hf_hub_download(repo_id=settings.llm_model_repo, filename=settings.llm_model_filename)
        _model = Llama(
            model_path=model_path,
            n_ctx=settings.llm_context_tokens,
            n_threads=settings.llm_threads,
            verbose=False,
        )
    return _model


def _parse_confidence(text: str) -> float:
    match = re.search(r"\d+", text)
    if not match:
        return 0.0
    value = int(match.group())
    return max(0.0, min(1.0, value / 100.0))


def classify_text(text: str) -> float:
    model = _get_model()
    prompt = _PROMPT_TEMPLATE.format(text=text.strip()[:2000])
    result = model(prompt, max_tokens=6, temperature=0.0, stop=["\n"])
    output = result["choices"][0]["text"]
    return _parse_confidence(output)


def _group_segments_into_windows(
    segments: list[TranscriptSegment], window_seconds: float, audio_duration_s: float
) -> list[tuple[float, float, str]]:
    if not segments or audio_duration_s <= 0:
        return []

    windows: list[tuple[float, float, str]] = []
    window_start = 0.0
    while window_start < audio_duration_s:
        window_end = min(audio_duration_s, window_start + window_seconds)
        text = " ".join(s.text.strip() for s in segments if s.end > window_start and s.start < window_end).strip()
        if text:
            windows.append((window_start, window_end, text))
        window_start = window_end
    return windows


def classify_transcript_windows(
    segments: list[TranscriptSegment], window_seconds: float, audio_duration_s: float
) -> list[LlmHit]:
    """Splits the transcript into sequential time windows and asks the local model
    whether each one reads like a sponsor/ad segment. Reuses the transcript faster-
    whisper already produced - no additional audio processing, just short, fast text
    classifications (a handful of generated tokens each, deterministic/temperature=0)."""
    hits = []
    for start_s, end_s, text in _group_segments_into_windows(segments, window_seconds, audio_duration_s):
        confidence = classify_text(text)
        hits.append(LlmHit(start_s=start_s, end_s=end_s, confidence=confidence, text=text))
    return hits
