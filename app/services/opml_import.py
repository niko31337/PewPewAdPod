import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class OpmlPodcast:
    title: str
    xml_url: str


def parse_opml(content: bytes) -> list[OpmlPodcast]:
    """Extracts every podcast feed listed in an OPML export (e.g. from Apple Podcasts,
    Overcast, Pocket Casts). Outlines can be nested inside folder outlines, so this walks
    the whole tree rather than assuming a flat list. Entries without an xmlUrl attribute
    (folder outlines themselves) are skipped. Duplicate xmlUrls within the same file are
    collapsed to their first occurrence. Raises xml.etree.ElementTree.ParseError for
    content that isn't well-formed XML at all."""
    root = ET.fromstring(content)
    body = root.find("body")
    if body is None:
        return []

    seen: set[str] = set()
    podcasts: list[OpmlPodcast] = []

    def walk(outline: ET.Element) -> None:
        xml_url = outline.get("xmlUrl")
        if xml_url and xml_url not in seen:
            seen.add(xml_url)
            title = outline.get("title") or outline.get("text") or xml_url
            podcasts.append(OpmlPodcast(title=title, xml_url=xml_url))
        for child in outline.findall("outline"):
            walk(child)

    for outline in body.findall("outline"):
        walk(outline)

    return podcasts
