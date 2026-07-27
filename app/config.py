from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    # Paths
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "app.db"
    originals_dir: Path = DATA_DIR / "audio" / "originals"
    processed_dir: Path = DATA_DIR / "audio" / "processed"
    covers_dir: Path = DATA_DIR / "covers"
    transcripts_dir: Path = DATA_DIR / "transcripts"
    logs_dir: Path = DATA_DIR / "logs"
    ad_keywords_path: Path = BASE_DIR / "app" / "config" / "ad_keywords.yaml"
    ad_jingles_dir: Path = BASE_DIR / "app" / "config" / "ad_jingles"

    # Scheduler intervals
    poll_interval_minutes: int = 30
    process_interval_seconds: int = 60
    retry_interval_minutes: int = 60
    max_retries: int = 3
    feed_backfill_check: int = 5  # how many recent entries to compare against known guids

    # Whisper
    whisper_model_size: str = "small"
    whisper_compute_type: str = "int8"

    # Local LLM ad classification (opt-in per AppConfig.llm_ad_detection_enabled - only
    # downloaded/loaded into memory the first time an episode is analyzed with it on)
    llm_model_repo: str = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    llm_model_filename: str = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    llm_context_tokens: int = 2048
    llm_threads: int = 4

    # Cutting
    crossfade_ms: int = 50
    default_auto_cut_threshold: float = 0.75

    # If set, the master feed's own self-referencing URLs (the <atom:link rel="self">
    # and feed <id>) are built as "{token}/master.xml" instead of "feed/master.xml".
    # For deployments that hide the master feed behind a secret path at the reverse
    # proxy (see Caddyfile) - without this, aggregators that re-fetch a feed using its
    # own embedded self-link (many do, per spec) would hit the now-blocked plain path
    # and get stuck showing only whatever was cached from the first successful fetch.
    # Must match the secret path segment configured in the proxy exactly.
    master_feed_secret_token: str | None = None

    model_config = SettingsConfigDict(env_prefix="PODCAST_")

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.originals_dir,
            self.processed_dir,
            self.covers_dir,
            self.transcripts_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
