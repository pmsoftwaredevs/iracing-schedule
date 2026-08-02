"""Deterministic parser: extracts the current season's logo-pack ZIP download links
from iracing.com/resources/logos/ (public, no login). The page has no stable ids for
these — each logo pack is just a "box" (`wp-block-cover__inner-container`) with an
`<h2>` title ("2026 Special Event Logos", "Official Series – Season 2 2026", "iRacing
eSports Logos", ...) followed by a "Download" button linking to the ZIP. Boxes are
matched by title text rather than position, since the page also lists eSports/brand
logo packs we don't want.
"""

import re

from bs4 import BeautifulSoup

HEADING_CLASS = "wp-block-heading"
CONTAINER_CLASS = "wp-block-cover__inner-container"

SPECIAL_EVENTS_HEADING_RE = re.compile(r"special event logos", re.IGNORECASE)
OFFICIAL_SERIES_HEADING_RE = re.compile(r"official series", re.IGNORECASE)
DOWNLOAD_TEXT_RE = re.compile(r"^download$", re.IGNORECASE)


def _download_link(heading) -> str | None:
    container = heading.find_parent("div", class_=CONTAINER_CLASS)
    if container is None:
        return None
    for link in container.find_all("a"):
        if DOWNLOAD_TEXT_RE.match(link.get_text(strip=True)):
            return link.get("href")
    return None


def parse_logos_page(html: str) -> tuple[str | None, str | None]:
    """Returns (special_events_zip_url, championship_zip_url) — either may be None if
    that box isn't found on the page (a layout change, not treated as fatal by callers:
    logos are supplementary, unlike the schedule/events data itself)."""
    soup = BeautifulSoup(html, "html.parser")

    special_events_zip_url = None
    championship_zip_url = None
    for heading in soup.find_all("h2", class_=HEADING_CLASS):
        text = heading.get_text(strip=True)
        if special_events_zip_url is None and SPECIAL_EVENTS_HEADING_RE.search(text):
            special_events_zip_url = _download_link(heading)
        elif championship_zip_url is None and OFFICIAL_SERIES_HEADING_RE.search(text):
            championship_zip_url = _download_link(heading)

    return special_events_zip_url, championship_zip_url
