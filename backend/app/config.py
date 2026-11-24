"""Application settings, read entirely from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def sqlite_path_from_url(database_url: str) -> str:
    """Turn ``sqlite:////data/app.db`` into ``/data/app.db``.

    ``sqlite:///relative.db`` becomes ``relative.db``; ``sqlite://:memory:`` is kept.
    """
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        return database_url[len(prefix):]
    if database_url.startswith("sqlite://"):
        return database_url[len("sqlite://"):]
    return database_url


@dataclass
class Settings:
    llm_api_keys: list[str] = field(default_factory=list)
    llm_model: str = "gemini/gemini-2.0-flash"
    database_url: str = "sqlite:///./data/app.db"
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    enable_failure_simulation: bool = False
    cache_enabled: bool = True
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_history_messages: int = 40
    llm_timeout_seconds: float = 30.0
    key_cooldown_seconds: float = 5.0

    @property
    def db_path(self) -> str:
        return sqlite_path_from_url(self.database_url)

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
        db_path = sqlite_path_from_url(database_url)
        return cls(
            llm_api_keys=_csv(os.getenv("LLM_API_KEYS")),
            llm_model=os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash").strip(),
            database_url=database_url,
            allowed_origins=_csv(os.getenv("ALLOWED_ORIGINS")) or ["http://localhost:3000"],
            enable_failure_simulation=_bool(os.getenv("ENABLE_FAILURE_SIMULATION")),
            cache_enabled=_bool(os.getenv("CACHE_ENABLED"), True),
            default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "40")),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            key_cooldown_seconds=float(os.getenv("KEY_COOLDOWN_SECONDS", "5")),
        )
