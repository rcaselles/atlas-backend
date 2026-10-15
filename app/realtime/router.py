import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.errors import ApiError

router = APIRouter()

INTERVALS = {"1m", "1h", "1d"}


def _snapshot(app) -> dict:
    items, _ = app.state.news_store.page(limit=50)
    watchlist = app.state.watchlist_snapshot() if hasattr(app.state, "watchlist_snapshot") else []
    return {
        "type": "snapshot",
        "data": {"watchlist": watchlist, "news": [i.model_dump(mode="json") for i in items]},
    }


def _error(message: str) -> str:
    return json.dumps({"type": "error", "data": {"code": "invalid_message", "message": message}})


async def _send_current_candle(ws: WebSocket, symbol: str, interval: str) -> None:
    """Send the in-progress candle right away, loading history if nobody asked for it yet
    (e.g. right after a backend restart), so live updates always have something to build on."""
    candles = getattr(ws.app.state, "candles", None)
    if candles is None:
        return
    try:
        candle = await candles.ensure_live(symbol, interval)
    except ApiError as exc:
        await ws.send_text(json.dumps({"type": "error", "data": {"code": exc.code, "message": exc.message}}))
        return
    if candle:
        await ws.send_text(json.dumps({"type": "candle", "data": {
            "symbol": symbol.upper(), "interval": interval, "candle": candle.model_dump()}}))


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    hub = ws.app.state.hub
    await ws.accept()
    await hub.add(ws)
    try:
        await ws.send_text(json.dumps(_snapshot(ws.app), default=str))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                kind = msg.get("type")
            except (ValueError, AttributeError):
                await ws.send_text(_error("el mensaje debe ser JSON con un campo type"))
                continue
            if kind == "ping":
                await ws.send_text('{"type": "pong"}')
            elif kind in ("subscribe_candles", "unsubscribe_candles"):
                data = msg.get("data") or {}
                symbol, interval = data.get("symbol"), data.get("interval")
                if not isinstance(symbol, str) or interval not in INTERVALS:
                    await ws.send_text(_error("hace falta symbol e interval (1m, 1h o 1d)"))
                    continue
                if kind == "subscribe_candles":
                    hub.subscribe_candles(ws, symbol, interval)
                    await _send_current_candle(ws, symbol, interval)
                else:
                    hub.unsubscribe_candles(ws, symbol, interval)
            else:
                await ws.send_text(_error(f"tipo de mensaje desconocido: {kind}"))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)
