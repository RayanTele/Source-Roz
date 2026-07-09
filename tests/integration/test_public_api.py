"""تكامل: واجهة الكتالوج العامة (HTTP حقيقي) + جسر عملة البوت (stdlib)."""
import json
import os
import sys
import tempfile
import unittest
import urllib.request

from aiohttp.test_utils import TestClient, TestServer

from app.core.catalog_service import CatalogService
from app.core.currency_cache import CurrencyCache, CurrencyProvider
from app.core.media_service import MediaService
from app.core.metrics import Metrics
from app.core.models import CanonicalCollectible
from app.core.pricing_service import PricingService
from app.infra.cache_sqlite import SQLiteCacheRepository
from app.infra.gram_price import GramPrice
from app.infra.http_client import HttpResponse
from app.infra.media_fs import FilesystemMediaStore
from app.infra.public_api import PublicApi, attach_public_routes
from tests.unit.test_api_privacy import make_init_data, TOKEN

# جسر البوت (المكتبة القياسية) — من مجلد bot_integration
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot_integration"))
import collectibles_bridge  # noqa: E402


class _Http:
    async def request(self, method, url, *, json=None, headers=None, timeout=30.0):
        return HttpResponse(200, _json={"symbol": "GRAMUSDT", "price": "2.05"})
    async def close(self): pass


class _Currency(CurrencyProvider):
    def __init__(self, repo, cur="YER", rate=540.0):
        self._repo = repo; self.cur = cur; self.rate = rate
    async def get_currency(self, user_id):
        self._repo.upsert_rates({self.cur: self.rate})  # كما يفعل BotCurrencyProvider
        return self.cur


def _app_with_catalog():
    from aiohttp import web
    d = tempfile.mkdtemp()
    repo = SQLiteCacheRepository(os.path.join(d, "c.db")); repo.init_schema()
    repo.upsert_items([CanonicalCollectible(
        provider="mrkt", provider_item_id="uuid-1", external_id="999",
        title="Liberty", collection="Liberty", model="Moonwalker",
        thumbnail_url="https://dom/media/k.webp",
        price_nano=2_000_000_000, is_on_sale=True,
        raw={"salePrice": 2_000_000_000, "giftId": 999},
    )])
    m = Metrics()
    gram = GramPrice(_Http(), ttl=60, metrics=m)
    cc = CurrencyCache(_Currency(repo), ttl=100, metrics=m)
    pricing = PricingService(repo, gram, cc, default_commission_usd="1", metrics=m)
    catalog = CatalogService(repo, pricing, provider_name="mrkt")
    api = PublicApi(catalog, TOKEN)
    app = web.Application()
    attach_public_routes(app, api)
    return app


class TestPublicApi(unittest.IsolatedAsyncioTestCase):
    async def test_list_requires_valid_initdata(self):
        async with TestClient(TestServer(_app_with_catalog())) as client:
            r = await client.get("/api/v1/collectibles")            # بلا مصادقة
            self.assertEqual(r.status, 401)
            r2 = await client.get("/api/v1/collectibles",
                                  headers={"Authorization": "tma " + make_init_data(TOKEN, 1770929898)})
            self.assertEqual(r2.status, 200)
            data = await r2.json()
            self.assertEqual(data["paging"]["total"], 1)
            item = data["items"][0]
            # السعر النهائي فقط: (2*2.05 + 1) * 540 = 2754.00
            self.assertEqual(item["price"]["currency"], "YER")
            self.assertEqual(item["price"]["amount"], "2754.00")
            # لا تسريب
            blob = json.dumps(data).lower()
            for forbidden in ("commission", "gram", "usd", "saleprice", "price_nano", "giftid", "raw"):
                self.assertNotIn(forbidden, blob)

    async def test_get_single_and_404(self):
        hdr = {"Authorization": "tma " + make_init_data(TOKEN, 5)}
        async with TestClient(TestServer(_app_with_catalog())) as client:
            r = await client.get("/api/v1/collectibles/uuid-1", headers=hdr)
            self.assertEqual(r.status, 200)
            self.assertEqual((await r.json())["id"], "uuid-1")
            r404 = await client.get("/api/v1/collectibles/nope", headers=hdr)
            self.assertEqual(r404.status, 404)


class TestBotBridge(unittest.TestCase):
    """تنفيذ محلي فعلي لجسر البوت (stdlib http.server) — منفصل عن bot.py."""
    def test_bridge_serves_currency_with_secret(self):
        def resolver(uid):
            return ("YER", 540.0) if uid == 7 else None
        srv = collectibles_bridge.start_currency_server(resolver, host="127.0.0.1", port=0, secret="s3cr3t")
        port = srv.server_address[1]
        try:
            # بلا سرّ → 401
            req = urllib.request.Request(f"http://127.0.0.1:{port}/internal/user/7/currency")
            try:
                urllib.request.urlopen(req); code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 401)
            # بسرّ صحيح → 200 + {currency, rate}
            req = urllib.request.Request(f"http://127.0.0.1:{port}/internal/user/7/currency",
                                         headers={"X-Internal-Secret": "s3cr3t"})
            body = json.loads(urllib.request.urlopen(req).read())
            self.assertEqual(body, {"currency": "YER", "rate": 540.0})
            # مستخدم غير موجود → 404
            req = urllib.request.Request(f"http://127.0.0.1:{port}/internal/user/99/currency",
                                         headers={"X-Internal-Secret": "s3cr3t"})
            try:
                urllib.request.urlopen(req); code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 404)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
