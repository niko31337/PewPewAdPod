from datetime import timezone

from feedgen.feed import FeedGenerator

from app.models import Episode, EpisodeStatus, Feed
from app.paths import cross_platform_basename


def build_feed_xml(feed: Feed, episodes: list[Episode], base_url: str) -> bytes:
    if not base_url.endswith("/"):
        base_url += "/"

    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.id(f"{base_url}feed/{feed.id}.xml")
    fg.title(feed.title)
    fg.link(href=f"{base_url}feed/{feed.id}.xml", rel="self")
    fg.description(feed.description or feed.title)
    fg.language(feed.language or "de")

    cover_url = None
    if feed.cover_image_local_path:
        cover_url = f"{base_url}media/covers/{feed.id}/cover.jpg"
        fg.image(url=cover_url)
        fg.podcast.itunes_image(cover_url)
    if feed.author:
        fg.podcast.itunes_author(feed.author)

    published = [e for e in episodes if e.status == EpisodeStatus.PUBLISHED and e.processed_audio_path]
    published.sort(key=lambda e: e.pubdate or e.created_at, reverse=True)

    for ep in published:
        fe = fg.add_entry()
        fe.id(ep.guid)
        fe.guid(ep.guid, permalink=False)
        fe.title(ep.title)
        fe.description(ep.description or "")
        pubdate = ep.pubdate or ep.created_at
        if pubdate.tzinfo is None:
            pubdate = pubdate.replace(tzinfo=timezone.utc)
        fe.pubDate(pubdate)

        # Use the actual processed-file's name rather than assuming "{id}.mp3" - an
        # applied correction points processed_audio_path at "{id}_correction.mp3" instead,
        # and the enclosure must match what's really on disk (and its real size).
        audio_url = f"{base_url}media/audio/processed/{cross_platform_basename(ep.processed_audio_path)}"
        size = ep.processed_size_bytes or 0
        fe.enclosure(audio_url, str(size), "audio/mpeg")

        ep_image = None
        if ep.local_image_path:
            ep_image = f"{base_url}media/covers/{feed.id}/episodes/{ep.id}.jpg"
        elif cover_url:
            ep_image = cover_url
        if ep_image:
            fe.podcast.itunes_image(ep_image)

        if ep.itunes_duration:
            fe.podcast.itunes_duration(ep.itunes_duration)
        if ep.itunes_season:
            fe.podcast.itunes_season(ep.itunes_season)
        if ep.itunes_episode:
            fe.podcast.itunes_episode(ep.itunes_episode)

    return fg.rss_str(pretty=True)


def build_master_feed_xml(
    entries: list[tuple[Feed, Episode, str]], base_url: str, master_cover_url: str
) -> bytes:
    """entries: (feed, episode, episode_image_url) tuples, one per source feed's latest
    published episode - already resolved by the caller (incl. watermarking fallback)."""
    if not base_url.endswith("/"):
        base_url += "/"

    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.id(f"{base_url}feed/master.xml")
    fg.title("Nikos pewpewadpod Feed")
    fg.link(href=f"{base_url}feed/master.xml", rel="self")
    fg.description(
        "Die jeweils neueste, werbefreie Episode aus allen bei PewPewAdPod hinterlegten Podcasts."
    )
    fg.language("de")
    fg.image(url=master_cover_url)
    fg.podcast.itunes_image(master_cover_url)
    fg.podcast.itunes_author("PewPewAdPod")

    sorted_entries = sorted(entries, key=lambda t: t[1].pubdate or t[1].created_at, reverse=True)

    for feed, ep, image_url in sorted_entries:
        fe = fg.add_entry()
        fe.id(ep.guid)
        fe.guid(ep.guid, permalink=False)
        fe.title(f"{ep.title} — {feed.title}")
        fe.description(ep.description or "")
        pubdate = ep.pubdate or ep.created_at
        if pubdate.tzinfo is None:
            pubdate = pubdate.replace(tzinfo=timezone.utc)
        fe.pubDate(pubdate)

        audio_url = f"{base_url}media/audio/processed/{cross_platform_basename(ep.processed_audio_path)}"
        size = ep.processed_size_bytes or 0
        fe.enclosure(audio_url, str(size), "audio/mpeg")

        fe.podcast.itunes_image(image_url)

        if ep.itunes_duration:
            fe.podcast.itunes_duration(ep.itunes_duration)

    return fg.rss_str(pretty=True)
