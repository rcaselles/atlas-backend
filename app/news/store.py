import asyncio
from collections.abc import Awaitable, Callable

from app.news.models import NewsItem
from app.news.normalize import title_key

Listener = Callable[[NewsItem], Awaitable[None]]


class NewsStore:
    """In-memory, deduplicated, newest-first news store with change listeners."""

    def __init__(self, max_items: int = 1000):
        self._max = max_items
        self._items: dict[str, NewsItem] = {}
        self._titles: set[str] = set()
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def add_many(self, items: list[NewsItem], notify: bool = True) -> list[NewsItem]:
        new = []
        for it in items:
            tk = title_key(it.title)
            if it.id in self._items or tk in self._titles:
                continue
            self._items[it.id] = it
            self._titles.add(tk)
            new.append(it)
        self._trim()
        new.sort(key=lambda i: i.published_at)
        if notify and new:
            for it in new:
                await asyncio.gather(*(l(it) for l in list(self._listeners)), return_exceptions=True)
        return new

    def _trim(self) -> None:
        if len(self._items) <= self._max:
            return
        for it in self._sorted()[self._max :]:
            del self._items[it.id]
            self._titles.discard(title_key(it.title))

    def _sorted(self) -> list[NewsItem]:
        return sorted(self._items.values(), key=lambda i: (i.published_at, i.id), reverse=True)

    def page(self, limit: int = 20, cursor: str | None = None, symbol: str | None = None,
             type_: str | None = None, source: str | None = None):
        items = self._sorted()
        if symbol:
            items = [i for i in items if symbol.upper() in i.symbols]
        if type_:
            items = [i for i in items if type_ in i.asset_types]
        if source:
            items = [i for i in items if source.lower() in (i.source_id.lower(), i.source.lower())]
        start = 0
        if cursor:
            idx = next((n for n, i in enumerate(items) if i.id == cursor), None)
            if idx is None:
                return None
            start = idx + 1
        chunk = items[start : start + limit]
        next_cursor = chunk[-1].id if len(chunk) == limit and start + limit < len(items) else None
        return chunk, next_cursor

    def __len__(self) -> int:
        return len(self._items)
