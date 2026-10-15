from fastapi import APIRouter, Request

from app.watchlist.service import WatchItem

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
def get_watchlist(request: Request):
    return {"items": request.app.state.watchlist.snapshot()}


@router.post("", status_code=201)
async def add_item(item: WatchItem, request: Request):
    await request.app.state.watchlist.add(item)
    return {"items": request.app.state.watchlist.snapshot()}


@router.delete("/{symbol}")
def remove_item(symbol: str, request: Request):
    request.app.state.watchlist.remove(symbol)
    return {"items": request.app.state.watchlist.snapshot()}
