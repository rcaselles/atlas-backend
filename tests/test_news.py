import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.news.aggregator import NewsAggregator, load_sources
from app.news.fetchers import parse_html
from app.news.models import NewsItem, SourceConfig
from app.news.normalize import item_id, normalize_url
from app.news.store import NewsStore
from app.news.symbols import SymbolDetector

FIX = Path(__file__).parent / "fixtures"

HTML_PAGE = """
<html><body>
  <article class="story"><h2><a href="/markets/bitcoin-rallies">Bitcoin rallies past resistance</a></h2>
    <time datetime="2026-10-15T12:00:00Z"></time><p class="dek">BTC climbs.</p></article>
  <article class="story"><h2><a href="https://other.example/apple">Apple unveils new chip</a></h2></article>
</body></html>
"""


def make_transport(routes: dict[str, tuple[int, bytes | str]]):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        status, body = routes.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode())

    return httpx.MockTransport(handler), calls


def rss_sources():
    return [
        SourceConfig(id="cnbc", name="CNBC", url="https://feeds.test/cnbc"),
        SourceConfig(id="ct", name="Cointelegraph", url="https://feeds.test/ct"),
        SourceConfig(id="down", name="Down", url="https://feeds.test/down"),
    ]


def run(coro):
    return asyncio.run(coro)


def test_rss_sources_parse_and_broken_source_is_isolated():
    transport, _ = make_transport(
        {
            "https://feeds.test/cnbc": (200, (FIX / "cnbc.xml").read_bytes()),
            "https://feeds.test/ct": (200, (FIX / "cointelegraph.xml").read_bytes()),
            "https://feeds.test/down": (503, b"oops"),
        }
    )
    store = NewsStore()

    async def go():
        agg = NewsAggregator(rss_sources(), store, client=httpx.AsyncClient(transport=transport))
        added = await agg.poll_all()
        await agg.stop()
        return agg, added

    agg, added = run(go())
    assert added == 6
    assert agg.status["cnbc"].ok and agg.status["ct"].ok
    assert agg.status["down"].ok is False and "503" in agg.status["down"].last_error
    item = store.page(limit=1)[0][0]
    assert item.title and item.url.startswith("http") and item.published_at.tzinfo is not None


def test_duplicates_are_dropped_across_polls_and_by_title():
    transport, _ = make_transport({"https://feeds.test/cnbc": (200, (FIX / "cnbc.xml").read_bytes())})
    store = NewsStore()
    src = [SourceConfig(id="cnbc", name="CNBC", url="https://feeds.test/cnbc")]

    async def go():
        agg = NewsAggregator(src, store, client=httpx.AsyncClient(transport=transport))
        first = await agg.poll_all()
        second = await agg.poll_all()
        await agg.stop()
        return first, second

    first, second = run(go())
    assert first == 3 and second == 0
    now = datetime.now(timezone.utc)
    same_title = NewsItem(id="x", title=store.page(1)[0][0].title.upper(), source="Other", url="https://x.test/a", published_at=now)
    assert run(store.add_many([same_title])) == []


def test_tracking_params_do_not_create_duplicates():
    a = "https://site.test/story/?utm_source=rss&id=7"
    b = "https://SITE.test/story?id=7"
    assert normalize_url(a) == normalize_url(b)
    assert item_id(a) == item_id(b)


def test_html_scraping_respects_robots():
    src = SourceConfig(
        id="h", name="Scraped", type="html", list_url="https://news.test/markets",
        item_selector="article.story", title_selector="h2", link_selector="a[href]",
        time_selector="time", summary_selector="p.dek",
    )
    allowed, calls = make_transport({
        "https://news.test/robots.txt": (200, "User-agent: *\nDisallow: /private\n"),
        "https://news.test/markets": (200, HTML_PAGE),
    })
    blocked, blocked_calls = make_transport({
        "https://news.test/robots.txt": (200, "User-agent: *\nDisallow: /\n"),
        "https://news.test/markets": (200, HTML_PAGE),
    })

    async def go(transport):
        store = NewsStore()
        agg = NewsAggregator([src], store, client=httpx.AsyncClient(transport=transport))
        n = await agg.poll_all()
        await agg.stop()
        return n, agg, store

    n, agg, store = run(go(allowed))
    assert n == 2
    items = {i.title: i for i in store.page(10)[0]}
    btc = items["Bitcoin rallies past resistance"]
    assert btc.url == "https://news.test/markets/bitcoin-rallies"
    assert btc.symbols == ["BTCUSDT"]
    assert btc.published_at == datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    n, agg, _ = run(go(blocked))
    assert n == 0 and "robots" in agg.status["h"].last_error
    assert "https://news.test/markets" not in blocked_calls


def test_symbol_detection():
    d = SymbolDetector()
    assert d.detect("Nvidia and Apple lead gains", None) == ["AAPL", "NVDA"]
    assert d.detect("Metaverse hype fades") == []


def test_sources_file_is_valid_and_enforces_min_interval(tmp_path):
    srcs = load_sources("config/sources.yaml")
    assert len(srcs) >= 3 and all(s.interval >= 60 for s in srcs)
    f = tmp_path / "s.yaml"
    f.write_text("sources:\n  - {id: a, name: A, url: 'https://a', interval: 5}\n  - {id: b, name: B, url: 'https://b', enabled: false}\n")
    out = load_sources(f)
    assert [s.id for s in out] == ["a"] and out[0].interval == 60


def test_news_endpoint_paginates_and_filters():
    base = datetime(2026, 10, 15, tzinfo=timezone.utc)
    items = [
        NewsItem(id=f"n{i}", title=f"Story {i}", source="T", url=f"https://t.test/{i}",
                 published_at=base + timedelta(minutes=i), symbols=["AAPL"] if i % 2 else [])
        for i in range(25)
    ]
    with TestClient(app) as client:
        run(app.state.news_store.add_many(items, notify=False))
        p1 = client.get("/api/news", params={"limit": 10}).json()
        assert [i["id"] for i in p1["items"]][:2] == ["n24", "n23"] and len(p1["items"]) == 10
        p2 = client.get("/api/news", params={"limit": 10, "cursor": p1["next_cursor"]}).json()
        assert p2["items"][0]["id"] == "n14"
        p3 = client.get("/api/news", params={"limit": 10, "cursor": p2["next_cursor"]}).json()
        assert len(p3["items"]) == 5 and p3["next_cursor"] is None
        f = client.get("/api/news", params={"symbol": "aapl", "limit": 100}).json()
        assert len(f["items"]) == 12
        bad = client.get("/api/news", params={"cursor": "nope"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_cursor"
        assert client.get("/api/news", params={"limit": 0}).status_code == 422
