import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ApiError
from app.main import app
from app.market.candles import CandleService, parse_klines, parse_yahoo_candles
from app.market.quotes import Quote
from app.market.yahoo import YahooClient

FIX = Path(__file__).parent / "fixtures"
KLINES = json.loads((FIX / "binance_klines_btc_1h.json").read_text())
YAHOO = json.loads((FIX / "yahoo_aapl_1d_candles.json").read_text())
TYPES = {"BTCUSDT": "crypto", "AAPL": "stock"}


def transport(calls):
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.path.endswith("/klines"):
            return httpx.Response(200, json=KLINES)
        if "/chart/AAPL" in req.url.path:
            return httpx.Response(200, json=YAHOO)
        return httpx.Response(500)
    return httpx.MockTransport(handler)


def service(calls, t=None):
    t = t or [0.0]
    http = httpx.AsyncClient(transport=transport(calls))
    return CandleService(http, YahooClient(http), TYPES.get, clock=lambda: t[0]), t


def test_parse_binance_klines():
    c = parse_klines(KLINES)
    assert len(c) == 5
    assert c[0].time == KLINES[0][0] // 1000 and c[0].open == float(KLINES[0][1]) and c[0].volume == float(KLINES[0][5])
    assert [x.time for x in c] == sorted(x.time for x in c)


def test_parse_yahoo_candles_skips_gaps_and_sorts():
    c = parse_yahoo_candles(YAHOO)
    ts = YAHOO["chart"]["result"][0]["timestamp"]
    assert len(c) == len(ts) - 1  # the null close is skipped
    assert ts[2] not in [x.time for x in c]
    assert [x.time for x in c] == sorted({x.time for x in c})


def test_history_for_crypto_and_stock_with_cache_and_limit():
    calls = []
    svc, t = service(calls)

    async def go():
        btc = await svc.history("btcusdt", "1h", limit=3)
        await svc.history("BTCUSDT", "1h")  # served from cache
        aapl = await svc.history("AAPL", "1d")
        t[0] = 301
        await svc.history("BTCUSDT", "1h")  # 1h cache expired after 5 min
        return btc, aapl

    btc, aapl = asyncio.run(go())
    assert len(btc) == 3 and btc[-1].time == KLINES[-1][0] // 1000
    assert aapl and all(c.close is not None for c in aapl)
    assert sum("klines" in c for c in calls) == 2
    assert any("interval=1d" in c and "range=2y" in c for c in calls)


def test_history_unknown_symbol_and_upstream_error():
    svc, _ = service([])

    async def go():
        with pytest.raises(ApiError) as e:
            await svc.history("DOGEUSDT", "1h")
        assert e.value.status == 404

    asyncio.run(go())
    bad = CandleService(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
                        YahooClient(), TYPES.get)

    async def go2():
        with pytest.raises(ApiError) as e:
            await bad.history("BTCUSDT", "1m")
        assert e.value.status == 502 and e.value.code == "upstream_error"

    asyncio.run(go2())


def q(price, ts):
    return Quote(symbol="BTCUSDT", type="crypto", name="Bitcoin", price=price, change_24h_pct=0,
                 updated_at=datetime.fromtimestamp(ts, tz=timezone.utc))


def test_live_candle_updates_from_prices():
    svc, _ = service([])
    last = parse_klines(KLINES)[-1]
    assert svc.apply_quote(q(1, last.time), "1h") is None  # nothing until history is loaded
    asyncio.run(svc.history("BTCUSDT", "1h"))
    up = svc.apply_quote(q(last.high + 100, last.time + 10), "1h")
    assert up.time == last.time and up.high == last.high + 100 and up.close == last.high + 100 and up.open == last.open
    down = svc.apply_quote(q(last.low - 50, last.time + 20), "1h")
    assert down.low == last.low - 50 and down.high == last.high + 100
    new = svc.apply_quote(q(123.0, last.time + 3600 + 5), "1h")
    assert new.time == last.time + 3600 and new.open == new.close == 123.0
    assert svc.apply_quote(q(1, last.time), "1h") is None  # out-of-order price ignored


def test_candles_endpoint_and_websocket_push():
    with TestClient(app) as client:
        calls = []
        http = httpx.AsyncClient(transport=transport(calls))
        app.state.candles.http = http
        app.state.candles.yahoo = YahooClient(http)
        r = client.get("/api/candles/BTCUSDT", params={"interval": "1h", "limit": 2})
        body = r.json()
        assert r.status_code == 200 and body["symbol"] == "BTCUSDT" and body["interval"] == "1h"
        assert len(body["candles"]) == 2 and set(body["candles"][0]) == {"time", "open", "high", "low", "close", "volume"}
        assert client.get("/api/candles/AAPL", params={"interval": "1d"}).status_code == 200
        assert client.get("/api/candles/BTCUSDT", params={"interval": "5m"}).status_code == 422
        assert client.get("/api/candles/ZZZ").json()["error"]["code"] == "not_found"

        last = body["candles"][-1]
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe_candles", "data": {"symbol": "BTCUSDT", "interval": "1h"}})
            assert ws.receive_json()["data"]["candle"]["time"] == last["time"]  # current candle on subscribe
            ws.send_json({"type": "ping"}); assert ws.receive_json()["type"] == "pong"
            client.portal.call(app.state.quotes.update, q(last["high"] + 1, last["time"] + 30))
            msgs = [ws.receive_json(), ws.receive_json()]
            candle = next(m for m in msgs if m["type"] == "candle")
            assert candle["data"]["symbol"] == "BTCUSDT" and candle["data"]["interval"] == "1h"
            assert candle["data"]["candle"]["time"] == last["time"] and candle["data"]["candle"]["close"] == last["high"] + 1
            assert any(m["type"] == "price" for m in msgs)


