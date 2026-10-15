import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.news.fetchers import parse_rss
from app.news.models import Asset, NewsItem, SourceConfig
from app.news.symbols import SymbolDetector, keywords_from_name

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Bitcoin and Nvidia rally together</title><link>https://t.test/1</link>
<pubDate>Thu, 15 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Inditex beats forecasts</title><link>https://t.test/2</link>
<pubDate>Thu, 15 Oct 2026 12:05:00 GMT</pubDate></item>
<item><title>Fed holds rates</title><link>https://t.test/3</link>
<pubDate>Thu, 15 Oct 2026 12:10:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_items_carry_assets_types_and_source():
    src = SourceConfig(id="cnbc", name="CNBC", url="https://t.test/rss")
    items = {i.title: i for i in parse_rss(RSS, src, SymbolDetector())}
    mixed = items["Bitcoin and Nvidia rally together"]
    assert mixed.source == "CNBC" and mixed.source_id == "cnbc"
    assert mixed.symbols == ["BTCUSDT", "NVDA"]
    assert mixed.assets == [Asset(symbol="BTCUSDT", type="crypto", name="Bitcoin"),
                            Asset(symbol="NVDA", type="stock", name="NVIDIA")]
    assert mixed.asset_types == ["crypto", "stock"]
    assert items["Inditex beats forecasts"].asset_types == ["stock"]
    plain = items["Fed holds rates"]
    assert plain.symbols == [] and plain.assets == [] and plain.asset_types == []


def test_detector_learns_watchlist_assets():
    assert keywords_from_name("Iberdrola, S.A.") == ["iberdrola"]
    assert keywords_from_name("NVIDIA Corporation") == ["nvidia"]
    d = SymbolDetector()
    assert d.detect("Iberdrola shares jump") == []
    d.add("IBE.MC", "stock", "Iberdrola, S.A.")
    d.add("PEPEUSDT", "crypto", "PEPEUSDT")
    assert d.detect("Iberdrola shares jump") == ["IBE.MC"]
    assert d.assets(["IBE.MC"]) == [Asset(symbol="IBE.MC", type="stock", name="Iberdrola, S.A.")]
    assert d.detect("PEPE memecoin surges") == ["PEPEUSDT"]
    d.add("AAPL", "stock", "Something Else")  # hand-written defaults are kept
    assert d.assets(["AAPL"])[0].name == "Apple"


def test_news_endpoint_filters_by_type_and_source():
    base = datetime(2026, 10, 15, tzinfo=timezone.utc)

    def item(i, types, source_id):
        return NewsItem(id=f"t{i}", title=f"Typed {i}", source=source_id.upper(), source_id=source_id,
                        url=f"https://t.test/t{i}", published_at=base + timedelta(minutes=i), asset_types=types)

    items = [item(0, ["crypto"], "ct"), item(1, ["stock"], "cnbc"), item(2, ["crypto", "stock"], "cnbc"), item(3, [], "cnbc")]
    with TestClient(app) as client:
        client.portal.call(app.state.news_store.add_many, items, False)
        ids = lambda **p: [i["id"] for i in client.get("/api/news", params={"limit": 100, **p}).json()["items"]]
        assert ids(type="crypto") == ["t2", "t0"]
        assert ids(type="stock") == ["t2", "t1"]
        assert ids(source="cnbc") == ["t3", "t2", "t1"]
        assert ids(type="crypto", source="cnbc") == ["t2"]
        assert client.get("/api/news", params={"type": "bond"}).status_code == 422
        body = client.get("/api/news", params={"limit": 1}).json()["items"][0]
        assert {"source", "source_id", "symbols", "assets", "asset_types"} <= body.keys()


def test_websocket_news_messages_include_assets():
    item = NewsItem(id="ws-a", title="ETH update", source="Cointelegraph", source_id="cointelegraph",
                    url="https://t.test/ws-a", published_at=datetime.now(timezone.utc), symbols=["ETHUSDT"],
                    assets=[Asset(symbol="ETHUSDT", type="crypto", name="Ethereum")], asset_types=["crypto"])
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.portal.call(app.state.news_store.add_many, [item])
        msg = ws.receive_json()
        assert msg["type"] == "news"
        assert msg["data"]["assets"] == [{"symbol": "ETHUSDT", "type": "crypto", "name": "Ethereum"}]
        assert msg["data"]["asset_types"] == ["crypto"] and msg["data"]["source_id"] == "cointelegraph"


def test_watchlist_items_include_type():
    with TestClient(app) as client:
        items = client.get("/api/watchlist").json()["items"]
        assert items and all(i["type"] in ("crypto", "stock") for i in items)
        assert {i["type"] for i in items} == {"crypto", "stock"}
