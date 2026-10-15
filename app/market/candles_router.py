from fastapi import APIRouter, Query, Request

from app.market.candles import Interval

router = APIRouter(prefix="/api/candles", tags=["candles"])


@router.get("/{symbol}")
async def get_candles(symbol: str, request: Request, interval: Interval = "1h", limit: int = Query(300, ge=1, le=1000)):
    candles = await request.app.state.candles.history(symbol, interval, limit)
    return {"symbol": symbol.upper(), "interval": interval, "candles": [c.model_dump() for c in candles]}
