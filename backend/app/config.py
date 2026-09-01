"""Application configuration. Reads server-side env only; never logs secret values."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fmp_api_key: str = ""
    openai_api_key: str = ""
    sec_user_agent: str = "PremarketHunter/1.0 contact@example.com"
    database_url: str = "sqlite+aiosqlite:///./data/premarket.db"
    app_secret: str = ""
    app_timezone: str = "America/New_York"
    openai_model: str = "gpt-4o-mini"
    fmp_rest_base: str = "https://financialmodelingprep.com/stable"
    scan_enabled: bool = True
    paper_mode: bool = True
    cors_origins: str = "http://localhost:3000"
    openai_monthly_budget_usd: float = 25.0
    api_access_key: str = ""  # when set, /api/* requires X-API-Key header (or ?api_key= for SSE/CSV)

    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def missing_required(self) -> List[str]:
        """Names (never values) of required-but-missing variables."""
        missing = []
        if not self.fmp_api_key:
            missing.append("FMP_API_KEY")
        if not self.app_secret:
            missing.append("APP_SECRET")
        return missing

    def provider_status(self) -> dict:
        """Configured / not-configured flags only. Never secrets or fragments."""
        return {
            "FMP_API_KEY": bool(self.fmp_api_key),
            "OPENAI_API_KEY": bool(self.openai_api_key),
            "SEC_USER_AGENT": bool(self.sec_user_agent),
            "DATABASE_URL": bool(self.database_url),
            "APP_SECRET": bool(self.app_secret),
        }


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
