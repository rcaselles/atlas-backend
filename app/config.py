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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
