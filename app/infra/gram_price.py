"""
جالب سعر GRAM من Binance (GRAMUSDT) مع كاش قصير و backoff.

- يعيد Decimal (دقّة نقدية، لا عوائم).
- كاش بعمر قصير (TTL) لتقليل النداءات.
- إعادة محاولة على الأخطاء العابرة.
- يستخدم تجريد عميل HTTP نفسه (قابل للاختبار بخادم mock دون Binance حيّ).

مستقل عن أي مزوّد مقتنيات (مصدر بيانات سوق عام).
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from app.core.logging_setup import get_logger
from app.core.metrics import Metrics, resolve
from app.infra.http_client import AsyncHttpClient
from app.infra.resilience import ProviderError, TransientError, retry_async

_log = get_logger("gram")


class GramPriceError(ProviderError):
    """تعذّر الحصول على سعر GRAM."""


class GramPrice:
    def __init__(
        self,
        http: AsyncHttpClient,
        *,
        base_url: str = "https://api.binance.com",
        symbol: str = "GRAMUSDT",
        ttl: float = 60.0,
        retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        timeout: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        metrics: Optional[Metrics] = None,
    ):
        self._http = http
        self._base = base_url.rstrip("/")
        self._symbol = symbol
        self._ttl = ttl
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._timeout = timeout
        self._clock = clock
        self._m = resolve(metrics)
        self._value: Optional[Decimal] = None
        self._ts: float = 0.0

    def cached(self) -> Optional[Decimal]:
        return self._value

    async def get(self, force: bool = False) -> Decimal:
        """يعيد سعر GRAMUSDT الحالي (من الكاش إن كان طازجاً)."""
        now = self._clock()
        if not force and self._value is not None and (now - self._ts) < self._ttl:
            self._m.inc("gram_price_cache_hits")
            return self._value
        self._m.inc("gram_price_cache_misses")

        async def _do() -> Decimal:
            with self._m.timer("gram_price_latency"):
                return await self._fetch()

        try:
            price = await retry_async(
                _do,
                retries=self._retries,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
                retry_on=(TransientError,),
                on_retry=lambda a: self._m.inc("gram_price_retries"),
            )
        except Exception:
            # تدهور رشيق: اخدم آخر قيمة معروفة إن وُجدت
            if self._value is not None:
                self._m.inc("gram_price_stale_served")
                _log.warning("gram price fetch failed; serving last known value")
                return self._value
            raise
        self._value = price
        self._ts = now
        return price

    async def _fetch(self) -> Decimal:
        url = f"{self._base}/api/v3/ticker/price?symbol={self._symbol}"
        resp = await self._http.request("GET", url, timeout=self._timeout)
        if resp.status == 429 or resp.status >= 500:
            raise TransientError(f"Binance حالة عابرة {resp.status}")
        if resp.status >= 400:
            raise GramPriceError(f"Binance حالة {resp.status}")
        data = resp.json() or {}
        raw = data.get("price")
        if raw is None:
            raise GramPriceError("استجابة Binance بلا حقل price")
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise GramPriceError(f"سعر غير صالح: {raw!r}") from exc
