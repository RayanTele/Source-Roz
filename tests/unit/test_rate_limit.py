"""اختبارات: محدِّد المعدّل، احترام Retry-After، وترويسات مطابقة العميل الرسمي."""
import unittest

from app.infra.http_client import HttpResponse
from app.infra.resilience import (
    AsyncRateLimiter,
    RateLimitError,
    TransientError,
    retry_async,
)
from app.providers.mrkt.v1.client import MrktClient
from app.providers.mrkt.v1.token_manager import TokenManager, InitDataProvider


class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_enforces_rate_ceiling(self):
        clk = FakeClock(); slept = []
        async def sleep(s): slept.append(s); clk.advance(s)
        # 2 rps, burst 1 → الطلب الثاني ينتظر ~0.5s
        lim = AsyncRateLimiter(rate=2.0, burst=1, clock=clk, sleep=sleep)
        await lim.acquire()                 # فوري (الدلو ممتلئ)
        self.assertEqual(slept, [])
        await lim.acquire()                 # ينتظر
        self.assertTrue(slept and abs(sum(slept) - 0.5) < 0.05, slept)

    async def test_burst_allows_initial_batch(self):
        clk = FakeClock(); slept = []
        async def sleep(s): slept.append(s); clk.advance(s)
        lim = AsyncRateLimiter(rate=1.0, burst=3, clock=clk, sleep=sleep)
        for _ in range(3):
            await lim.acquire()
        self.assertEqual(slept, [])          # الثلاثة ضمن الدفعة
        await lim.acquire()
        self.assertTrue(slept)               # الرابع ينتظر

    async def test_pause_blocks_until_retry_after(self):
        clk = FakeClock(); slept = []
        async def sleep(s): slept.append(s); clk.advance(s)
        lim = AsyncRateLimiter(rate=100.0, burst=10, clock=clk, sleep=sleep)
        lim.pause(30.0)                      # كما يحدث عند 429 Retry-After: 30
        await lim.acquire()
        self.assertGreaterEqual(sum(slept), 30.0)

    def test_snapshot(self):
        lim = AsyncRateLimiter(rate=1.5, burst=2)
        s = lim.snapshot()
        self.assertEqual(s["rate"], 1.5); self.assertEqual(s["burst"], 2)


class TestRetryAfterHonored(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_used_instead_of_backoff(self):
        slept = []
        async def sleep(s): slept.append(s)
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RateLimitError("429", retry_after=37.0)
            return "ok"
        out = await retry_async(fn, retries=3, base_delay=0.5, sleep=sleep)
        self.assertEqual(out, "ok")
        self.assertEqual(slept, [37.0])      # لا backoff أُسّي — بل المدّة المطلوبة

    async def test_retry_after_capped(self):
        slept = []
        async def sleep(s): slept.append(s)
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            if calls["n"] == 1: raise RateLimitError("429", retry_after=99999.0)
            return "ok"
        await retry_async(fn, retries=2, sleep=sleep, retry_after_cap=300.0)
        self.assertEqual(slept, [300.0])

    async def test_backoff_when_no_retry_after(self):
        slept = []
        async def sleep(s): slept.append(s)
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            if calls["n"] < 3: raise RateLimitError("429", retry_after=None)
            return "ok"
        await retry_async(fn, retries=3, base_delay=1.0, max_delay=8.0, jitter=0.0, sleep=sleep)
        self.assertEqual(slept, [1.0, 2.0])  # أُسّي
        # لا إعادة محاولة فورية إطلاقاً
        self.assertTrue(all(s > 0 for s in slept))

    async def test_rate_limit_error_is_transient(self):
        self.assertTrue(issubclass(RateLimitError, TransientError))


class TestClient429(unittest.TestCase):
    def test_429_raises_rate_limit_error_and_pauses_limiter(self):
        lim = AsyncRateLimiter(rate=10.0, burst=5)
        resp = HttpResponse(429, body=b"if you need more rps, please contact the support",
                            headers={"Retry-After": "42", "Server": "cloudflare"})
        with self.assertRaises(RateLimitError) as cm:
            MrktClient._raise_for_status(resp, limiter=lim)
        self.assertEqual(cm.exception.retry_after, 42.0)
        self.assertGreater(lim.snapshot()["paused_for"], 40)

    def test_429_without_retry_after_uses_conservative_pause(self):
        lim = AsyncRateLimiter(rate=10.0, burst=5)
        resp = HttpResponse(429, body=b"rps", headers={"Server": "cloudflare"})
        with self.assertRaises(RateLimitError) as cm:
            MrktClient._raise_for_status(resp, limiter=lim)
        self.assertIsNone(cm.exception.retry_after)
        self.assertGreater(lim.snapshot()["paused_for"], 0)

    def test_parse_retry_after_case_insensitive(self):
        self.assertEqual(MrktClient._parse_retry_after(HttpResponse(429, headers={"retry-after": "9"})), 9.0)
        self.assertIsNone(MrktClient._parse_retry_after(HttpResponse(429, headers={"Retry-After": "Wed, 21 Oct"})))
        self.assertIsNone(MrktClient._parse_retry_after(HttpResponse(429, headers={})))


class _IDP(InitDataProvider):
    async def get_init_data(self): return "x"


class TestHeaderParity(unittest.TestCase):
    def test_client_sends_official_headers(self):
        class _H:
            async def request(self, *a, **k): pass
            async def close(self): pass
        c = MrktClient(_H(), "https://api.tgmrkt.io", TokenManager(_H(), "https://api.tgmrkt.io", _IDP()))
        h = c._headers("TOKEN-UUID")
        self.assertEqual(h["Authorization"], "TOKEN-UUID")     # خام بلا Bearer
        self.assertEqual(h["Referer"], "https://cdn.tgmrkt.io/")
        self.assertEqual(h["Origin"], "https://cdn.tgmrkt.io")
        self.assertIn("Mozilla/5.0", h["User-Agent"])

    def test_token_manager_auth_headers(self):
        class _H:
            async def request(self, *a, **k): pass
            async def close(self): pass
        tm = TokenManager(_H(), "https://api.tgmrkt.io", _IDP())
        self.assertEqual(tm._auth_headers["Referer"], "https://cdn.tgmrkt.io/")
        self.assertIn("Mozilla/5.0", tm._auth_headers["User-Agent"])


if __name__ == "__main__":
    unittest.main()
