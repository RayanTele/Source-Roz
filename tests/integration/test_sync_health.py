"""تكامل: محرّك المزامنة + الصحة + خادم الويب (HTTP حقيقي على localhost)."""
import os
import tempfile
import unittest
from typing import Any, Dict, List

from aiohttp.test_utils import TestClient, TestServer

from app.core.health import HealthService
from app.core.media_service import MediaService
from app.core.metrics import Metrics
from app.core.models import CanonicalCollectible
from app.core.sync_engine import SyncEngine
from app.infra.cache_sqlite import SQLiteCacheRepository
from app.infra.gram_price import GramPrice
from app.infra.http_client import HttpResponse
from app.infra.media_fs import FilesystemMediaStore
from app.infra.web_server import build_app
from app.providers.base import BaseProvider


GIFT = {
    "id": "u-1", "giftIdString": "999", "modelName": "M", "collectionName": "C",
    "modelStickerThumbnailKey": "gifts/t/abc.webp", "salePrice": 3000000000, "isOnSale": True,
}


class StubProvider(BaseProvider):
    """مزوّد وهمي يعيد صفحتين ثم ينتهي."""
    def __init__(self, pages: List[List[Dict[str, Any]]]):
        super().__init__("mrkt", "v1")
        self._pages = pages; self.idx = 0
    async def authenticate(self): return "tok"
    async def fetch_listings(self, cursor=""):
        if self.idx >= len(self._pages): return [], ""
        page = self._pages[self.idx]; self.idx += 1
        nxt = "c" if self.idx < len(self._pages) else ""
        return page, nxt
    async def fetch_by_ids(self, ids): return []
    async def fetch_one(self, item_id): return {}
    def normalize(self, raw): 
        from app.providers.mrkt.v1 import mapper
        return mapper.map_gift(raw)
    def health(self):
        return {"provider": "mrkt", "version": "v1", "has_token": True, "breaker": {"state": "closed"}}


class _Http:
    async def request(self, method, url, *, json=None, headers=None, timeout=30.0):
        return HttpResponse(200, body=b"IMG")
    async def close(self): pass


class TestSyncHealth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        d = tempfile.mkdtemp()
        self.repo = SQLiteCacheRepository(os.path.join(d, "c.db")); self.repo.init_schema()
        self.metrics = Metrics()
        self.media = MediaService(FilesystemMediaStore(os.path.join(d, "m")), _Http(),
                                  cdn_base_url="https://cdn.x", media_base_url="https://dom",
                                  metrics=self.metrics)
        self.gram = GramPrice(_Http(), ttl=60, metrics=self.metrics)
        self.provider = StubProvider([[GIFT], []])  # صفحة فيها عنصر ثم صفحة فارغة
        self.sync = SyncEngine(self.provider, self.repo, media_service=self.media, metrics=self.metrics)
        self.health = HealthService(self.provider, self.repo, self.media, self.gram, self.metrics)

    async def test_sync_populates_cache_and_media_url(self):
        res = await self.sync.run_once()
        self.assertTrue(res.ok)
        self.assertEqual(res.new, 1)
        self.assertEqual(self.repo.count_items("mrkt"), 1)
        item = self.repo.get_item("mrkt", "u-1")
        self.assertEqual(item.thumbnail_url, "https://dom/media/gifts/t/abc.webp")
        # المقاييس سُجّلت
        snap = self.metrics.snapshot()
        self.assertGreaterEqual(snap["counters"].get("sync_runs", 0), 1)
        self.assertIn("sync_duration", snap["timers"])

    async def test_second_sync_prunes_unseen(self):
        await self.sync.run_once()          # يخزّن u-1
        # جولة ثانية بلا عناصر → u-1 يصبح غير مرئي ويُحذَف
        self.provider._pages = [[]]; self.provider.idx = 0
        res = await self.sync.run_once()
        self.assertTrue(res.ok)
        self.assertEqual(self.repo.count_items("mrkt"), 0)

    async def test_health_endpoint_no_commission(self):
        await self.sync.run_once()
        self.repo.set_meta("commission_usd", "9.99")  # موجودة داخلياً
        app = build_app(self.health, self.media)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/health")
            self.assertEqual(r.status, 200)
            body = await r.text()
            self.assertNotIn("commission", body.lower())
            self.assertNotIn("9.99", body)
            data = await __import__("json").loads(body) if False else (await r.json())
            self.assertIn(data["status"], ("ok", "degraded"))
            self.assertEqual(data["cache"]["cached_items"], 1)
            # /metrics
            rm = await client.get("/metrics")
            self.assertEqual(rm.status, 200)
            mbody = await rm.text()
            self.assertNotIn("commission", mbody.lower())
            # /media hit (الصورة خُزّنت أثناء المزامنة)
            rmed = await client.get("/media/gifts/t/abc.webp")
            self.assertEqual(rmed.status, 200)
            rmiss = await client.get("/media/does/not/exist.webp")
            self.assertEqual(rmiss.status, 404)


class TestBootstrapConstructs(unittest.TestCase):
    def test_build_service_constructs_without_io(self):
        # يبني الرسم الكامل دون أي نداء شبكي (إقلاع فقط)
        os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "c.db")
        os.environ["MEDIA_DIR"] = tempfile.mkdtemp()
        from app.bootstrap import build_service
        ctx = build_service()
        self.assertEqual(ctx.provider.name, "mrkt")
        self.assertTrue(hasattr(ctx.sync, "run_once"))
        self.assertTrue(hasattr(ctx.health, "snapshot"))


if __name__ == "__main__":
    unittest.main()
