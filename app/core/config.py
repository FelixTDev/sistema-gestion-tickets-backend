from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ticket Management API"
    app_env: str = "development"
    debug: bool = False
    database_url: str = "mysql+pymysql://tickets:tickets@localhost:3306/tickets"
    secret_key: str = "change-this-development-secret"
    access_token_expire_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