def test_yahoo_live_point_is_snapped_to_grid():
    base = json.loads(json.dumps(YAHOO))
    r = base["chart"]["result"][0]
    q0 = r["indicators"]["quote"][0]
    r["timestamp"] = [1000, 4600, 4600 + 1234]  # last = live point inside the 4600 candle
    for k, vals in {"open": [1, 2, 3], "high": [1, 2, 9], "low": [1, 2, 0.5], "close": [1, 2, 3], "volume": [1, 1, 0]}.items():
        q0[k] = vals
    merged = parse_yahoo_candles(base, 3600)
    assert [c.time for c in merged] == [1000, 4600] and merged[-1].high == 9 and merged[-1].low == 0.5 and merged[-1].close == 3
    r["timestamp"] = [1000, 4600, 4600 + 3600 + 77]  # live point opens a new candle
    snapped = parse_yahoo_candles(base, 3600)
    assert [c.time for c in snapped] == [1000, 4600, 8200]


def test_refresh_brings_real_volume_and_new_candles():
    calls = []
    svc, _ = service(calls)
    asyncio.run(svc.history("BTCUSDT", "1h"))
    last = parse_klines(KLINES)[-1]
    svc.apply_quote(q(last.high + 500, last.time + 10), "1h")  # live extreme not yet in the source
    fresh = [list(KLINES[-1]), list(KLINES[-1])]
    fresh[0][5] = "999.5"                       # same candle, updated volume
    fresh[1][0] = KLINES[-1][0] + 3_600_000     # next candle just opened
    fresh[1][5] = "1.25"
    svc.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=fresh)))
    changed = asyncio.run(svc.refresh("BTCUSDT", "1h"))
    assert [c.time for c in changed] == [last.time, last.time + 3600]
    assert changed[0].volume == 999.5 and changed[0].high == last.high + 500
    assert svc.live("BTCUSDT", "1h").volume == 1.25
    hist = asyncio.run(svc.history("BTCUSDT", "1h"))  # cache updated too
    assert hist[-1].time == last.time + 3600 and hist[-2].volume == 999.5
    # live price keeps the refreshed volume
    assert svc.apply_quote(q(1.0, last.time + 3600 + 30), "1h").volume == 1.25


def test_refresher_loop_pushes_to_subscribers():
    svc, t = service([])
    pushed = []

    class FakeHub:
        def candle_subscriptions(self):
            return {("BTCUSDT", "1m"), ("AAPL", "1d"), ("ZZZ", "1h")}

        async def broadcast_candle(self, symbol, interval, candle):
            pushed.append((symbol, interval))

    async def go():
        task = asyncio.create_task(svc.run_refresher(FakeHub(), tick=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert ("BTCUSDT", "1m") in pushed and ("AAPL", "1d") in pushed
    assert pushed.count(("AAPL", "1d")) == 1  # stocks refresh at most once a minute (clock frozen)


def test_subscribe_without_history_sends_current_candle():
    with TestClient(app) as client:
        http = httpx.AsyncClient(transport=transport([]))
        app.state.candles = CandleService(http, YahooClient(http), app.state.candles.type_of)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe_candles", "data": {"symbol": "btcusdt", "interval": "1h"}})
            msg = ws.receive_json()
            assert msg["type"] == "candle" and msg["data"]["symbol"] == "BTCUSDT"
            assert msg["data"]["candle"]["time"] == KLINES[-1][0] // 1000
            assert msg["data"]["candle"]["volume"] == float(KLINES[-1][5])
            ws.send_json({"type": "subscribe_candles", "data": {"symbol": "ZZZ", "interval": "1h"}})
            err = ws.receive_json()
            assert err["type"] == "error" and err["data"]["code"] == "not_found"


def test_refresh_fixes_closed_candle_in_cache_without_pushing():
    svc, _ = service([])
    asyncio.run(svc.history("BTCUSDT", "1h"))
    last = parse_klines(KLINES)[-1]
    svc.apply_quote(q(5.0, last.time + 3600 + 1), "1h")  # a price already opened the next candle
    closed = list(KLINES[-1]); closed[5] = "777.0"
    svc.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[closed])))
    assert asyncio.run(svc.refresh("BTCUSDT", "1h")) == []
    hist = asyncio.run(svc.history("BTCUSDT", "1h"))
    assert hist[-1].time == last.time and hist[-1].volume == 777.0
