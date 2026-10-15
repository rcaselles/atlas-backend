from datetime import datetime

from pydantic import BaseModel, Field


class Asset(BaseModel):
    symbol: str
    type: str  # crypto | stock
    name: str


class NewsItem(BaseModel):
    id: str
    title: str
    source: str
    source_id: str = ""
    url: str
    published_at: datetime
    summary: str | None = None
    symbols: list[str] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    asset_types: list[str] = Field(default_factory=list)


class SourceConfig(BaseModel):
    id: str
    name: str
    type: str = "rss"  # rss | html
    url: str | None = None
    list_url: str | None = None
    interval: int = 120
    enabled: bool = True
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None
    time_selector: str | None = None
    summary_selector: str | None = None


class SourceStatus(BaseModel):
    id: str
    name: str
    type: str
    ok: bool | None = None
    last_run: datetime | None = None
    last_error: str | None = None
    items_last_run: int = 0
