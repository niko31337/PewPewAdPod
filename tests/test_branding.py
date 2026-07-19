from PIL import Image

from app.services import branding


def test_generate_master_cover_creates_square_jpeg(tmp_path):
    dest = tmp_path / "cover.jpg"
    branding.generate_master_cover(dest, size=200)

    assert dest.exists()
    img = Image.open(dest)
    assert img.size == (200, 200)
    assert img.mode == "RGB"


def test_add_watermark_preserves_size_and_adds_badge_corner(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (400, 300), (10, 120, 200)).save(source, format="JPEG")

    dest = tmp_path / "watermarked.jpg"
    branding.add_watermark(source, dest, badge_fraction=0.3)

    assert dest.exists()
    result = Image.open(dest)
    assert result.size == (400, 300)

    # bottom-right corner pixel should differ from the original flat blue background,
    # i.e. the badge was actually drawn there
    original_color = (10, 120, 200)
    corner_pixel = result.getpixel((390, 290))
    assert corner_pixel != original_color

    # top-left corner should be untouched (allow a couple of JPEG-compression rounding steps)
    untouched_pixel = result.getpixel((2, 2))
    assert all(abs(a - b) <= 3 for a, b in zip(untouched_pixel, original_color))


def test_ensure_watermarked_episode_image_returns_none_without_source():
    result = branding.ensure_watermarked_episode_image(None, feed_id=1, episode_id=999999)
    assert result is None
