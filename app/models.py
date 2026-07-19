from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EpisodeStatus(str, Enum):
    NEW = "new"
    REANALYZE = "reanalyze"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    PENDING_REVIEW = "pending_review"
    CUTTING = "cutting"
    PUBLISHED = "published"
    FAILED_DOWNLOAD = "failed_download"
    FAILED_TRANSCRIBE = "failed_transcribe"
    FAILED_ANALYZE = "failed_analyze"
    FAILED_CUT = "failed_cut"
    ERROR_PERMANENT = "error_permanent"


class SegmentStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SegmentSource(str, Enum):
    KEYWORD = "keyword"
    ACOUSTIC = "acoustic"
    MERGED = "merged"
    MANUAL = "manual"
    JINGLE = "jingle"
    DUPLICATE = "duplicate"


class CorrectionStatus(str, Enum):
    """Tracks a podcaster having swapped the audio file of an already-processed episode
    (e.g. to fix an error). The original download/cut is never touched; a correction is
    downloaded and processed on the side until the user applies or discards it."""

    DETECTED = "detected"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    CUTTING = "cutting"
    READY = "ready"
    FAILED = "failed"


class Feed(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    original_rss_url: str = Field(unique=True, index=True)
    description: Optional[str] = None
    author: Optional[str] = None
    language: str = "de"
    cover_image_source_url: Optional[str] = None
    cover_image_local_path: Optional[str] = None
    auto_cut: bool = False
    confidence_threshold: Optional[float] = None
    active: bool = True
    last_polled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Episode(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uq_episode_feed_guid"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    feed_id: int = Field(foreign_key="feed.id", index=True)
    guid: str = Field(index=True)
    title: str
    description: Optional[str] = None
    pubdate: Optional[datetime] = None

    original_audio_url: str
    original_audio_path: Optional[str] = None
    processed_audio_path: Optional[str] = None
    processed_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None

    itunes_image_url: Optional[str] = None
    local_image_path: Optional[str] = None
    itunes_duration: Optional[str] = None
    itunes_season: Optional[str] = None
    itunes_episode: Optional[str] = None

    status: str = EpisodeStatus.NEW
    error_message: Optional[str] = None
    retry_count: int = 0

    # Set when the podcaster replaces this episode's audio file after the fact (e.g. to
    # fix an error in the original). Populated by feed_ingest, processed by
    # pipeline.process_episode_correction(); original_audio_path/processed_audio_path
    # above are never touched until the user explicitly applies the correction.
    correction_audio_url: Optional[str] = None
    correction_original_path: Optional[str] = None
    correction_processed_path: Optional[str] = None
    correction_status: Optional[str] = None
    correction_error_message: Optional[str] = None
    correction_detected_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    published_at: Optional[datetime] = None


class AdSegment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    start_ms: int
    end_ms: int
    confidence: float = 0.0
    matched_keywords: str = "[]"  # JSON-encoded list[str]
    matched_jingles: str = "[]"  # JSON-encoded list[str] (jingle filenames)
    transcript_snippet: str = ""
    source: str = SegmentSource.MERGED
    status: str = SegmentStatus.PENDING
    is_correction: bool = False
    is_duplicate_match: bool = False  # found identical in the previous episode's audio too
    created_at: datetime = Field(default_factory=utcnow)


class FeedJingleMatch(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("feed_id", "jingle_filename", name="uq_feed_jingle"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    feed_id: int = Field(foreign_key="feed.id", index=True)
    jingle_filename: str
    match_count: int = 0
    last_matched_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class AppConfig(SQLModel, table=True):
    """Single-row table (id always 1) holding user-editable runtime settings."""

    id: Optional[int] = Field(default=None, primary_key=True)
    max_episodes_per_feed: Optional[int] = None  # None = unbegrenzt
    max_cache_size_mb: Optional[float] = None  # None = unbegrenzt
    min_duplicate_seconds: Optional[float] = None  # None = ad_keywords.yaml-Default verwenden
    updated_at: datetime = Field(default_factory=utcnow)
