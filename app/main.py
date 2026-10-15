from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

VERSION = "0.1.0"

app = FastAPI(title="Atlas Backend", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "env": settings.env}
