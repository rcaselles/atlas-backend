import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx


class RobotsCache:
    """Caches robots.txt per host and answers whether our user agent may fetch a URL."""

    def __init__(self, client: httpx.AsyncClient, user_agent: str, ttl: int = 3600):
        self._client = client
        self._ua = user_agent
        self._ttl = ttl
        self._cache: dict[str, tuple[float, RobotFileParser]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        cached = self._cache.get(host)
        if not cached or time.monotonic() - cached[0] > self._ttl:
            parser = RobotFileParser()
            try:
                r = await self._client.get(f"{host}/robots.txt")
                if r.status_code in (401, 403):
                    parser.disallow_all = True
                elif r.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(r.text.splitlines())
            except httpx.HTTPError:
                # If robots.txt can't be read, be conservative and skip this round.
                return False
            cached = (time.monotonic(), parser)
            self._cache[host] = cached
        return cached[1].can_fetch(self._ua, url)
