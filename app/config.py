"""
إعدادات خدمة المقتنيات (Collectibles Service).

مبدأ أمني ثابت: كل القيم تُقرأ من متغيّرات البيئة (.env) فقط.
لا يوجد أي سرّ مكتوب داخل الكود، ولا قيمة افتراضية تحمل سرّاً
(أسرار Telethon تتخلّف إلى فارغ/صفر، لا إلى قيمة حقيقية).

Phase B: الحقول البنيوية (المخزن/الوسائط/العنوان).
Phase C: إضافة إعدادات MRKT + Telethon + الصمود (retry/circuit breaker).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _get(key: str, default: str) -> str:
    val = os.getenv(key)
    return val if val is not None and val != "" else default


@dataclass(frozen=True)
class Settings:
    """إعدادات غير قابلة للتعديل بعد التحميل (immutable)."""

    # ── المخزن المستقل للخدمة (ليس store.db الخاص بالبوت) ──
    db_path: str
    media_dir: str
    media_base_url: str
    service_host: str
    service_port: int

    # ── MRKT (Phase C) ──
    mrkt_base_url: str
    mrkt_cdn_url: str
    mrkt_timeout: float
    saling_count: int
    mrkt_auth_app_id: Optional[str]

    # ── Telethon (أسرار من .env فقط؛ تتخلّف إلى فارغ) ──
    tg_api_id: int
    tg_api_hash: str
    tg_assistant_session: str
    mrkt_bot_username: str
    mrkt_app_short_name: str
    mrkt_max_rps: float
    mrkt_burst: int
    mrkt_referer: str
    mrkt_origin: str
    mrkt_user_agent: str
    mrkt_webview_url: str

    # ── الصمود (Phase C) ──
    retry_max: int
    retry_base_delay: float
    retry_max_delay: float
    cb_fail_threshold: int
    cb_reset_timeout: float

    # ── التسعير + العملة + المراقبة (Phase D) ──
    binance_base_url: str
    gram_symbol: str
    gram_price_ttl: float
    default_commission_usd: str
    currency_cache_ttl: float
    default_currency: str
    bot_internal_url: str
    internal_shared_secret: str
    storeroz_bot_token: str
    log_level: str
    sync_interval: float


def load_settings() -> Settings:
    """يبني الإعدادات من البيئة مع قيم افتراضية بنيوية آمنة (بلا أسرار)."""
    app_id = os.getenv("MRKT_AUTH_APP_ID")  # None صراحةً كما في العقد
    return Settings(
        # بنيوي
        db_path=_get("DB_PATH", "data/collectibles.db"),
        media_dir=_get("MEDIA_DIR", "data/media"),
        media_base_url=_get("MEDIA_BASE_URL", ""),
        service_host=_get("SERVICE_HOST", "127.0.0.1"),
        service_port=int(_get("SERVICE_PORT", "8100")),
        # MRKT
        mrkt_base_url=_get("MRKT_API_URL", "https://api.tgmrkt.io"),
        mrkt_cdn_url=_get("MRKT_CDN_URL", "https://cdn.tgmrkt.io"),
        mrkt_timeout=float(_get("MRKT_TIMEOUT", "30")),
        saling_count=int(_get("MRKT_SALING_COUNT", "20")),
        mrkt_auth_app_id=app_id if app_id not in (None, "") else None,
        # Telethon (أسرار)
        tg_api_id=int(_get("TG_API_ID", "0")),
        tg_api_hash=_get("TG_API_HASH", ""),
        tg_assistant_session=_get("TG_ASSISTANT_SESSION", ""),
        mrkt_bot_username=_get("MRKT_BOT_USERNAME", "mrkt"),
        mrkt_app_short_name=_get("MRKT_APP_SHORT_NAME", "app"),
        mrkt_max_rps=float(_get("MRKT_MAX_RPS", "1.0")),
        mrkt_burst=int(_get("MRKT_BURST", "1")),
        mrkt_referer=_get("MRKT_REFERER", "https://cdn.tgmrkt.io/"),
        mrkt_origin=_get("MRKT_ORIGIN", ""),          # المرجع لا يرسلها
        mrkt_user_agent=_get("MRKT_USER_AGENT", ""),  # المرجع لا يرسلها
        mrkt_webview_url=_get("MRKT_WEBVIEW_URL", ""),
        # الصمود
        retry_max=int(_get("RETRY_MAX", "3")),
        retry_base_delay=float(_get("RETRY_BASE_DELAY", "0.5")),
        retry_max_delay=float(_get("RETRY_MAX_DELAY", "8.0")),
        cb_fail_threshold=int(_get("CB_FAIL_THRESHOLD", "5")),
        cb_reset_timeout=float(_get("CB_RESET_TIMEOUT", "60.0")),
        # التسعير + العملة + المراقبة
        binance_base_url=_get("BINANCE_API_URL", "https://api.binance.com"),
        gram_symbol=_get("GRAM_SYMBOL", "GRAMUSDT"),
        gram_price_ttl=float(_get("GRAM_PRICE_TTL", "60")),
        default_commission_usd=_get("DEFAULT_COMMISSION_USD", "0"),
        currency_cache_ttl=float(_get("CURRENCY_CACHE_TTL", "3600")),
        default_currency=_get("DEFAULT_CURRENCY", "USD"),
        bot_internal_url=_get("BOT_INTERNAL_URL", "http://127.0.0.1:8090"),
        internal_shared_secret=_get("INTERNAL_SHARED_SECRET", ""),
        storeroz_bot_token=_get("STOREROZ_BOT_TOKEN", ""),
        log_level=_get("LOG_LEVEL", "INFO"),
        sync_interval=float(_get("SYNC_INTERVAL_SECONDS", "300")),
    )
