"""تكامل: خدمة التسعير من طرف إلى طرف (كاش + أسعار + عمولة + GRAM + عملة)."""
import os
import tempfile
import unittest
from decimal import Decimal

from app.core.currency_cache import CurrencyCache, CurrencyProvider
from app.core.models import CanonicalCollectible
from app.core.pricing_service import PricingService
from app.infra.cache_sqlite import SQLiteCacheRepository
from app.infra.gram_price import GramPrice
from app.infra.http_client import HttpResponse


class _Http:
    def __init__(self, price): self.price = price; self.calls = 0
    async def request(self, method, url, *, json=None, headers=None, timeout=30.0):
        self.calls += 1
        return HttpResponse(200, _json={"symbol": "GRAMUSDT", "price": self.price})
    async def close(self): pass


class _Currency(CurrencyProvider):
    def __init__(self, cur): self.cur = cur
    async def get_currency(self, user_id): return self.cur


class TestPricingServiceIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        d = tempfile.mkdtemp()
        self.repo = SQLiteCacheRepository(os.path.join(d, "c.db"))
        self.repo.init_schema()
        self.repo.upsert_rates({"YER": 540.0, "USD": 1.0})
        self.gram = GramPrice(_Http("2.05"), ttl=60)
        self.cc = CurrencyCache(_Currency("YER"), ttl=100)
        self.svc = PricingService(
            self.repo, self.gram, self.cc,
            default_commission_usd="1", default_currency="USD",
        )

    async def test_price_item_matches_expected(self):
        # 2 GRAM، سعر 2.05$، عمولة 1$، YER=540 → (2*2.05 + 1) * 540 = 2754
        item = CanonicalCollectible("mrkt", "id1", "999", price_nano=2_000_000_000, is_on_sale=True)
        b = await self.svc.price_item(item, user_id=1770929898)
        self.assertEqual(b.currency, "YER")
        self.assertEqual(b.usd_total, Decimal("5.10"))
        self.assertEqual(b.final_amount, Decimal("2754.00"))

    async def test_commission_persists_in_cache_meta(self):
        self.svc.set_commission_usd(Decimal("2.5"))
        self.assertEqual(self.repo.get_meta("commission_usd"), "2.5")
        # خدمة جديدة تقرأ نفس المخزن → نفس العمولة (مصدر الحقيقة = الكاش)
        svc2 = PricingService(self.repo, self.gram, self.cc, default_commission_usd="0")
        self.assertEqual(svc2.get_commission_usd(), Decimal("2.5"))

    async def test_unknown_currency_falls_back_to_usd_rate(self):
        item = CanonicalCollectible("mrkt", "id2", "1", price_nano=1_000_000_000, is_on_sale=True)
        b = await self.svc.price_in_currency(item, "XXX")  # غير موجودة في النسخة
        self.assertEqual(b.rate, Decimal("1"))
        self.assertEqual(b.final_amount, Decimal("3.05"))  # (1*2.05+1)*1


if __name__ == "__main__":
    unittest.main()
