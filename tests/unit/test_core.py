"""اختبارات وحدة للصمود، المقاييس، كاش العملة، سعر GRAM، المِحوَل، النموذج."""
import unittest
from decimal import Decimal

from app.core.metrics import Metrics, NullMetrics, CB_STATE_CODE
from app.core.models import CanonicalCollectible
from app.core.currency_cache import CurrencyCache, CurrencyProvider
from app.infra.gram_price import GramPrice, GramPriceError
from app.infra.http_client import HttpResponse
from app.infra.resilience import (
    CircuitBreaker, CircuitOpenError, TransientError, retry_async,
)
from app.providers.mrkt.v1 import mapper


# ── أدوات مساعدة ──
class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


class MockHttp:
    """عميل HTTP وهمي يعيد استجابات مبرمجة بالتسلسل."""
    def __init__(self, responses): self.responses = list(responses); self.calls = 0
    async def request(self, method, url, *, json=None, headers=None, timeout=30.0):
        r = self.responses[min(self.calls, len(self.responses) - 1)]; self.calls += 1
        return r
    async def close(self): pass


async def _noslee_p(_): return None


class TestMetrics(unittest.TestCase):
    def test_counters_gauges_timers(self):
        m = Metrics()
        m.inc("a"); m.inc("a", 2); m.gauge("g", 5); m.observe("t", 1.0); m.observe("t", 3.0)
        s = m.snapshot()
        self.assertEqual(s["counters"]["a"], 3)
        self.assertEqual(s["gauges"]["g"], 5)
        self.assertEqual(s["timers"]["t"]["count"], 2)
        self.assertEqual(s["timers"]["t"]["max"], 3.0)

    def test_null_metrics_noop(self):
        m = NullMetrics(); m.inc("x"); m.gauge("y", 1); m.observe("z", 1)
        self.assertEqual(m.snapshot()["counters"], {})

    def test_cb_state_codes(self):
        self.assertEqual(CB_STATE_CODE["closed"], 0)
        self.assertEqual(CB_STATE_CODE["open"], 2)


class TestCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    async def test_opens_and_recovers(self):
        clk = FakeClock()
        cb = CircuitBreaker(fail_threshold=2, reset_timeout=10, clock=clk)

        async def boom(): raise TransientError("x")
        async def ok(): return "ok"

        for _ in range(2):
            with self.assertRaises(TransientError):
                await cb.call(boom)
        self.assertEqual(cb.state, "open")
        with self.assertRaises(CircuitOpenError):
            await cb.call(ok)
        clk.advance(11)  # بعد المهلة → half_open
        self.assertEqual(cb.state, "half_open")
        self.assertEqual(await cb.call(ok), "ok")  # نجاح → closed
        self.assertEqual(cb.state, "closed")


class TestRetry(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds_and_counts(self):
        attempts = {"n": 0}; retried = {"n": 0}
        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3: raise TransientError("t")
            return "done"
        out = await retry_async(
            flaky, retries=5, base_delay=0, max_delay=0, jitter=0,
            sleep=_noslee_p, on_retry=lambda a: retried.__setitem__("n", retried["n"] + 1),
        )
        self.assertEqual(out, "done")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(retried["n"], 2)

    async def test_no_retry_on_non_transient(self):
        async def bad(): raise ValueError("nope")
        with self.assertRaises(ValueError):
            await retry_async(bad, retries=3, sleep=_noslee_p)


class FakeCurrencyProvider(CurrencyProvider):
    def __init__(self, currency="YER", fail=False):
        self.currency = currency; self.fail = fail; self.calls = 0
    async def get_currency(self, user_id):
        self.calls += 1
        if self.fail: raise RuntimeError("down")
        return self.currency


class TestCurrencyCache(unittest.IsolatedAsyncioTestCase):
    async def test_caches_and_ttl(self):
        clk = FakeClock(); p = FakeCurrencyProvider("YER")
        cc = CurrencyCache(p, ttl=100, clock=clk)
        self.assertEqual(await cc.get(1), "YER")
        self.assertEqual(await cc.get(1), "YER")  # من الكاش
        self.assertEqual(p.calls, 1)              # لم يُسأل مرتين
        clk.advance(101)
        self.assertEqual(await cc.get(1), "YER")  # قِدَم → سؤال جديد
        self.assertEqual(p.calls, 2)

    async def test_serves_stale_on_failure(self):
        clk = FakeClock(); p = FakeCurrencyProvider("SAR")
        cc = CurrencyCache(p, ttl=10, clock=clk)
        await cc.get(7)                # مخزّن SAR
        p.fail = True; clk.advance(11) # قِدَم + فشل
        self.assertEqual(await cc.get(7), "SAR")  # قديم يُخدَم

    async def test_default_on_failure_no_cache(self):
        p = FakeCurrencyProvider(fail=True)
        cc = CurrencyCache(p, ttl=10, default_currency="USD")
        self.assertEqual(await cc.get(99), "USD")


class TestGramPrice(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_parses_decimal_and_caches(self):
        clk = FakeClock()
        http = MockHttp([HttpResponse(200, _json={"symbol": "GRAMUSDT", "price": "2.0567"})])
        gp = GramPrice(http, ttl=60, clock=clk)
        v = await gp.get()
        self.assertEqual(v, Decimal("2.0567"))
        await gp.get()  # من الكاش
        self.assertEqual(http.calls, 1)
        clk.advance(61)
        await gp.get()  # انتهى الكاش → نداء جديد
        self.assertEqual(http.calls, 2)

    async def test_retries_on_5xx(self):
        http = MockHttp([
            HttpResponse(503, _json={}),
            HttpResponse(200, _json={"price": "1.5"}),
        ])
        gp = GramPrice(http, ttl=60, base_delay=0, max_delay=0)
        # نستبدل النوم داخل retry عبر base/max=0؛ jitter صغير مقبول
        v = await gp.get()
        self.assertEqual(v, Decimal("1.5"))

    async def test_bad_status_raises(self):
        http = MockHttp([HttpResponse(400, _json={})])
        gp = GramPrice(http, ttl=60)
        with self.assertRaises(GramPriceError):
            await gp.get()


class TestMapperModel(unittest.TestCase):
    RAW = {
        "id": "u-1", "giftIdString": "999", "giftId": 999, "title": "T",
        "collectionName": "C", "modelName": "M", "backdropName": "B", "symbolName": "S",
        "number": 42, "modelStickerThumbnailKey": "k.webp", "salePrice": 3000000000,
        "isOnSale": True, "isLocked": False, "unlockDate": "2026-07-23T00:00:00Z",
    }

    def test_mapper_fields(self):
        c = mapper.map_gift(self.RAW)
        self.assertEqual(c.provider, "mrkt")
        self.assertEqual(c.provider_item_id, "u-1")
        self.assertEqual(c.external_id, "999")
        self.assertEqual(c.price_nano, 3000000000)
        self.assertTrue(c.is_on_sale)
        self.assertFalse(c.is_locked)
        self.assertEqual(c.media_key, "k.webp")
        self.assertGreater(c.synced_at, 0)

    def test_model_row_roundtrip(self):
        c = mapper.map_gift(self.RAW)
        back = CanonicalCollectible.from_row(c.to_row())
        self.assertEqual(back.external_id, "999")
        self.assertEqual(back.price_nano, 3000000000)
        self.assertEqual(back.raw.get("salePrice"), 3000000000)


if __name__ == "__main__":
    unittest.main()
