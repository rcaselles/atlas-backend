import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ApiError
from app.main import app
from app.market.quotes import Quote, QuoteStore
from app.market.stocks import StockPoller
from app.market.yahoo import YahooClient, YahooError, parse_chart_quote
from app.watchlist.service import Validator, Watchlist, WatchItem, parse_default

FIX = Path(__file__).parent / "fixtures"
AAPL = json.loads((FIX / "yahoo_aapl.json").read_text())


def yahoo_transport(calls):
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if "/chart/AAPL" in req.url.path:
            return httpx.Response(200, json=AAPL)
        if "data-api.binance.vision" in req.url.host:
            ok = req.url.params.get("symbol") == "DOGEUSDT"
            return httpx.Response(200 if ok else 400, json={})
        return httpx.Response(404, json={"chart": {"result": None}})
    return httpx.MockTransport(handler)


def test_parse_yahoo_quote_is_marked_delayed():
    q = parse_chart_quote(AAPL)
    meta = AAPL["chart"]["result"][0]["meta"]
    assert q.symbol == "AAPL" and q.type == "stock" and q.delayed is True
    assert q.price == meta["regularMarketPrice"] and q.currency == "USD"
    expected = (meta["regularMarketPrice"] - meta["chartPreviousClose"]) / meta["chartPreviousClose"] * 100
    assert q.change_24h_pct == pytest.approx(expected, abs=1e-3)
    with pytest.raises(YahooError):
        parse_chart_quote({"chart": {"result": []}})


def test_yahoo_cache_limits_requests():
    calls, t = [], [0.0]
    y = YahooClient(httpx.AsyncClient(transport=yahoo_transport(calls)), ttl=60, clock=lambda: t[0])

    async def go():
        await y.quote("aapl"); await y.quote("AAPL")
        t[0] = 61
        await y.quote("AAPL")
        with pytest.raises(YahooError):
            await y.quote("NOPE")

    asyncio.run(go())
    assert sum("/chart/AAPL" in c for c in calls) == 2


def test_stock_poller_isolates_bad_symbols():
    calls = []
    store = QuoteStore(min_gap=0)
    y = YahooClient(httpx.AsyncClient(transport=yahoo_transport(calls)))
    p = StockPoller(y, store, ["AAPL", "NOPE"])
    asyncio.run(p.poll_once())
    assert store.get("AAPL").delayed is True
    assert "NOPE" in p.errors and store.get("NOPE") is None


def make_watchlist(tmp_path=None, calls=None):
    calls = [] if calls is None else calls
    http = httpx.AsyncClient(transport=yahoo_transport(calls))
    store = QuoteStore(min_gap=0)
    wl = Watchlist(tmp_path / "wl.json" if tmp_path else None, parse_default("BTCUSDT:crypto,AAPL:stock"),
                   store, Validator(http, YahooClient(http)))
    return wl, store


def test_watchlist_add_remove_validate_and_persist(tmp_path):
    wl, store = make_watchlist(tmp_path)
    changes = []
    wl.on_change(lambda items: changes.append([i.symbol for i in items]))

    async def go():
        await wl.add(WatchItem(symbol="dogeusdt", type="crypto"))
        for bad, code in [(WatchItem(symbol="FAKEUSDT", type="crypto"), 422),
                          (WatchItem(symbol="NOPE", type="stock"), 422),
                          (WatchItem(symbol="BTCUSDT", type="crypto"), 409),
                          (WatchItem(symbol="bad symbol!", type="stock"), 422)]:
            with pytest.raises(ApiError) as e:
                await wl.add(bad)
            assert e.value.status == code

    asyncio.run(go())
    assert wl.symbols("crypto") == ["BTCUSDT", "DOGEUSDT"]
    wl.remove("aapl")
    with pytest.raises(ApiError) as e:
        wl.remove("AAPL")
    assert e.value.status == 404
    assert changes == [["BTCUSDT", "AAPL", "DOGEUSDT"], ["BTCUSDT", "DOGEUSDT"]]
    # Reload from disk keeps the edited list instead of the defaults
    wl2, _ = make_watchlist(tmp_path)
    assert [i.symbol for i in wl2.items] == ["BTCUSDT", "DOGEUSDT"]


def test_snapshot_has_placeholders_until_first_quote():
    wl, store = make_watchlist()
    snap = wl.snapshot()
    assert snap[0] == {"symbol": "BTCUSDT", "type": "crypto", "name": "Bitcoin", "price": None,
                       "change_24h_pct": None, "updated_at": None, "delayed": False, "currency": None}
    assert snap[1]["delayed"] is True
    asyncio.run(store.update(Quote(symbol="BTCUSDT", type="crypto", name="Bitcoin", price=5,
                                   change_24h_pct=1, updated_at=datetime.now(timezone.utc))))
    assert wl.snapshot()[0]["price"] == 5


def test_watchlist_endpoints():
    with TestClient(app) as client:
        wl = app.state.watchlist
        wl.validator = lambda item: asyncio.sleep(0, result="Test Corp" if item.symbol == "TEST" else None)
        r = client.get("/api/watchlist")
        assert r.status_code == 200 and r.json()["items"][0]["symbol"] == "BTCUSDT"
        r = client.post("/api/watchlist", json={"symbol": "test", "type": "stock"})
        assert r.status_code == 201 and r.json()["items"][-1]["name"] == "Test Corp"
        r = client.post("/api/watchlist", json={"symbol": "TEST", "type": "stock"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
        r = client.post("/api/watchlist", json={"symbol": "ZZZZ", "type": "stock"})
        assert r.status_code == 422
        r = client.post("/api/watchlist", json={"symbol": "X", "type": "bond"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
        r = client.delete("/api/watchlist/TEST")
        assert r.status_code == 200 and all(i["symbol"] != "TEST" for i in r.json()["items"])
        assert client.delete("/api/watchlist/TEST").status_code == 404
