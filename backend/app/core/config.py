"""
config.py — Centralised settings management.

CONCEPT: Pydantic Settings
---------------------------
pydantic-settings reads environment variables and .env files automatically.
Every attribute on the Settings class maps to an env var of the same name.
If the env var is missing and there's no default, it raises an error on
startup — fail fast rather than silently using wrong values.

This means your entire config is in one place, type-checked, and
validated before the app starts. No scattered os.getenv() calls.
"""

from pydantic import ConfigDict, validator
from pydantic_settings import BaseSettings
from typing import List, Union


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: Union[List[str], str] = ["http://localhost:3000"]

    @validator("ALLOWED_ORIGINS", pre=True)
    def parse_origins(cls, v):
        if isinstance(v, str):
            # Handle both JSON list and comma-separated string
            v = v.strip()
            if v.startswith("["):
                import json
                return json.loads(v)
            return [i.strip() for i in v.split(",")]
        return v

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/soiree"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # Swiggy MCP (from mcp.swiggy.com/builders)
    SWIGGY_API_KEY: str = ""
    SWIGGY_MCP_FOOD_URL: str = ""
    SWIGGY_MCP_INSTAMART_URL: str = ""
    SWIGGY_MCP_DINEOUT_URL: str = ""

    # Auth — MSG91 for Indian phone OTP
    MSG91_AUTH_KEY: str = ""
    MSG91_TEMPLATE_ID: str = ""

    # Cache TTLs in seconds
    OFFERS_CACHE_TTL: int = 300  # 5 min — offers change fast
    RESTAURANT_CACHE_TTL: int = 3600  # 1 hr
    MENU_CACHE_TTL: int = 1800  # 30 min

    # class Config:
    #     env_file = ".env"
    #     case_sensitive = True
    model_config = ConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
