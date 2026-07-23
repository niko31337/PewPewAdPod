from app import paths
from app.paths import cross_platform_basename, resolve_cover_path, resolve_stored_path


def test_windows_style_path():
    assert cross_platform_basename(r"C:\Users\niko-\PythonProject\data\audio\processed\1.mp3") == "1.mp3"


def test_posix_style_path():
    assert cross_platform_basename("/app/data/audio/processed/1.mp3") == "1.mp3"


def test_bare_filename():
    assert cross_platform_basename("1.mp3") == "1.mp3"


def test_none_and_empty():
    assert cross_platform_basename(None) == ""
    assert cross_platform_basename("") == ""


def test_resolve_stored_path_returns_none_for_empty():
    assert resolve_stored_path(None) is None
    assert resolve_stored_path("") is None


def test_resolve_stored_path_uses_the_path_as_is_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.settings, "originals_dir", tmp_path / "originals")
    monkeypatch.setattr(paths.settings, "processed_dir", tmp_path / "processed")
    audio = tmp_path / "5.mp3"
    audio.write_bytes(b"data")

    assert resolve_stored_path(str(audio)) == audio


def test_resolve_stored_path_falls_back_to_basename_lookup_across_os_styles(tmp_path, monkeypatch):
    # Simulates a DB row written by a process on a different OS (e.g. a Linux Docker
    # container's "/app/data/..." path, now read by this Windows test run - or, in
    # production, the reverse: a Windows-authored path read inside the container).
    # Path(...) resolves that string relative to the current OS's own rules and won't
    # find the real file, so this must relocate it by basename instead.
    originals_dir = tmp_path / "originals"
    originals_dir.mkdir()
    monkeypatch.setattr(paths.settings, "originals_dir", originals_dir)
    monkeypatch.setattr(paths.settings, "processed_dir", tmp_path / "processed")
    real_file = originals_dir / "7.mp3"
    real_file.write_bytes(b"data")

    stale_foreign_path = "/app/data/audio/originals/7.mp3"

    assert resolve_stored_path(stale_foreign_path) == real_file


def test_resolve_stored_path_returns_none_when_file_truly_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.settings, "originals_dir", tmp_path / "originals")
    monkeypatch.setattr(paths.settings, "processed_dir", tmp_path / "processed")

    assert resolve_stored_path(r"C:\some\stale\path\99.mp3") is None


def test_resolve_cover_path_uses_the_stored_path_as_is_when_it_exists(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"data")
    canonical = tmp_path / "elsewhere" / "cover.jpg"  # deliberately not where the file is

    assert resolve_cover_path(str(cover), canonical) == cover


def test_resolve_cover_path_falls_back_to_canonical_location_for_a_foreign_os_path(tmp_path):
    # Reproduces the real bug: a Feed/Episode row whose cover path was written by a
    # process on a different OS (e.g. a Linux Docker container's "/app/data/..." path,
    # now read by this Windows test run - or, in production, the reverse: a
    # Windows-authored path read inside the container). Path(...).exists() on a
    # foreign-OS string is always False, so watermarking silently fell back to the
    # fully-replaced master cover instead of actually watermarking this feed's real
    # cover art - even though the real file is sitting right where convention says.
    canonical = tmp_path / "covers" / "2" / "cover.jpg"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"data")
    stale_foreign_path = "/app/data/covers/2/cover.jpg"

    assert resolve_cover_path(stale_foreign_path, canonical) == canonical


def test_resolve_cover_path_returns_none_when_nothing_exists(tmp_path):
    assert resolve_cover_path(None, tmp_path / "covers" / "9" / "cover.jpg") is None
    assert resolve_cover_path("/app/data/covers/9/cover.jpg", tmp_path / "covers" / "9" / "cover.jpg") is None
