"""تكامل: مزوّد MRKT فوق HTTP حقيقي على localhost ضد خادم mock يحترم العقد.
ليس تنفيذاً حيّاً ضد MRKT — محاكاة للعقد المؤكَّد."""
import unittest
import uuid

from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.metrics import Metrics
from app.infra.http_client import AiohttpClient
from app.infra.resilience import CircuitBreaker, CircuitOpenError, ProviderError
from app.providers.mrkt.v1.adapter import MrktProvider
from app.providers.mrkt.v1.client import MrktClient
from app.providers.mrkt.v1.token_manager import InitDataProvider, TokenManager

GIFT = {
    "id": "633c7d68", "giftIdString": "5931453499106658162", "giftId": 5931453499106658162,
    "title": "Liberty Figure", "collectionName": "Liberty Figure", "modelName": "Moonwalker",
    "backdropName": "Amber", "symbolName": "Gift Box", "number": 109482,
    "modelStickerThumbnailKey": "gifts/stickers/thumbnails/abc.webp",
    "salePrice": 14950000000, "isOnSale": True, "isLocked": True,
    "unlockDate": "2026-07-23T09:12:15Z",
}


class FakeIDP(InitDataProvider):
    async def get_init_data(self): return "query_id=X&user=%7B%22id%22%3A1%7D&hash=deadbeef"


def build_app(state):
    app = web.Application()

    async def auth(req):
        body = await req.json()
        state["appId_seen"] = body.get("appId", "__missing__")
        tok = str(uuid.uuid4()); state["valid"] = tok; state["auth_calls"] += 1
        return web.json_response({"token": tok})

    async def saling(req):
        if state.get("rotate_once") and not state.get("rotated"):
            state["rotated"] = True; state["valid"] = "ROT-" + str(state["valid"])
        if req.headers.get("Authorization") != state["valid"]:
            return web.json_response({}, status=401)
        if state["fail_n"] > 0:
            state["fail_n"] -= 1; return web.json_response({}, status=503)
        cur = (await req.json()).get("cursor", "")
        if cur == "":
            return web.json_response({"gifts": [GIFT], "cursor": "CUR2", "total": 0})
        return web.json_response({"gifts": [], "cursor": None, "total": 0})

    async def byids(req):
        if req.headers.get("Authorization") != state["valid"]:
            return web.json_response({}, status=401)
        return web.json_response([GIFT for _ in (await req.json()).get("ids", [])])

    async def gift(req):
        if req.headers.get("Authorization") != state["valid"]:
            return web.json_response({}, status=401)
        return web.json_response(GIFT)

    async def always500(req):
        if req.headers.get("Authorization") != state["valid"]:
            return web.json_response({}, status=401)
        return web.json_response({}, status=500)

    app.router.add_post("/api/v1/auth", auth)
    app.router.add_post("/api/v1/gifts/saling", saling)
    app.router.add_post("/api/v1/gifts/saling/by-ids", byids)
    app.router.add_get("/api/v1/gifts/gift/{uuid}", gift)
    app.router.add_post("/api/v1/always500", always500)
    return app


def new_state():
    return {"valid": None, "auth_calls": 0, "fail_n": 0, "appId_seen": "__unset__"}


class TestMrktProviderIntegration(unittest.IsolatedAsyncioTestCase):
    async def _make(self, state, **client_kw):
        srv = TestServer(build_app(state)); await srv.start_server()
        base = str(srv.make_url("")).rstrip("/")
        http = AiohttpClient()
        m = Metrics()
        tm = TokenManager(http, base, FakeIDP(), app_id=None, metrics=m)
        cl = MrktClient(http, base, tm, breaker=client_kw.pop("breaker", CircuitBreaker()),
                        base_delay=0.01, max_delay=0.05, metrics=m, **client_kw)
        return srv, http, MrktProvider(cl, tm, metrics=m), cl, m

    async def test_happy_pagination_normalize_appid(self):
        st = new_state(); srv, http, prov, cl, m = await self._make(st)
        try:
            self.assertTrue(await prov.authenticate())
            self.assertIsNone(st["appId_seen"])  # appId=null مُرسَل
            g1, c1 = await prov.fetch_listings("")
            self.assertEqual(len(g1), 1); self.assertEqual(c1, "CUR2")
            g2, c2 = await prov.fetch_listings(c1)
            self.assertEqual(g2, []); self.assertFalse(c2)
            canon = prov.normalize(g1[0])
            self.assertEqual(canon.external_id, "5931453499106658162")
            self.assertEqual(canon.price_nano, 14950000000)
            ids = await prov.fetch_by_ids(["a", "b"]); self.assertEqual(len(ids), 2)
            one = await prov.fetch_one("633c7d68"); self.assertEqual(one["id"], "633c7d68")
            self.assertGreaterEqual(m.snapshot()["counters"].get("api_requests", 0), 4)
        finally:
            await http.close(); await srv.close()

    async def test_401_refresh(self):
        st = new_state(); st["rotate_once"] = True
        srv, http, prov, cl, m = await self._make(st, retries=3)
        try:
            g, _ = await cl.fetch_listings("")
            self.assertEqual(len(g), 1)
            self.assertEqual(st["auth_calls"], 2)  # جرى تجديد
            self.assertGreaterEqual(m.snapshot()["counters"].get("token_refreshes", 0), 2)
        finally:
            await http.close(); await srv.close()

    async def test_transient_retry(self):
        st = new_state(); st["fail_n"] = 2
        srv, http, prov, cl, m = await self._make(st, retries=3)
        try:
            g, _ = await cl.fetch_listings("")
            self.assertEqual(len(g), 1)
            self.assertGreaterEqual(m.snapshot()["counters"].get("retries", 0), 2)
        finally:
            await http.close(); await srv.close()

    async def test_circuit_breaker_opens(self):
        st = new_state()
        br = CircuitBreaker(fail_threshold=2, reset_timeout=999)
        srv, http, prov, cl, m = await self._make(st, retries=0, breaker=br)
        try:
            await cl._tokens.get_token()
            for _ in range(2):
                with self.assertRaises(ProviderError):
                    await cl._authed_request("POST", "/api/v1/always500", json={})
            with self.assertRaises(CircuitOpenError):
                await cl._authed_request("POST", "/api/v1/always500", json={})
            self.assertEqual(br.snapshot()["state"], "open")
        finally:
            await http.close(); await srv.close()


if __name__ == "__main__":
    unittest.main()
