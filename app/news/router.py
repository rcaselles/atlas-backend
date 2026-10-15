from typing import Literal

from fastapi import APIRouter, Query, Request

from app.errors import ApiError
from app.news.models import NewsItem, SourceStatus
from pydantic import BaseModel

router = APIRouter(prefix="/api/news", tags=["news"])


class NewsPage(BaseModel):
    items: list[NewsItem]
    next_cursor: str | None


@router.get("", response_model=NewsPage)
def list_news(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    symbol: str | None = None,
    type: Literal["crypto", "stock"] | None = None,
    source: str | None = None,
):
    result = request.app.state.news_store.page(limit=limit, cursor=cursor, symbol=symbol, type_=type, source=source)
    if result is None:
        raise ApiError(400, "cursor no válido o caducado", "invalid_cursor")
    items, next_cursor = result
    return NewsPage(items=items, next_cursor=next_cursor)


@router.get("/sources", response_model=list[SourceStatus])
def list_sources(request: Request):
    agg = request.app.state.news_aggregator
    return list(agg.status.values()) if agg else []
