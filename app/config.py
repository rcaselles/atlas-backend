from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ATLAS_")

    env: str = "development"
    # Comma-separated list of allowed frontend origins
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    # News aggregator
    news_sources_file: str = "config/sources.yaml"
    news_polling: bool = True
    news_max_items: int = 1000
    # Market data
    market_streaming: bool = True
    # Default watchlist as SYMBOL:type pairs, used until someone edits it
    default_watchlist: str = (
        "BTCUSDT:crypto,ETHUSDT:crypto,SOLUSDT:crypto,AAPL:stock,MSFT:stock,NVDA:stock,TSLA:stock,SAN.MC:stock"
    )
    watchlist_file: str = "data/watchlist.json"
    stock_poll_seconds: float = 60.0
    binance_ws_urls: str = "wss://data-stream.binance.vision,wss://stream.binance.com:9443"

    @property
    def binance_url_list(self) -> list[str]:
        return [u.strip().rstrip("/") for u in self.binance_ws_urls.split(",") if u.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
