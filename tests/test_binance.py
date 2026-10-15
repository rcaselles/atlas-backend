import asyncio
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.market.binance import BinanceStream, parse_mini_ticker
from app.market.quotes import Quote, QuoteStore


def ticker(symbol="BTCUSDT", close="105.0", open_="100.0", ts=1792074975017):
    return json.dumps({"stream": f"{symbol.lower()}@miniTicker",
                       "data": {"e": "24hrMiniTicker", "E": ts, "s": symbol, "c": close, "o": open_,
                                "h": "110", "l": "95", "v": "1", "q": "1"}})


class FakeWS:
    def __init__(self, messages, then="close"):
        self.messages = list(messages)
        self.then = then

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        if self.then == "hang":
            await asyncio.Event().wait()
        raise ConnectionError("socket closed")


class FakeConnect:
    """Each call returns the next scripted connection; a scripted Exception simulates a failed handshake."""

    def __init__(self, script):
        self.script = list(script)
        self.urls = []

    def __call__(self, url, **kw):
        self.urls.append(url)
        item = self.script.pop(0) if self.script else FakeWS([], then="hang")
        if isinstance(item, Exception):
            raise item
        return item


async def no_sleep(_):
    await asyncio.sleep(0)


def test_parse_mini_ticker():
    q = parse_mini_ticker(json.loads(ticker()))
    assert q.symbol == "BTCUSDT" and q.type == "crypto" and q.name == "Bitcoin"
    assert q.price == 105.0 and q.change_24h_pct == 5.0 and q.delayed is False
    assert q.updated_at == datetime.fromtimestamp(1792074975.017, tz=timezone.utc)
    assert parse_mini_ticker({"data": {"e": "trade"}}) is None
    assert parse_mini_ticker({"data": {"e": "24hrMiniTicker", "s": "X"}}) is None


def test_stream_url_and_quotes_in_memory():
    async def go():
        store = QuoteStore(min_gap=0)
        conn = FakeConnect([FakeWS([ticker("BTCUSDT"), ticker("ETHUSDT", "50", "40"), "garbage", ticker("DOGEUSDT")], then="hang")])
        s = BinanceStream(["ethusdt", "BTCUSDT"], store, urls=["wss://a"], connect=conn, sleep=no_sleep)
        s.start()
        for _ in range(50):
            await asyncio.sleep(0)
        await s.stop()
        return store, conn

    store, conn = asyncio.run(go())
    assert conn.urls[0] == "wss://a/stream?streams=btcusdt@miniTicker/ethusdt@miniTicker"
    assert store.get("BTCUSDT").price == 105.0
    assert store.get("ETHUSDT").change_24h_pct == 25.0
    assert store.get("DOGEUSDT") is None  # not subscribed


def test_reconnects_and_falls_back_to_next_url():
    async def go():
        store = QuoteStore(min_gap=0)
        conn = FakeConnect([OSError("handshake timeout"), FakeWS([ticker(close="1")]), FakeWS([ticker(close="2")], then="hang")])
        s = BinanceStream(["BTCUSDT"], store, urls=["wss://a", "wss://b"], connect=conn, sleep=no_sleep)
        s.start()
        for _ in range(100):
            await asyncio.sleep(0)
        await s.stop()
        return store, conn, s

    store, conn, s = asyncio.run(go())
    assert [u.split("/stream")[0] for u in conn.urls[:3]] == ["wss://a", "wss://b", "wss://b"]
    assert s.connections == 2
    assert store.get("BTCUSDT").price == 2.0


def test_changing_symbols_resubscribes():
    async def go():
        store = QuoteStore(min_gap=0)
        conn = FakeConnect([FakeWS([], then="hang"), FakeWS([ticker("SOLUSDT", "10", "10")], then="hang")])
        s = BinanceStream(["BTCUSDT"], store, urls=["wss://a"], connect=conn, sleep=no_sleep)
        s.start()
        for _ in range(20):
            await asyncio.sleep(0)
        s.set_symbols(["BTCUSDT", "SOLUSDT"])
        for _ in range(50):
            await asyncio.sleep(0)
        await s.stop()
        return store, conn

    store, conn = asyncio.run(go())
    assert len(conn.urls) == 2 and "solusdt@miniTicker" in conn.urls[1]
    assert store.get("SOLUSDT").price == 10.0


def test_price_pushes_are_throttled_per_symbol():
    t = [0.0]
    store = QuoteStore(min_gap=1.0, clock=lambda: t[0])
    pushed = []

    async def listener(q):
        pushed.append(q.price)

    store.subscribe(listener)

    def q(price, sym="BTCUSDT"):
        return Quote(symbol=sym, type="crypto", name=sym, price=price, change_24h_pct=0, updated_at=datetime.now(timezone.utc))

    async def go():
        await store.update(q(1)); await store.update(q(2)); await store.update(q(9, "ETHUSDT"))
        t[0] = 1.5
        await store.update(q(3))

    asyncio.run(go())
    assert pushed == [1, 9, 3]
    assert store.get("BTCUSDT").price == 3  # latest value is always kept


def test_price_reaches_websocket_clients_and_snapshot():
    with TestClient(app) as client:
        quote = parse_mini_ticker(json.loads(ticker(close="110", open_="100")))
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.portal.call(app.state.quotes.update, quote)
            msg = ws.receive_json()
            assert msg["type"] == "price"
            assert msg["data"]["symbol"] == "BTCUSDT" and msg["data"]["price"] == 110.0
            assert msg["data"]["change_24h_pct"] == 10.0 and msg["data"]["delayed"] is False
        with client.websocket_connect("/ws") as ws2:
            snap = ws2.receive_json()
            assert snap["data"]["watchlist"][0]["symbol"] == "BTCUSDT"
            assert snap["data"]["watchlist"][0]["name"] == "Bitcoin"
