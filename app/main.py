import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import errors
from app.config import settings
from app.market.binance import BinanceStream
from app.market.candles import CandleService
from app.market.candles_router import router as candles_router
from app.market.quotes import QuoteStore
from app.market.stocks import StockPoller
from app.market.yahoo import YahooClient
from app.news.aggregator import NewsAggregator, load_sources
from app.news.router import router as news_router
from app.news.store import NewsStore
from app.news.symbols import SymbolDetector
from app.realtime.hub import Hub
from app.realtime.router import router as ws_router
from app.watchlist.router import router as watchlist_router
from app.watchlist.service import Validator, Watchlist, parse_default

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
    app.state.detector = SymbolDetector()

    app.state.quotes = QuoteStore()

    async def push_price(quote):
        await app.state.hub.broadcast("price", quote.price_message())

    app.state.quotes.subscribe(push_price)
    app.state.yahoo = YahooClient()
    app.state.http = httpx.AsyncClient(timeout=10)
    wl = Watchlist(settings.watchlist_file, parse_default(settings.default_watchlist), app.state.quotes,
                   Validator(app.state.http, app.state.yahoo))
    app.state.watchlist = wl
    app.state.watchlist_snapshot = wl.snapshot

    def teach_detector(items):
        for i in items:
            app.state.detector.add(i.symbol, i.type, wl.names.get(i.symbol, i.symbol))

    teach_detector(wl.items)
    wl.on_change(teach_detector)

    def type_of(symbol):
        return next((i.type for i in wl.items if i.symbol == symbol), None)

    app.state.candles = CandleService(app.state.http, app.state.yahoo, type_of)

    async def push_candles(quote):
        hub = app.state.hub
        for interval in hub.candle_intervals(quote.symbol):
            candle = app.state.candles.apply_quote(quote, interval)
            if candle:
                await hub.broadcast_candle(quote.symbol, interval, candle.model_dump())

    app.state.quotes.subscribe_all(push_candles)
    app.state.binance = None
    app.state.stocks = None
    app.state.candle_refresher = None
    if settings.market_streaming:
        app.state.candle_refresher = asyncio.create_task(app.state.candles.run_refresher(app.state.hub))
        app.state.binance = BinanceStream(wl.symbols("crypto"), app.state.quotes, urls=settings.binance_url_list)
        app.state.binance.start()
        app.state.stocks = StockPoller(app.state.yahoo, app.state.quotes, wl.symbols("stock"), settings.stock_poll_seconds)
        app.state.stocks.start()

        def resubscribe(_items):
            app.state.binance.set_symbols(wl.symbols("crypto"))
            app.state.stocks.set_symbols(wl.symbols("stock"))

        wl.on_change(resubscribe)
    if settings.news_polling:
        agg = NewsAggregator(load_sources(settings.news_sources_file), app.state.news_store, detector=app.state.detector)
        app.state.news_aggregator = agg
        await agg.start()
    yield
    if app.state.candle_refresher:
        app.state.candle_refresher.cancel()
    if app.state.binance:
        await app.state.binance.stop()
    if app.state.stocks:
        await app.state.stocks.stop()
    await app.state.yahoo.aclose()
    await app.state.http.aclose()
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
app.include_router(watchlist_router)
app.include_router(candles_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "env": settings.env}
