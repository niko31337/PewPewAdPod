from app import paths
from app.paths import cross_platform_basename, resolve_stored_path


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
