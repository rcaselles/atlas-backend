"""OHLC candle history (REST) and live candle updates built from the quote stream."""
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from app.errors import ApiError
from app.market.quotes import Quote
from app.market.yahoo import YahooClient, YahooError

Interval = Literal["1m", "1h", "1d"]
SECONDS = {"1m": 60, "1h": 3600, "1d": 86400}
# How long a fetched history stays fresh before asking the source again
CACHE_TTL = {"1m": 30, "1h": 300, "1d": 1800}
# Yahoo needs a range big enough to cover `limit` candles for each interval
YAHOO_RANGE = {"1m": "5d", "1h": "3mo", "1d": "2y"}
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"


class Candle(BaseModel):
    time: int  # Unix seconds, candle open time (Lightweight Charts UTCTimestamp)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def parse_klines(rows: list) -> list[Candle]:
    return [Candle(time=int(r[0]) // 1000, open=float(r[1]), high=float(r[2]), low=float(r[3]),
                   close=float(r[4]), volume=float(r[5])) for r in rows]


def parse_yahoo_candles(payload: dict, step: int | None = None) -> list[Candle]:
    try:
        res = payload["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise YahooError("respuesta de Yahoo sin velas") from exc
    out: dict[int, Candle] = {}
    for i, t in enumerate(ts):
        o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue  # Yahoo leaves gaps as nulls
        v = (q.get("volume") or [0] * len(ts))[i] or 0
        out[int(t)] = Candle(time=int(t), open=o, high=h, low=l, close=c, volume=float(v))
    candles = [out[k] for k in sorted(out)]  # sorted and unique, as Lightweight Charts requires
    # During market hours Yahoo appends the live price as an extra point stamped with the current
    # second. Snap it onto the candle grid so the chart doesn't get an odd extra bar.
    if step and len(candles) >= 2:
        prev, last = candles[-2], candles[-1]
        base = prev.time + (last.time - prev.time) // step * step
        if base == prev.time:
            candles[-2] = prev.model_copy(update={"high": max(prev.high, last.high), "low": min(prev.low, last.low),
                                                  "close": last.close})
            candles.pop()
        elif base != last.time:
            candles[-1] = last.model_copy(update={"time": base})
    return candles


class CandleService:
    def __init__(self, http: httpx.AsyncClient, yahoo: YahooClient, type_of, clock=time.monotonic):
        self.http, self.yahoo, self.type_of, self._clock = http, yahoo, type_of, clock
        self._cache: dict[tuple[str, str], tuple[float, list[Candle]]] = {}
        self._live: dict[tuple[str, str], Candle] = {}

    async def history(self, symbol: str, interval: str, limit: int = 300) -> list[Candle]:
        symbol = symbol.upper()
        type_ = self.type_of(symbol)
        if type_ is None:
            raise ApiError(404, f"{symbol} no está en la lista de seguimiento")
        key = (symbol, interval)
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_TTL[interval]:
            candles = hit[1]
        else:
            candles = await (self._crypto(symbol, interval) if type_ == "crypto" else self._stock(symbol, interval))
            self._cache[key] = (self._clock(), candles)
            if candles:
                self._live[key] = candles[-1].model_copy()
        return candles[-limit:]

    async def _crypto(self, symbol: str, interval: str) -> list[Candle]:
        try:
            r = await self.http.get(KLINES_URL, params={"symbol": symbol, "interval": interval, "limit": 1000})
        except httpx.HTTPError as exc:
            raise ApiError(502, f"Binance no responde: {exc}", "upstream_error") from exc
        if r.status_code != 200:
            raise ApiError(502, f"Binance respondió HTTP {r.status_code}", "upstream_error")
        return parse_klines(r.json())

    async def _stock(self, symbol: str, interval: str) -> list[Candle]:
        try:
            return parse_yahoo_candles(await self.yahoo.chart(symbol, interval, YAHOO_RANGE[interval]), SECONDS[interval])
        except (YahooError, httpx.HTTPError) as exc:
            raise ApiError(502, str(exc), "upstream_error") from exc

    def apply_quote(self, quote: Quote, interval: str) -> Candle | None:
        """Fold a new price into the in-progress candle for (symbol, interval). Returns the updated candle."""
        key = (quote.symbol, interval)
        step = SECONDS[interval]
        cur = self._live.get(key)
        if cur is None:
            return None  # no history loaded yet for this pair; the client fetches it first
        ts = int(quote.updated_at.timestamp())
        if ts < cur.time:
            return None  # stale/out-of-order price
        # Stay on the source's own grid (stocks open at :30, daily bars at the session open)
        t = cur.time + (ts - cur.time) // step * step
        if t < cur.time:
            return None  # stale/out-of-order price
        if t > cur.time:
            cur = Candle(time=t, open=quote.price, high=quote.price, low=quote.price, close=quote.price, volume=0)
        else:
            cur = cur.model_copy(update={"high": max(cur.high, quote.price), "low": min(cur.low, quote.price),
                                         "close": quote.price})
        self._live[key] = cur
        return cur
