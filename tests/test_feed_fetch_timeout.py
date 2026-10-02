from contextlib import contextmanager

import httpx
from sqlmodel import Session, SQLModel, create_engine

from app.models import Feed
from app.services import feed_ingest


def test_fetch_returns_none_instead_of_hanging_or_raising_on_timeout(monkeypatch):
    # The original bug: feedparser.parse(url) used urllib without any timeout, so a feed
    # server that accepted the connection and never answered blocked poll_feeds_job
    # forever. A timeout must now surface as a clean "no content" result.
    def timing_out_stream(*_args, **_kwargs):
        raise httpx.ReadTimeout("no response")

    monkeypatch.setattr(feed_ingest.httpx, "stream", timing_out_stream)

    assert feed_ingest._fetch_feed_content("https://example.test/feed.xml") is None


def test_fetch_passes_an_explicit_timeout_to_httpx(monkeypatch):
    seen = {}

    def capturing_stream(method, url, **kwargs):
        seen.update(kwargs)
        raise httpx.ConnectError("stop here")

    monkeypatch.setattr(feed_ingest.httpx, "stream", capturing_stream)

    feed_ingest._fetch_feed_content("https://example.test/feed.xml")

    assert seen["timeout"] is not None
    assert seen["follow_redirects"] is True


def test_fetch_returns_none_on_http_error_status(monkeypatch):
    @contextmanager
    def failing_stream(method, url, **kwargs):
        request = httpx.Request(method, url)
        yield httpx.Response(503, request=request)

    monkeypatch.setattr(feed_ingest.httpx, "stream", failing_stream)

    assert feed_ingest._fetch_feed_content("https://example.test/feed.xml") is None


def test_fetch_returns_the_body_on_success(monkeypatch):
    @contextmanager
    def ok_stream(method, url, **kwargs):
        request = httpx.Request(method, url)
        yield httpx.Response(200, content=b"<rss/>", request=request)

    monkeypatch.setattr(feed_ingest.httpx, "stream", ok_stream)

    assert feed_ingest._fetch_feed_content("https://example.test/feed.xml") == b"<rss/>"


def test_poll_feed_returns_zero_when_the_feed_cannot_be_fetched(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    feed = Feed(title="Feed", original_rss_url="https://example.test/feed.xml")
    session.add(feed)
    session.commit()
    session.refresh(feed)

    monkeypatch.setattr(feed_ingest, "_fetch_feed_content", lambda _url: None)

    assert feed_ingest.poll_feed(session, feed) == 0
