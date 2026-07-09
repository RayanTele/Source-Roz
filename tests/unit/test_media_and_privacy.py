"""اختبار خصوصية العمولة + خدمة الوسائط (وحدة)."""
import json
import unittest
from decimal import Decimal

from app.core.media_service import MediaService
from app.core.pricing_engine import PricingEngine
from app.infra.http_client import HttpResponse
from app.infra.media_fs import FilesystemMediaStore
import tempfile


class TestCommissionPrivacy(unittest.TestCase):
    def _breakdown(self):
        return PricingEngine.price(2_000_000_000, "2.05", "1.75", "YER", "540")

    def test_customer_view_hides_commission_and_internals(self):
        cv = self._breakdown().customer_view()
        blob = json.dumps(cv).lower()
        for forbidden in ("commission", "usd_base", "usd_total", "gram", "rate"):
            self.assertNotIn(forbidden, blob, f"{forbidden} leaked to customer view")
        # يحوي فقط العملة والسعر النهائي
        self.assertEqual(set(cv.keys()), {"currency", "amount", "amount_exact"})
        self.assertEqual(cv["currency"], "YER")

    def test_customer_view_amount_is_final_only(self):
        b = self._breakdown()
        cv = b.customer_view()
        # (2*2.05 + 1.75) * 540 = (4.10+1.75)*540 = 5.85*540 = 3159
        self.assertEqual(Decimal(cv["amount_exact"]), Decimal("3159.00"))

    def test_internal_dict_still_has_commission_for_admin(self):
        d = self._breakdown().to_dict()
        self.assertIn("commission_usd", d)  # داخلي فقط — لا يُرسَل لعميل


class TestMediaService(unittest.IsolatedAsyncioTestCase):
    class _Http:
        def __init__(self, status=200, body=b"IMG"):
            self.status = status; self.body = body; self.calls = 0
        async def request(self, method, url, *, json=None, headers=None, timeout=30.0):
            self.calls += 1
            return HttpResponse(self.status, body=self.body)
        async def close(self): pass

    async def test_fetch_then_cache_hit(self):
        store = FilesystemMediaStore(tempfile.mkdtemp())
        http = self._Http()
        ms = MediaService(store, http, cdn_base_url="https://cdn.x", media_base_url="https://d")
        key = "gifts/t/abc.webp"
        url = await ms.ensure(key)
        self.assertEqual(url, "https://d/media/gifts/t/abc.webp")
        self.assertTrue(store.has(key))
        self.assertEqual(http.calls, 1)
        await ms.ensure(key)                 # الآن من الكاش
        self.assertEqual(http.calls, 1)      # لا جلب ثانٍ

    async def test_fetch_failure_is_soft(self):
        store = FilesystemMediaStore(tempfile.mkdtemp())
        http = self._Http(status=404)
        ms = MediaService(store, http, cdn_base_url="https://cdn.x")
        url = await ms.ensure("k.webp")      # يفشل لكن لا يرفع
        self.assertEqual(url, "/media/k.webp")
        self.assertFalse(store.has("k.webp"))

    async def test_empty_key(self):
        store = FilesystemMediaStore(tempfile.mkdtemp())
        ms = MediaService(store, self._Http(), cdn_base_url="https://cdn.x")
        self.assertEqual(await ms.ensure(""), "")


if __name__ == "__main__":
    unittest.main()
