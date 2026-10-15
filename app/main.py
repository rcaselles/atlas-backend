import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import errors
from app.config import settings
from app.news.aggregator import NewsAggregator, load_sources
from app.news.router import router as news_router
from app.news.store import NewsStore

VERSION = "0.1.0"
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.news_store = NewsStore(max_items=settings.news_max_items)
    app.state.news_aggregator = None
    if settings.news_polling:
        agg = NewsAggregator(load_sources(settings.news_sources_file), app.state.news_store)
        app.state.news_aggregator = agg
        await agg.start()
    yield
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "env": settings.env}
