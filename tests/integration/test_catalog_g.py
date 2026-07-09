"""تكامل: إضافات كتالوج Phase G (categories/facets/sort/بحث/نطاق سعر/مقفول)."""
import os
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.core.catalog_service import CatalogService
from app.core.currency_cache import CurrencyCache, CurrencyProvider
from app.core.metrics import Metrics
from app.core.models import CanonicalCollectible
from app.core.pricing_service import PricingService
from app.infra.cache_sqlite import SQLiteCacheRepository
from app.infra.gram_price import GramPrice
from app.infra.http_client import HttpResponse
from app.infra.public_api import PublicApi, attach_public_routes
from tests.unit.test_api_privacy import make_init_data, TOKEN


class _Http:
    async def request(self, *a, **k):
        return HttpResponse(200, _json={"price": "2.0"})
    async def close(self): pass


class _Cur(CurrencyProvider):
    def __init__(self, repo): self._repo = repo
    async def get_currency(self, uid):
        self._repo.upsert_rates({"USD": 1.0}); return "USD"


def _build():
    d = tempfile.mkdtemp()
    repo = SQLiteCacheRepository(os.path.join(d, "c.db")); repo.init_schema()
    import time
    items = [
        CanonicalCollectible("mrkt", "a", "1", title="Alpha Gift", collection="Rockets",
                             model="Neo", backdrop="Amber", symbol="Star",
                             price_nano=5_000_000_000, is_on_sale=True, number=10),
        CanonicalCollectible("mrkt", "b", "2", title="Beta Gift", collection="Hearts",
                             model="Retro", backdrop="Onyx", symbol="Moon",
                             price_nano=1_000_000_000, is_on_sale=True, is_locked=True, number=20),
        CanonicalCollectible("mrkt", "c", "3", title="Gamma Gift", collection="Rockets",
                             model="Neo", backdrop="Amber", symbol="Star",
                             price_nano=9_000_000_000, is_on_sale=True, number=30),
    ]
    for it in items: it.stamp()
    repo.upsert_items(items)
    m = Metrics()
    pricing = PricingService(repo, GramPrice(_Http(), ttl=60, metrics=m),
                             CurrencyCache(_Cur(repo), ttl=100), default_commission_usd="0")
    api = PublicApi(CatalogService(repo, pricing), TOKEN)
    app = web.Application(); attach_public_routes(app, api)
    return app


HDR = {"Authorization": "tma " + make_init_data(TOKEN, 5)}


class TestCatalogG(unittest.IsolatedAsyncioTestCase):
    async def test_categories_and_facets(self):
        async with TestClient(TestServer(_build())) as c:
            cats = await (await c.get("/api/v1/categories", headers=HDR)).json()
            ids = [x["id"] for x in cats["categories"]]
            self.assertIn("cheapest", ids); self.assertIn("collections", ids)
            facets = await (await c.get("/api/v1/facets", headers=HDR)).json()
            self.assertIn("Rockets", facets["collections"])
            self.assertIn("Amber", facets["backdrops"])

    async def test_sort_cheapest_expensive(self):
        async with TestClient(TestServer(_build())) as c:
            cheap = await (await c.get("/api/v1/collectibles?sort=cheapest", headers=HDR)).json()
            self.assertEqual([i["id"] for i in cheap["items"]], ["b", "a", "c"])
            exp = await (await c.get("/api/v1/collectibles?sort=expensive", headers=HDR)).json()
            self.assertEqual([i["id"] for i in exp["items"]][0], "c")

    async def test_search_and_locked_and_collection(self):
        async with TestClient(TestServer(_build())) as c:
            s = await (await c.get("/api/v1/collectibles?q=Beta", headers=HDR)).json()
            self.assertEqual([i["id"] for i in s["items"]], ["b"])
            num = await (await c.get("/api/v1/collectibles?q=30", headers=HDR)).json()
            self.assertEqual([i["id"] for i in num["items"]], ["c"])
            lk = await (await c.get("/api/v1/collectibles?locked=1", headers=HDR)).json()
            self.assertEqual([i["id"] for i in lk["items"]], ["b"])
            col = await (await c.get("/api/v1/collectibles?collection=Rockets&sort=cheapest", headers=HDR)).json()
            self.assertEqual([i["id"] for i in col["items"]], ["a", "c"])

    async def test_price_range_in_customer_currency(self):
        # USD, gram=2.0, commission=0 => price_usd = nano/1e9 * 2
        # a=5n->10$, b=1n->2$, c=9n->18$ ; range 5..12 => only a(10)
        async with TestClient(TestServer(_build())) as c:
            r = await (await c.get("/api/v1/collectibles?price_min=5&price_max=12", headers=HDR)).json()
            self.assertEqual([i["id"] for i in r["items"]], ["a"])


if __name__ == "__main__":
    unittest.main()
