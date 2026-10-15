import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from app.news.fetchers import fetch_source
from app.news.models import SourceConfig, SourceStatus
from app.news.robots import RobotsCache
from app.news.store import NewsStore
from app.news.symbols import SymbolDetector

log = logging.getLogger("atlas.news")

MIN_INTERVAL = 60
USER_AGENT = "AtlasBot/0.1 (+https://github.com/rcaselles/atlas-backend)"


def load_sources(path: str | Path) -> list[SourceConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = [SourceConfig(**s) for s in data.get("sources", [])]
    for s in sources:
        s.interval = max(MIN_INTERVAL, s.interval)
    return [s for s in sources if s.enabled]


class NewsAggregator:
    def __init__(
        self,
        sources: list[SourceConfig],
        store: NewsStore,
        client: httpx.AsyncClient | None = None,
        detector: SymbolDetector | None = None,
    ):
        self.sources = sources
        self.store = store
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True
        )
        self.robots = RobotsCache(self.client, USER_AGENT)
        self.detector = detector or SymbolDetector()
        self.status = {s.id: SourceStatus(id=s.id, name=s.name, type=s.type) for s in sources}
        self._tasks: list[asyncio.Task] = []

    async def poll_once(self, source: SourceConfig, notify: bool = True) -> int:
        st = self.status[source.id]
        st.last_run = datetime.now(timezone.utc)
        try:
            items = await fetch_source(self.client, source, self.robots, self.detector)
            new = await self.store.add_many(items, notify=notify)
            st.ok, st.last_error, st.items_last_run = True, None, len(items)
            return len(new)
        except Exception as exc:  # one broken source must never break the others
            st.ok, st.last_error, st.items_last_run = False, str(exc)[:200], 0
            log.warning("news source %s failed: %s", source.id, exc)
            return 0

    async def poll_all(self, notify: bool = True) -> int:
        results = await asyncio.gather(*(self.poll_once(s, notify) for s in self.sources))
        return sum(results)

    async def _loop(self, source: SourceConfig) -> None:
        while True:
            await asyncio.sleep(source.interval)
            await self.poll_once(source)

    async def start(self) -> None:
        await self.poll_all(notify=False)  # initial fill, no push storm
        self._tasks = [asyncio.create_task(self._loop(s)) for s in self.sources]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()
