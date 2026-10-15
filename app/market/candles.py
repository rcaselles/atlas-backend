"""OHLC candle history (REST) and live candle updates built from the quote stream."""
import asyncio
import logging
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
log = logging.getLogger("atlas.candles")
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
# Short Yahoo ranges used to refresh the in-progress candle (with its real volume)
YAHOO_REFRESH_RANGE = {"1m": "1d", "1h": "5d", "1d": "5d"}
# How often the in-progress candle is re-read from the source, per asset type
REFRESH_SECONDS = {"crypto": 5, "stock": 60}


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

    async def _crypto(self, symbol: str, interval: str, limit: int = 1000) -> list[Candle]:
        try:
            r = await self.http.get(KLINES_URL, params={"symbol": symbol, "interval": interval, "limit": limit})
        except httpx.HTTPError as exc:
            raise ApiError(502, f"Binance no responde: {exc}", "upstream_error") from exc
        if r.status_code != 200:
            raise ApiError(502, f"Binance respondió HTTP {r.status_code}", "upstream_error")
        return parse_klines(r.json())

    async def _stock(self, symbol: str, interval: str, range_: str | None = None) -> list[Candle]:
        try:
            payload = await self.yahoo.chart(symbol, interval, range_ or YAHOO_RANGE[interval])
            return parse_yahoo_candles(payload, SECONDS[interval])
        except (YahooError, httpx.HTTPError) as exc:
            raise ApiError(502, str(exc), "upstream_error") from exc

    def live(self, symbol: str, interval: str) -> Candle | None:
        return self._live.get((symbol.upper(), interval))

    async def ensure_live(self, symbol: str, interval: str) -> Candle | None:
        """Make sure there is an in-progress candle for the pair (loads history if needed)."""
        if (symbol.upper(), interval) not in self._live:
            await self.history(symbol, interval, 1)
        return self.live(symbol, interval)

    async def refresh(self, symbol: str, interval: str) -> list[Candle]:
        """Re-read the latest candles from the source, so volume and OHLC match it exactly.
        Returns the candles that changed (to push to subscribers)."""
        symbol = symbol.upper()
        type_ = self.type_of(symbol)
        if type_ is None:
            return []
        if (symbol, interval) not in self._live:
            await self.history(symbol, interval, 1)
            cur = self.live(symbol, interval)
            return [cur] if cur else []
        if type_ == "crypto":
            recent = await self._crypto(symbol, interval, limit=2)
        else:
            recent = (await self._stock(symbol, interval, YAHOO_REFRESH_RANGE[interval]))[-2:]
        key = (symbol, interval)
        cur = self._live[key]
        changed, closed = [], []
        for c in recent:
            if c.time < cur.time:
                closed.append(c)  # already closed on our side: fix it in the cache, but don't push it
                continue          # (Lightweight Charts' update() only accepts the last bar)
            if c.time == cur.time:
                # keep any extreme seen from the live quotes that the source hasn't caught up with yet
                c = c.model_copy(update={"high": max(c.high, cur.high), "low": min(c.low, cur.low)})
            changed.append(c)
            cur = c
        if changed:
            self._live[key] = cur
        if changed or closed:
            hit = self._cache.get(key)
            if hit:
                by_time = {c.time: c for c in hit[1]}
                by_time.update({c.time: c for c in closed + changed})
                self._cache[key] = (hit[0], [by_time[t] for t in sorted(by_time)])
        return changed

    async def run_refresher(self, hub, tick: float = 5.0) -> None:
        """Background loop: refresh the in-progress candle of every subscribed pair and push it."""
        last: dict[tuple[str, str], float] = {}
        while True:
            now = self._clock()
            for symbol, interval in hub.candle_subscriptions():
                type_ = self.type_of(symbol)
                if type_ is None or now - last.get((symbol, interval), -1e9) < REFRESH_SECONDS[type_]:
                    continue
                last[(symbol, interval)] = now
                try:
                    for c in await self.refresh(symbol, interval):
                        await hub.broadcast_candle(symbol, interval, c.model_dump())
                except Exception as exc:  # a failing source must not kill the loop
                    log.warning("no se pudo refrescar la vela %s %s: %s", symbol, interval, exc)
            await asyncio.sleep(tick)

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
