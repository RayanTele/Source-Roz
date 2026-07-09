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


class RateLimitError(TransientError):
    """
    حدّ معدّل من المزوّد (429). يحمل مدّة الانتظار المطلوبة إن أرسلها الخادم
    في ترويسة Retry-After. يرث TransientError كي تبقى سياسة إعادة المحاولة
    القائمة كما هي دون تغيير في المنطق العام.
    """

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class AsyncRateLimiter:
    """
    محدِّد معدّل داخلي (token bucket) يمنع تجاوز الحدّ المسموح لدى المزوّد.

    - rate: أقصى عدد طلبات في الثانية (rps).
    - burst: عدد الطلبات المسموح بها دفعةً واحدة (سعة الدلو).
    - pause(seconds): يوقف الإصدار مؤقتاً (يُستخدم عند Retry-After).

    آمن للتزامن (قفل asyncio). لا يغيّر أي واجهة عامة.
    """

    def __init__(
        self,
        rate: float = 1.0,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._rate = max(0.01, float(rate))
        self._burst = max(1, int(burst))
        self._tokens = float(self._burst)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    def pause(self, seconds: float) -> None:
        """يمنع أي طلب جديد لمدة seconds (احترام Retry-After)."""
        if seconds and seconds > 0:
            self._paused_until = max(self._paused_until, self._clock() + float(seconds))

    async def acquire(self) -> None:
        """ينتظر حتى يُسمح بإصدار طلب واحد."""
        while True:
            async with self._lock:
                now = self._clock()
                # مرحلة الإيقاف المؤقّت (Retry-After)
                wait_pause = self._paused_until - now
                if wait_pause <= 0:
                    # تعبئة الدلو
                    elapsed = now - self._updated
                    self._updated = now
                    self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) / self._rate
                else:
                    wait = wait_pause
            await self._sleep(max(wait, 0.01))

    def snapshot(self) -> dict:
        return {
            "rate": self._rate,
            "burst": self._burst,
            "tokens": round(self._tokens, 3),
            "paused_for": max(0.0, round(self._paused_until - self._clock(), 2)),
        }


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
    retry_after_cap: float = 300.0,
) -> T:
    """
    ينفّذ fn ويعيد المحاولة على الأخطاء العابرة فقط، مع تأخير أُسّي + jitter.
    لا يعيد المحاولة على أخطاء غير مدرجة في retry_on (مثل AuthError/4xx).

    إن حمل الاستثناء سمة retry_after (مثل RateLimitError عند 429)، نحترمها
    وننتظر تلك المدة بالضبط بدل التأخير الأُسّي — مع سقف أمان retry_after_cap.
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
            hinted = getattr(exc, "retry_after", None)
            if hinted:
                # احترام Retry-After كما أرسله الخادم (مع سقف أمان)
                delay = min(float(hinted), retry_after_cap)
            else:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, jitter * delay)
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
