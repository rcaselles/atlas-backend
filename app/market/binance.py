import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets

from app.market.quotes import NAMES, Quote, QuoteStore

log = logging.getLogger("atlas.binance")

# Public market-data endpoints, no API key. The .vision host is market-data only and reachable from more regions.
DEFAULT_URLS = ["wss://data-stream.binance.vision", "wss://stream.binance.com:9443"]


def parse_mini_ticker(msg: dict) -> Quote | None:
    d = msg.get("data", msg)
    if d.get("e") != "24hrMiniTicker":
        return None
    try:
        close, open_ = float(d["c"]), float(d["o"])
        symbol = d["s"].upper()
        ts = datetime.fromtimestamp(int(d["E"]) / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    change = (close - open_) / open_ * 100 if open_ else 0.0
    return Quote(symbol=symbol, type="crypto", name=NAMES.get(symbol, symbol), price=close,
                 change_24h_pct=round(change, 4), updated_at=ts)


class BinanceStream:
    """Keeps a public Binance websocket open for the given symbols and feeds a QuoteStore. Reconnects with backoff."""

    def __init__(self, symbols: list[str], store: QuoteStore, urls: list[str] | None = None,
                 connect=websockets.connect, max_backoff: float = 60.0, sleep=asyncio.sleep):
        self.symbols = sorted({s.upper() for s in symbols})
        self.store = store
        self.urls = urls or DEFAULT_URLS
        self._connect = connect
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self._restart = asyncio.Event()
        self.connected = False
        self.connections = 0

    def stream_url(self, base: str) -> str:
        streams = "/".join(f"{s.lower()}@miniTicker" for s in self.symbols)
        return f"{base}/stream?streams={streams}"

    def set_symbols(self, symbols: list[str]) -> None:
        new = sorted({s.upper() for s in symbols})
        if new != self.symbols:
            self.symbols = new
            self._restart.set()

    async def run(self) -> None:
        backoff, attempt = 1.0, 0
        while True:
            if not self.symbols:
                self._restart.clear()
                await self._restart.wait()
                continue
            base = self.urls[attempt % len(self.urls)]
            opened = False
            try:
                async with self._connect(self.stream_url(base), open_timeout=10, ping_interval=20) as ws:
                    opened = True
                    self.connected, self.connections = True, self.connections + 1
                    backoff = 1.0
                    log.info("binance connected (%s, %d symbols)", base, len(self.symbols))
                    self._restart.clear()
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("binance stream error on %s: %s", base, exc)
                if not opened:  # handshake failed: try the next endpoint
                    attempt += 1
            finally:
                self.connected = False
            if self._restart.is_set():
                continue
            await self._sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def _consume(self, ws) -> None:
        restart = asyncio.create_task(self._restart.wait())
        try:
            while True:
                recv = asyncio.create_task(ws.recv())
                done, _ = await asyncio.wait({recv, restart}, return_when=asyncio.FIRST_COMPLETED)
                if restart in done:
                    recv.cancel()
                    return
                raw = recv.result()
                try:
                    quote = parse_mini_ticker(json.loads(raw))
                except ValueError:
                    continue
                if quote and quote.symbol in self.symbols:
                    await self.store.update(quote)
        finally:
            restart.cancel()

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
