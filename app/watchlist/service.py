import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from app.errors import ApiError
from app.market.quotes import NAMES, QuoteStore
from app.market.yahoo import YahooClient, YahooError

log = logging.getLogger("atlas.watchlist")

AssetType = Literal["crypto", "stock"]
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")
BINANCE_PRICE = "https://data-api.binance.vision/api/v3/ticker/price"


class WatchItem(BaseModel):
    symbol: str
    type: AssetType


def parse_default(spec: str) -> list[WatchItem]:
    out = []
    for part in spec.split(","):
        if ":" in part:
            sym, typ = part.strip().split(":", 1)
            out.append(WatchItem(symbol=sym.upper(), type=typ.strip()))
    return out


class Validator:
    """Checks that a symbol really exists before it is added."""

    def __init__(self, http: httpx.AsyncClient, yahoo: YahooClient):
        self.http, self.yahoo = http, yahoo

    async def __call__(self, item: WatchItem) -> str | None:
        if item.type == "crypto":
            r = await self.http.get(BINANCE_PRICE, params={"symbol": item.symbol})
            return NAMES.get(item.symbol, item.symbol) if r.status_code == 200 else None
        try:
            q = await self.yahoo.quote(item.symbol)
            return q.name
        except YahooError:
            return None


ChangeHook = Callable[[list[WatchItem]], None]


class Watchlist:
    def __init__(self, path: str | Path | None, default: list[WatchItem], quotes: QuoteStore,
                 validator: Callable[[WatchItem], Awaitable[str | None]]):
        self.path = Path(path) if path else None
        self.quotes = quotes
        self.validator = validator
        self.names: dict[str, str] = {}
        self._hooks: list[ChangeHook] = []
        self.items = self._load() or list(default)

    def _load(self) -> list[WatchItem] | None:
        if self.path and self.path.exists():
            try:
                return [WatchItem(**i) for i in json.loads(self.path.read_text())]
            except (ValueError, TypeError) as exc:
                log.warning("watchlist file unreadable, using defaults: %s", exc)
        return None

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([i.model_dump() for i in self.items], indent=2))

    def on_change(self, hook: ChangeHook) -> None:
        self._hooks.append(hook)

    def symbols(self, type_: AssetType) -> list[str]:
        return [i.symbol for i in self.items if i.type == type_]

    def _changed(self) -> None:
        self._save()
        for h in self._hooks:
            h(self.items)

    async def add(self, item: WatchItem) -> None:
        item = WatchItem(symbol=item.symbol.strip().upper(), type=item.type)
        if not SYMBOL_RE.match(item.symbol):
            raise ApiError(422, f"símbolo no válido: {item.symbol}")
        if any(i.symbol == item.symbol for i in self.items):
            raise ApiError(409, f"{item.symbol} ya está en la lista de seguimiento")
        name = await self.validator(item)
        if not name:
            raise ApiError(422, f"no existe el símbolo {item.symbol} como {item.type}")
        self.names[item.symbol] = name
        self.items.append(item)
        self._changed()

    def remove(self, symbol: str) -> None:
        symbol = symbol.upper()
        before = len(self.items)
        self.items = [i for i in self.items if i.symbol != symbol]
        if len(self.items) == before:
            raise ApiError(404, f"{symbol} no está en la lista de seguimiento")
        self.quotes.remove(symbol)
        self._changed()

    def snapshot(self) -> list[dict]:
        out = []
        for i in self.items:
            q = self.quotes.get(i.symbol)
            if q:
                out.append(q.model_dump(mode="json"))
            else:
                out.append({"symbol": i.symbol, "type": i.type,
                            "name": self.names.get(i.symbol) or NAMES.get(i.symbol, i.symbol),
                            "price": None, "change_24h_pct": None, "updated_at": None,
                            "delayed": i.type == "stock", "currency": None})
        return out
