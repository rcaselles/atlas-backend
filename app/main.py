import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import errors
from app.config import settings
from app.market.binance import BinanceStream
from app.market.quotes import QuoteStore
from app.news.aggregator import NewsAggregator, load_sources
from app.news.router import router as news_router
from app.news.store import NewsStore
from app.realtime.hub import Hub
from app.realtime.router import router as ws_router

VERSION = "0.1.0"
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.news_store = NewsStore(max_items=settings.news_max_items)
    app.state.news_aggregator = None
    app.state.hub = Hub()

    async def push_news(item):
        await app.state.hub.broadcast("news", item.model_dump(mode="json"))

    app.state.news_store.subscribe(push_news)

    app.state.quotes = QuoteStore()

    async def push_price(quote):
        await app.state.hub.broadcast("price", quote.price_message())

    app.state.quotes.subscribe(push_price)
    app.state.watchlist_snapshot = lambda: [q.model_dump(mode="json") for q in app.state.quotes.all()]
    app.state.binance = None
    if settings.market_streaming:
        app.state.binance = BinanceStream(settings.crypto_symbol_list, app.state.quotes, urls=settings.binance_url_list)
        app.state.binance.start()
    if settings.news_polling:
        agg = NewsAggregator(load_sources(settings.news_sources_file), app.state.news_store)
        app.state.news_aggregator = agg
        await agg.start()
    yield
    if app.state.binance:
        await app.state.binance.stop()
    if app.state.news_aggregator:
        await app.state.news_aggregator.stop()


app = FastAPI(title="Atlas Backend", version=VERSION, lifespan=lifespan)
errors.install(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(news_router)
app.include_router(ws_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "env": settings.env}
