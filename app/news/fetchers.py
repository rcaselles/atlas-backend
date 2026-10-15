from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.news.models import NewsItem, SourceConfig
from app.news.normalize import clean_text, item_id, to_utc
from app.news.robots import RobotsCache
from app.news.symbols import SymbolDetector


class FetchError(Exception):
    pass


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime(*st[:6], tzinfo=timezone.utc)
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                return parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                pass
    return None


def _tag(item: NewsItem, source: SourceConfig, detector: SymbolDetector) -> NewsItem:
    item.source_id = source.id
    item.assets = detector.assets(item.symbols)
    item.asset_types = sorted({a.type for a in item.assets})
    return item


def parse_rss(content: bytes | str, source: SourceConfig, detector: SymbolDetector) -> list[NewsItem]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise FetchError(f"invalid feed: {feed.bozo_exception}")
    items = []
    for e in feed.entries:
        title = clean_text(e.get("title"))
        link = e.get("link")
        if not title or not link:
            continue
        summary = clean_text(e.get("summary"), max_len=400)
        items.append(_tag(
            NewsItem(
                id=item_id(link),
                title=title,
                source=source.name,
                url=link,
                published_at=to_utc(_entry_time(e)),
                summary=summary,
                symbols=detector.detect(title, summary),
            ), source, detector)
        )
    return items


def parse_html(content: str, source: SourceConfig, detector: SymbolDetector) -> list[NewsItem]:
    soup = BeautifulSoup(content, "html.parser")
    items = []
    for node in soup.select(source.item_selector or "article"):
        t = node.select_one(source.title_selector) if source.title_selector else node
        a = node.select_one(source.link_selector or "a[href]")
        if not t or not a or not a.get("href"):
            continue
        title = clean_text(t.get_text(" "))
        if not title:
            continue
        link = urljoin(source.list_url or "", a["href"])
        published = None
        if source.time_selector and (tm := node.select_one(source.time_selector)):
            raw = tm.get("datetime") or tm.get_text(strip=True)
            try:
                published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                published = None
        summary = None
        if source.summary_selector and (s := node.select_one(source.summary_selector)):
            summary = clean_text(s.get_text(" "), max_len=400)
        items.append(_tag(
            NewsItem(
                id=item_id(link),
                title=title,
                source=source.name,
                url=link,
                published_at=to_utc(published),
                summary=summary,
                symbols=detector.detect(title, summary),
            ), source, detector)
        )
    return items


async def fetch_source(
    client: httpx.AsyncClient, source: SourceConfig, robots: RobotsCache, detector: SymbolDetector
) -> list[NewsItem]:
    if source.type == "rss":
        r = await client.get(source.url)
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code}")
        return parse_rss(r.content, source, detector)
    if source.type == "html":
        url = source.list_url
        if not await robots.allowed(url):
            raise FetchError("blocked by robots.txt")
        r = await client.get(url)
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code}")
        return parse_html(r.text, source, detector)
    raise FetchError(f"unknown source type {source.type!r}")
