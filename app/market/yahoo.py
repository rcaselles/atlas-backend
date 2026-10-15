"""Free, delayed stock quotes from Yahoo Finance's public chart endpoint (unofficial, no key)."""
import time
from datetime import datetime, timezone

import httpx

from app.market.quotes import Quote

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "AtlasBot/0.1 (+https://github.com/rcaselles/atlas-backend)"


class YahooError(Exception):
    pass


def parse_chart_quote(payload: dict) -> Quote:
    try:
        meta = payload["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise YahooError("respuesta de Yahoo sin precio") from exc
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = (price - float(prev)) / float(prev) * 100 if prev else 0.0
    ts = meta.get("regularMarketTime")
    return Quote(
        symbol=meta.get("symbol", "").upper(),
        type="stock",
        name=meta.get("shortName") or meta.get("longName") or meta.get("symbol", ""),
        price=price,
        change_24h_pct=round(change, 4),
        updated_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc),
        delayed=True,
        currency=meta.get("currency"),
    )


class YahooClient:
    """Fetches quotes with a per-symbol cache so we never hit Yahoo more than once per `ttl` seconds per symbol."""

    def __init__(self, client: httpx.AsyncClient | None = None, ttl: float = 60.0, clock=time.monotonic):
        self.client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=10)
        self.ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, Quote]] = {}

    async def chart(self, symbol: str, interval: str, range_: str) -> dict:
        r = await self.client.get(CHART_URL.format(symbol=symbol), params={"interval": interval, "range": range_})
        if r.status_code == 404:
            raise YahooError(f"símbolo desconocido: {symbol}")
        if r.status_code != 200:
            raise YahooError(f"Yahoo respondió HTTP {r.status_code}")
        return r.json()

    async def quote(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        hit = self._cache.get(symbol)
        if hit and self._clock() - hit[0] < self.ttl:
            return hit[1]
        q = parse_chart_quote(await self.chart(symbol, "1d", "1d"))
        self._cache[symbol] = (self._clock(), q)
        return q

    async def aclose(self) -> None:
        await self.client.aclose()
