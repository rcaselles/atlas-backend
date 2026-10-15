import asyncio
import logging

from app.market.quotes import QuoteStore
from app.market.yahoo import YahooClient

log = logging.getLogger("atlas.stocks")


class StockPoller:
    """Polls delayed stock quotes for the watchlist's stocks every `interval` seconds."""

    def __init__(self, yahoo: YahooClient, store: QuoteStore, symbols: list[str], interval: float = 60.0):
        self.yahoo, self.store, self.interval = yahoo, store, max(30.0, interval)
        self.symbols = sorted({s.upper() for s in symbols})
        self.errors: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def set_symbols(self, symbols: list[str]) -> None:
        self.symbols = sorted({s.upper() for s in symbols})
        self._wake.set()

    async def poll_once(self) -> None:
        for sym in list(self.symbols):
            try:
                await self.store.update(await self.yahoo.quote(sym))
                self.errors.pop(sym, None)
            except Exception as exc:  # one bad symbol never stops the rest
                self.errors[sym] = str(exc)[:200]
                log.warning("stock quote %s failed: %s", sym, exc)

    async def run(self) -> None:
        while True:
            await self.poll_once()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
