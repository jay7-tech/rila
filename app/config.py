"""Settings, read from environment variables (and .env in development)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet


def _ids(value: str) -> FrozenSet[int]:
    return frozenset(int(x) for x in value.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit())


@dataclass(frozen=True)
class Settings:
    database_url: str
    bot_token: str
    groq_api_key: str
    groq_model: str
    groq_fallback_models: tuple
    invite_code: str  # if set, new Telegram users must send /start <code>
    allowed_ids: FrozenSet[int]  # if set, only these Telegram ids may join
    public_url: str  # base URL testers use to open the web app
    cookies_path: str  # optional Instagram cookies file for yt-dlp
    whisper_model: str
    min_confidence: float
    port: int
    run_migrations: bool  # false when a separate 'migrate' step handles it


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        database_url=e("DATABASE_URL", "sqlite:///./data/rilla.db"),
        bot_token=e("BOT_TOKEN", ""),
        groq_api_key=e("GROQ_API_KEY", ""),
        groq_model=e("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_fallback_models=tuple(
            m.strip() for m in e("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b,llama-3.1-8b-instant").split(",") if m.strip()
        ),
        invite_code=e("INVITE_CODE", ""),
        allowed_ids=_ids(e("ALLOWED_USER_IDS", e("ALLOWED_USER_ID", ""))),
        public_url=e("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        cookies_path=e("INSTAGRAM_COOKIES_PATH", ""),
        whisper_model=e("WHISPER_MODEL", "small"),
        min_confidence=float(e("MIN_CONFIDENCE", "0.7")),
        port=int(e("PORT", "8000")),
        run_migrations=e("RUN_MIGRATIONS", "1").strip().lower() not in ("0", "false", "no"),
    )
