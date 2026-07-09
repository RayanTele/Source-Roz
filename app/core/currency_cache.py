"""
كاش عملة المستخدم داخل الخدمة (Currency Cache).

يخزّن user_id → (currency, timestamp) بعمر TTL. يسأل مزوّد العملة (البوت)
فقط عند الفقد أو القِدَم، ثم يخزّن. عند فشل المزوّد يخدم القيمة القديمة إن
وُجدت، وإلا العملة الافتراضية. يقلّل الاتصال الداخلي للحدّ الأدنى.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional, Tuple

from app.core.metrics import Metrics, resolve


class CurrencyProvider(ABC):
    """مصدر عملة المستخدم (يُنفَّذ إنتاجياً بنداء داخلي للبوت)."""

    @abstractmethod
    async def get_currency(self, user_id: int) -> str:
        raise NotImplementedError


class CurrencyCache:
    def __init__(
        self,
        provider: CurrencyProvider,
        *,
        ttl: float = 3600.0,
        default_currency: str = "USD",
        clock: Callable[[], float] = time.monotonic,
        metrics: Optional[Metrics] = None,
    ):
        self._provider = provider
        self._ttl = ttl
        self._default = default_currency
        self._clock = clock
        self._m = resolve(metrics)
        self._lock = threading.Lock()
        self._cache: Dict[int, Tuple[str, float]] = {}

    async def get(self, user_id: int) -> str:
        now = self._clock()
        with self._lock:
            entry = self._cache.get(user_id)
        if entry is not None and (now - entry[1]) < self._ttl:
            self._m.inc("currency_cache_hits")
            return entry[0]

        self._m.inc("currency_cache_misses")
        try:
            currency = await self._provider.get_currency(user_id)
        except Exception:
            # فشل المزوّد: اخدم القيمة القديمة إن وُجدت، وإلا الافتراضية
            self._m.inc("currency_provider_failures")
            if entry is not None:
                return entry[0]
            return self._default

        currency = currency or self._default
        with self._lock:
            self._cache[user_id] = (currency, now)
        return currency

    def invalidate(self, user_id: int) -> None:
        with self._lock:
            self._cache.pop(user_id, None)

    def size(self) -> int:
        with self._lock:
            return len(self._cache)
