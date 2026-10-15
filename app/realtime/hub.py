import asyncio
import json
import logging

from fastapi import WebSocket

log = logging.getLogger("atlas.ws")


class Hub:
    """Tracks connected websocket clients and broadcasts JSON messages to them."""

    def __init__(self):
        self._clients: dict[WebSocket, set[tuple[str, str]]] = {}
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._clients)

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[ws] = set()

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)

    def subscribe_candles(self, ws: WebSocket, symbol: str, interval: str) -> None:
        if ws in self._clients:
            self._clients[ws].add((symbol.upper(), interval))

    def unsubscribe_candles(self, ws: WebSocket, symbol: str, interval: str) -> None:
        if ws in self._clients:
            self._clients[ws].discard((symbol.upper(), interval))

    async def _send(self, ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
        except Exception:
            await self.remove(ws)

    async def broadcast(self, type_: str, data: dict) -> None:
        text = json.dumps({"type": type_, "data": data}, default=str)
        await asyncio.gather(*(self._send(ws, text) for ws in list(self._clients)))

    async def broadcast_candle(self, symbol: str, interval: str, candle: dict) -> None:
        key = (symbol.upper(), interval)
        text = json.dumps({"type": "candle", "data": {"symbol": key[0], "interval": interval, "candle": candle}})
        targets = [ws for ws, subs in list(self._clients.items()) if key in subs]
        await asyncio.gather(*(self._send(ws, text) for ws in targets))
