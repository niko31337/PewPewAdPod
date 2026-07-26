import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.models import Feed
from app.routers import opml
from app.services.opml_import import OpmlPodcast, parse_opml

SAMPLE_OPML = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>Subscriptions</title></head>
  <body>
    <outline text="Folder" title="Folder">
      <outline text="Show A" title="Show A" type="rss" xmlUrl="https://a.example/feed.xml" htmlUrl="https://a.example"/>
    </outline>
    <outline text="Show B" title="Show B" type="rss" xmlUrl="https://b.example/feed.xml"/>
    <outline text="Show B dup" title="Show B dup" type="rss" xmlUrl="https://b.example/feed.xml"/>
    <outline text="No Feed Here" title="No Feed Here"/>
  </body>
</opml>
"""


def test_parse_opml_walks_nested_folders_and_dedupes_by_url():
    podcasts = parse_opml(SAMPLE_OPML)

    assert podcasts == [
        OpmlPodcast(title="Show A", xml_url="https://a.example/feed.xml"),
        OpmlPodcast(title="Show B", xml_url="https://b.example/feed.xml"),
    ]


def test_parse_opml_falls_back_to_text_or_url_when_title_missing():
    content = b"""<opml><body>
        <outline text="Only text attr" type="rss" xmlUrl="https://c.example/feed.xml"/>
        <outline type="rss" xmlUrl="https://d.example/feed.xml"/>
    </body></opml>"""

    podcasts = parse_opml(content)

    assert podcasts == [
        OpmlPodcast(title="Only text attr", xml_url="https://c.example/feed.xml"),
        OpmlPodcast(title="https://d.example/feed.xml", xml_url="https://d.example/feed.xml"),
    ]


def test_parse_opml_returns_empty_list_when_body_missing():
    assert parse_opml(b"<opml><head></head></opml>") == []


def test_parse_opml_raises_on_invalid_xml():
    with pytest.raises(ET.ParseError):
        parse_opml(b"this is not xml at all <<<")


def _make_client():
    # StaticPool: the FastAPI dependency, the TestClient's request thread, and this
    # test's own setup/assertion code each open their own connection - a plain
    # in-memory sqlite:// DB is per-connection, so without a shared pool each of those
    # would see a separate, empty database.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(opml.router)

    def _override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app), engine


def test_opml_import_preview_marks_already_subscribed_feeds_unchecked(monkeypatch):
    client, engine = _make_client()
    with Session(engine) as session:
        session.add(Feed(title="Show B", original_rss_url="https://b.example/feed.xml"))
        session.commit()

    response = client.post(
        "/opml/import", files={"file": ("subs.opml", SAMPLE_OPML, "text/xml")}
    )

    assert response.status_code == 200
    rows = response.text.split("<tr>")
    show_a_row = next(r for r in rows if "https://a.example/feed.xml" in r)
    show_b_row = next(r for r in rows if "https://b.example/feed.xml" in r)
    # Show A (not yet subscribed) should be preselected; Show B (already subscribed) not.
    assert "checked" in show_a_row
    assert "checked" not in show_b_row
    assert "bereits abonniert" in show_b_row


def test_opml_import_preview_shows_error_for_invalid_file():
    client, _ = _make_client()

    response = client.post(
        "/opml/import", files={"file": ("subs.opml", b"not xml <<<", "text/xml")}
    )

    assert response.status_code == 400
    assert "konnte nicht als OPML gelesen werden" in response.text


def test_opml_import_confirm_creates_only_checked_feeds(monkeypatch):
    client, engine = _make_client()
    monkeypatch.setattr(opml.feed_ingest, "poll_feed", lambda session, feed: None)

    response = client.post(
        "/opml/import/confirm",
        data={
            "import_0": "1",
            "url_0": "https://a.example/feed.xml",
            "title_0": "Show A",
            # import_1 intentionally omitted - simulates an unchecked checkbox
            "url_1": "https://b.example/feed.xml",
            "title_1": "Show B",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?opml_imported=1"
    with Session(engine) as session:
        feeds = session.exec(select(Feed)).all()
    assert [f.original_rss_url for f in feeds] == ["https://a.example/feed.xml"]


def test_opml_import_confirm_skips_urls_already_subscribed_even_if_checked(monkeypatch):
    client, engine = _make_client()
    monkeypatch.setattr(opml.feed_ingest, "poll_feed", lambda session, feed: None)
    with Session(engine) as session:
        session.add(Feed(title="Show B", original_rss_url="https://b.example/feed.xml"))
        session.commit()

    response = client.post(
        "/opml/import/confirm",
        data={"import_0": "1", "url_0": "https://b.example/feed.xml", "title_0": "Show B"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?opml_imported=0"
    with Session(engine) as session:
        feeds = session.exec(select(Feed)).all()
    assert len(feeds) == 1  # still just the pre-existing one, no duplicate created
