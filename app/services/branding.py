import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image

from app.config import BASE_DIR, settings

log = logging.getLogger(__name__)

BRAND_DARK = (17, 17, 17)

MASTER_COVER_PATH = settings.covers_dir / "master" / "cover.jpg"
LOGO_ICON_PATH = BASE_DIR / "app" / "static" / "img" / "logo-icon.png"


@lru_cache(maxsize=1)
def _logo_icon() -> Image.Image:
    """The real PewPewAdPod comic badge (starburst + dual bolts + 'AD' tag), rasterized
    from logo-icon.svg. Cached since every watermark call needs a fresh-resized copy of it."""
    return Image.open(LOGO_ICON_PATH).convert("RGBA")


def generate_master_cover(dest_path: Path, size: int = 1400) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (size, size), BRAND_DARK)

    logo_size = int(size * 0.82)
    logo = _logo_icon().resize((logo_size, logo_size), Image.LANCZOS)
    offset = ((size - logo_size) // 2, (size - logo_size) // 2)
    img.paste(logo, offset, logo)
    img.save(dest_path, format="JPEG", quality=92)


def ensure_master_cover() -> Path:
    if not MASTER_COVER_PATH.exists():
        generate_master_cover(MASTER_COVER_PATH)
    return MASTER_COVER_PATH


def add_watermark(source_path: Path, dest_path: Path, badge_fraction: float = 0.22) -> None:
    """Overlay the small PewPewAdPod badge in the bottom-right corner of an episode's
    cover art, without touching the original file."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    base = Image.open(source_path).convert("RGBA")
    w, h = base.size
    badge_size = max(24, int(min(w, h) * badge_fraction))
    margin = max(4, int(badge_size * 0.12))

    badge = _logo_icon().resize((badge_size, badge_size), Image.LANCZOS)

    composed = base.copy()
    composed.alpha_composite(badge, (w - badge_size - margin, h - badge_size - margin))
    composed.convert("RGB").save(dest_path, format="JPEG", quality=90)


def watermarked_episode_image_path(feed_id: int, episode_id: int) -> Path:
    return settings.covers_dir / "master" / "episodes" / f"{episode_id}.jpg"


def ensure_watermarked_episode_image(source_image_path: str | None, feed_id: int, episode_id: int) -> Path | None:
    """Returns the path to a watermarked copy of the episode's (or, as fallback, the
    feed's) cover art, generating it on first use. Returns None if no source image
    is available to watermark at all."""
    if not source_image_path or not Path(source_image_path).exists():
        return None

    dest = watermarked_episode_image_path(feed_id, episode_id)
    if not dest.exists():
        try:
            add_watermark(Path(source_image_path), dest)
        except Exception:
            log.warning("Could not watermark cover art for episode %s", episode_id, exc_info=True)
            return None
    return dest
