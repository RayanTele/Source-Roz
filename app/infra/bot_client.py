"""
مزوّد عملة المستخدم عبر البوت (BotCurrencyProvider).

يستدعي نقطة البوت الداخلية GET /internal/user/{id}/currency (localhost + سرّ
مشترك)، التي تُرجع {"currency": <code>, "rate": <float>}. يعيد العملة، ويكتب
سعرها في نسخة الأسعار المحلية (cache_repo) — فيبقى البوت السلطة الوحيدة
للأسعار وتبقى CurrencyCache وPricingService دون تغيير.

مستقل عن أي مزوّد مقتنيات.
"""
from __future__ import annotations

from typing import Optional

from app.core.currency_cache import CurrencyProvider
from app.infra.http_client import AsyncHttpClient
from app.infra.resilience import ProviderError
from app.ports.cache_repository import CacheRepository


class BotCurrencyProvider(CurrencyProvider):
    def __init__(
        self,
        http: AsyncHttpClient,
        bot_url: str,
        secret: str,
        default_currency: str = "USD",
        cache_repo: Optional[CacheRepository] = None,
        timeout: float = 10.0,
    ):
        self._http = http
        self._base = bot_url.rstrip("/")
        self._secret = secret
        self._default = default_currency
        self._repo = cache_repo
        self._timeout = timeout

    async def get_currency(self, user_id: int) -> str:
        resp = await self._http.request(
            "GET",
            f"{self._base}/internal/user/{int(user_id)}/currency",
            headers={"X-Internal-Secret": self._secret},
            timeout=self._timeout,
        )
        if resp.status >= 400:
            raise ProviderError(f"نقطة عملة البوت أرجعت {resp.status}")
        data = resp.json() or {}
        currency = data.get("currency") or self._default
        # اكتب سعر الصرف في النسخة المحلية (البوت هو المصدر الموثوق)
        rate = data.get("rate")
        if rate is not None and self._repo is not None:
            try:
                self._repo.upsert_rates({currency: float(rate)})
            except (TypeError, ValueError):
                pass
        return currency
