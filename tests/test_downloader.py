from pathlib import Path

import pytest

from app.services.downloader import _replace_with_retry


def test_replace_succeeds_immediately_when_unlocked(tmp_path):
    tmp = tmp_path / "file.mp3.part"
    tmp.write_bytes(b"content")
    dest = tmp_path / "file.mp3"

    _replace_with_retry(tmp, dest, attempts=3, delay_s=0)

    assert dest.read_bytes() == b"content"
    assert not tmp.exists()


def test_replace_retries_then_succeeds_after_transient_lock(tmp_path, monkeypatch):
    tmp = tmp_path / "file.mp3.part"
    tmp.write_bytes(b"content")
    dest = tmp_path / "file.mp3"

    calls = {"count": 0}
    real_replace = Path.replace

    def flaky_replace(self, target):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("simulated Windows file lock")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    _replace_with_retry(tmp, dest, attempts=5, delay_s=0)

    assert calls["count"] == 3
    assert dest.read_bytes() == b"content"


def test_replace_gives_up_and_raises_after_exhausting_attempts(tmp_path, monkeypatch):
    tmp = tmp_path / "file.mp3.part"
    tmp.write_bytes(b"content")
    dest = tmp_path / "file.mp3"

    def always_locked(self, target):
        raise PermissionError("simulated permanent lock")

    monkeypatch.setattr(Path, "replace", always_locked)

    with pytest.raises(PermissionError):
        _replace_with_retry(tmp, dest, attempts=3, delay_s=0)
