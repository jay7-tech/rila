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
    asr_language: str  # '' = detect the spoken language automatically
    ocr_langs: str  # tesseract language codes joined with '+', e.g. eng+hin+kan
    ocr_min_conf: float  # drop OCR words below this confidence (0-100)
    max_frames: int  # frames sampled per video for on-screen text
    max_video_minutes: int
    max_upload_mb: int
    uploads_dir: str
    open_signup: bool  # anyone may create a web account (off unless you turn it on)
    allowed_hosts: tuple  # video sites accepted by /api/submit


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
        asr_language=e("ASR_LANGUAGE", "").strip(),
        ocr_langs=e("OCR_LANGS", "eng+hin+kan+tam+tel+mal").strip(),
        ocr_min_conf=float(e("OCR_MIN_CONF", "60")),
        max_frames=int(e("MAX_FRAMES", "30")),
        max_video_minutes=int(e("MAX_VIDEO_MINUTES", "20")),
        max_upload_mb=int(e("MAX_UPLOAD_MB", "200")),
        uploads_dir=e("UPLOADS_DIR", "./data/uploads"),
        open_signup=e("OPEN_SIGNUP", "0").strip().lower() in ("1", "true", "yes"),
        allowed_hosts=tuple(h.strip().lower() for h in e(
            "ALLOWED_HOSTS", "instagram.com,youtube.com,youtu.be,facebook.com,fb.watch,x.com,twitter.com,tiktok.com"
        ).split(",") if h.strip()),
    )
