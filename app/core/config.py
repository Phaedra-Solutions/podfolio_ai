from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Podfolio API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me"

    DATABASE_URL: str
    DIRECT_URL: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    @property
    def async_database_url(self) -> str:
        """Ensure the async driver prefix is used."""
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """Synchronous URL for Alembic migrations (uses psycopg2)."""
        url = self.DIRECT_URL or self.DATABASE_URL
        for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
            if url.startswith(prefix):
                url = url.replace(prefix, "postgresql://", 1)
        return url


settings = Settings()
