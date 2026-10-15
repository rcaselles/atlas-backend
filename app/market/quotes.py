import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

NAMES = {
    "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "BNBUSDT": "BNB",
    "XRPUSDT": "XRP", "ADAUSDT": "Cardano", "DOGEUSDT": "Dogecoin",
}


class Quote(BaseModel):
    symbol: str
    type: str
    name: str
    price: float
    change_24h_pct: float
    updated_at: datetime
    delayed: bool = False
    currency: str | None = None

    def price_message(self) -> dict:
        return self.model_dump(mode="json", include={"symbol", "price", "change_24h_pct", "updated_at", "delayed"})


Listener = Callable[[Quote], Awaitable[None]]


class QuoteStore:
    """Latest quote per symbol, shared by the rest of the system. Notifies at most once per symbol per `min_gap` seconds."""

    def __init__(self, min_gap: float = 1.0, clock: Callable[[], float] = time.monotonic):
        self._quotes: dict[str, Quote] = {}
        self._last_push: dict[str, float] = {}
        self._listeners: list[Listener] = []
        self._raw_listeners: list[Listener] = []
        self._gap = min_gap
        self._clock = clock

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def subscribe_all(self, listener: Listener) -> None:
        """Like subscribe, but called on every update, without throttling (for candle building)."""
        self._raw_listeners.append(listener)

    def get(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol.upper())

    def remove(self, symbol: str) -> None:
        self._quotes.pop(symbol.upper(), None)
        self._last_push.pop(symbol.upper(), None)

    def all(self) -> list[Quote]:
        return list(self._quotes.values())

    async def update(self, quote: Quote) -> bool:
        self._quotes[quote.symbol] = quote
        if self._raw_listeners:
            await asyncio.gather(*(l(quote) for l in list(self._raw_listeners)), return_exceptions=True)
        now = self._clock()
        if now - self._last_push.get(quote.symbol, float("-inf")) < self._gap:
            return False
        self._last_push[quote.symbol] = now
        await asyncio.gather(*(l(quote) for l in list(self._listeners)), return_exceptions=True)
        return True
