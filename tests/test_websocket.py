import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.news.models import NewsItem


def _item(i: int) -> NewsItem:
    return NewsItem(id=f"w{i}", title=f"WS story {i}", source="T", url=f"https://t.test/w{i}",
                    published_at=datetime(2026, 10, 15, 12, i, tzinfo=timezone.utc))


def test_snapshot_then_live_news_push():
    with TestClient(app) as client:
        client.portal.call(app.state.news_store.add_many, [_item(1)], False)
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["data"]["watchlist"] == []
            assert [n["id"] for n in snap["data"]["news"]] == ["w1"]

            client.portal.call(app.state.news_store.add_many, [_item(2)])
            msg = ws.receive_json()
            assert msg["type"] == "news" and msg["data"]["id"] == "w2"
            assert msg["data"]["published_at"] == "2026-10-15T12:02:00Z"


def test_ping_pong_and_invalid_messages():
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "subscribe_candles", "data": {"symbol": "BTCUSDT", "interval": "5m"}})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "whatever"})
        assert ws.receive_json()["type"] == "error"


def test_candle_messages_only_reach_subscribers():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
            a.receive_json(); b.receive_json()
            a.send_json({"type": "subscribe_candles", "data": {"symbol": "btcusdt", "interval": "1m"}})
            a.send_json({"type": "ping"}); assert a.receive_json()["type"] == "pong"  # subscription processed
            candle = {"time": 1760536800, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}
            client.portal.call(app.state.hub.broadcast_candle, "BTCUSDT", "1m", candle)
            got = a.receive_json()
            assert got == {"type": "candle", "data": {"symbol": "BTCUSDT", "interval": "1m", "candle": candle}}
            b.send_json({"type": "ping"})
            assert b.receive_json()["type"] == "pong"  # b got no candle before its pong


def test_disconnect_cleans_up():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert app.state.hub.count == 1
        client.get("/health")
        assert app.state.hub.count == 0


def test_cors_production_origin(monkeypatch):
    from app.config import Settings
    s = Settings(cors_origins="https://atlas.vercel.app, http://localhost:5173")
    assert s.cors_origin_list == ["https://atlas.vercel.app", "http://localhost:5173"]
