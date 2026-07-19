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

    # Cutting
    crossfade_ms: int = 50
    default_auto_cut_threshold: float = 0.75

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
