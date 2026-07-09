"""
حزمة مزوّد MRKT (إضافة/plugin).

register() يسجّل مصنعاً كسولاً باسم "mrkt". build_mrkt_provider() يسمح للـ
bootstrap ببناء المزوّد بمقاييس/عميل HTTP مشتركين (للمقاييس المجمّعة وإغلاق
موحّد). لا يتطلّب التسجيل aiohttp/telethon/أسراراً (كلها مؤجّلة).
"""
from __future__ import annotations

from typing import Optional

from app.providers import register_provider


def _wire(settings, metrics, http):
    """يربط طبقات MRKT فوق عميل HTTP ومقاييس مُعطَيَين."""
    from app.infra.resilience import CircuitBreaker
    from app.providers.mrkt.v1.adapter import MrktProvider
    from app.providers.mrkt.v1.client import MrktClient
    from app.providers.mrkt.v1.token_manager import (
        TelethonInitDataProvider,
        TokenManager,
    )

    idp = TelethonInitDataProvider(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        session=settings.tg_assistant_session,
        bot_username=settings.mrkt_bot_username,
        webview_url=settings.mrkt_webview_url,
        app_short_name=settings.mrkt_app_short_name,
    )
    tokens = TokenManager(
        http, settings.mrkt_base_url, idp,
        app_id=settings.mrkt_auth_app_id, timeout=settings.mrkt_timeout, metrics=metrics,
    )
    breaker = CircuitBreaker(
        fail_threshold=settings.cb_fail_threshold, reset_timeout=settings.cb_reset_timeout
    )
    client = MrktClient(
        http, settings.mrkt_base_url, tokens,
        breaker=breaker, retries=settings.retry_max,
        base_delay=settings.retry_base_delay, max_delay=settings.retry_max_delay,
        timeout=settings.mrkt_timeout, saling_count=settings.saling_count, metrics=metrics,
    )
    return MrktProvider(client, tokens, metrics=metrics)


def build_mrkt_provider(settings, metrics, http):
    """يبني مزوّد MRKT بعميل HTTP ومقاييس مشتركين (يستخدمه bootstrap)."""
    return _wire(settings, metrics, http)


def _build_default():
    """مصنع الإضافة الافتراضي (get_provider) — ينشئ عميلاً ومقاييس خاصّين به."""
    from app.config import load_settings
    from app.core.metrics import Metrics
    from app.infra.http_client import AiohttpClient

    s = load_settings()
    return _wire(s, Metrics(), AiohttpClient(timeout=s.mrkt_timeout))


def register() -> None:
    register_provider("mrkt", _build_default)
