"""
أدوات الصمود (Resilience): إعادة المحاولة بـ backoff أُسّي + قاطع دائرة.

مستقلة عن أي مزوّد؛ يستخدمها عميل MRKT (وأي مزوّد مستقبلي) لتحمّل
الأعطال العابرة مع إبقاء الكاش مصدر الحقيقة عند فتح القاطع.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, Iterable, Optional, Type, TypeVar

T = TypeVar("T")


class ProviderError(Exception):
    """خطأ عام من طبقة المزوّد."""


class TransientError(ProviderError):
    """خطأ عابر يستحق إعادة المحاولة (شبكة/5xx/429)."""


class AuthError(ProviderError):
    """فشل مصادقة (401) — يُعالَج بتجديد التوكن لا بإعادة المحاولة العمياء."""


class CircuitOpenError(ProviderError):
    """القاطع مفتوح — نفشل بسرعة ونترك الكاش يخدم."""


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: float = 0.3,
    retry_on: Iterable[Type[BaseException]] = (TransientError,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Optional[Callable[[int], None]] = None,
) -> T:
    """
    ينفّذ fn ويعيد المحاولة على الأخطاء العابرة فقط، مع تأخير أُسّي + jitter.
    لا يعيد المحاولة على أخطاء غير مدرجة في retry_on (مثل AuthError/4xx).
    on_retry(attempt) يُستدعى قبل كل انتظار (للمقاييس).
    """
    attempt = 0
    retry_on = tuple(retry_on)
    while True:
        try:
            return await fn()
        except retry_on as exc:  # type: ignore[misc]
            attempt += 1
            if attempt > retries:
                raise
            if on_retry is not None:
                on_retry(attempt)
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, jitter * delay)
            _ = exc
            await sleep(delay)


class CircuitBreaker:
    """
    قاطع دائرة بثلاث حالات: CLOSED / OPEN / HALF_OPEN.

    - CLOSED: يمرّر الطلبات؛ بعد fail_threshold إخفاقات متتالية → OPEN.
    - OPEN: يفشل بسرعة (CircuitOpenError) حتى انقضاء reset_timeout → HALF_OPEN.
    - HALF_OPEN: يسمح بمحاولة واحدة؛ نجاحها → CLOSED، فشلها → OPEN.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        fail_threshold: int = 5,
        reset_timeout: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.fail_threshold = fail_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        # انتقال تلقائي OPEN → HALF_OPEN عند انقضاء المهلة
        if self._state == self.OPEN and (self._clock() - self._opened_at) >= self.reset_timeout:
            self._state = self.HALF_OPEN
        return self._state

    def _on_success(self) -> None:
        self._failures = 0
        self._state = self.CLOSED

    def _on_failure(self) -> None:
        self._failures += 1
        if self._state == self.HALF_OPEN or self._failures >= self.fail_threshold:
            self._state = self.OPEN
            self._opened_at = self._clock()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """ينفّذ fn عبر القاطع؛ يرفع CircuitOpenError إن كان مفتوحاً."""
        st = self.state
        if st == self.OPEN:
            raise CircuitOpenError("circuit open — serving from cache")
        try:
            result = await fn()
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def snapshot(self) -> dict:
        """لقطة للصحة/المقاييس."""
        return {
            "state": self.state,
            "failures": self._failures,
            "opened_at": self._opened_at,
        }
