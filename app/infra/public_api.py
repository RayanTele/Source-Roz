"""
واجهة REST العامة للكتالوج (Phase F/G).

المسارات (يستهلكها الـ WebApp تماماً كموقع/تطبيق مستقبلي):
  GET /api/v1/collectibles?limit=&offset=&sort=&q=&collection=&model=&backdrop=&symbol=&locked=&price_min=&price_max=
  GET /api/v1/collectibles/{id}
  GET /api/v1/categories        (chips الواجهة)
  GET /api/v1/facets            (قيم الفلاتر: المجموعات/الموديلات/الخلفيات/الرموز)

المصادقة: initData الخاص بـ Telegram في ترويسة Authorization "tma <initData>"
(أو X-Init-Data). يُتحقَّق بتوكن البوت ويُستخرَج user_id للتسعير بعملته.
كل استجابة تحوي **السعر النهائي للعميل فقط** — لا عمولة/GRAM/خام/داخلي/مزوّد.
"""
from __future__ import annotations

from typing import Optional

from aiohttp import web

from app.core.catalog_service import CatalogService
from app.core.logging_setup import get_logger, request_context
from app.infra.resilience import ProviderError
from app.telegram.init_data import InitDataPrincipal, InvalidInitData, verify_init_data

_log = get_logger("api")

_SORTS = {"newest", "oldest", "cheapest", "expensive"}


def _extract_init_data(req) -> str:
    auth = req.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[4:].strip()
    x = req.headers.get("X-Init-Data")
    return x.strip() if x else ""


class PublicApi:
    def __init__(self, catalog: CatalogService, bot_token: str, *, init_data_max_age: Optional[int] = 86400):
        self._catalog = catalog
        self._bot_token = bot_token
        self._max_age = init_data_max_age

    def _auth(self, req) -> InitDataPrincipal:
        return verify_init_data(_extract_init_data(req), self._bot_token, max_age_seconds=self._max_age)

    async def list_handler(self, req):
        with request_context(provider="api"):
            try:
                principal = self._auth(req)
            except InvalidInitData as exc:
                return web.json_response({"error": "unauthorized", "detail": str(exc)}, status=401)
            q = req.rel_url.query
            filters = {k: q[k] for k in ("collection", "model", "backdrop", "symbol", "q") if q.get(k)}
            if q.get("locked") in ("1", "true", "0", "false"):
                filters["locked"] = q["locked"] in ("1", "true")
            for k in ("price_min", "price_max"):
                if q.get(k):
                    filters[k] = q[k]
            sort = q.get("sort") if q.get("sort") in _SORTS else "newest"
            try:
                limit = int(q.get("limit", "40"))
                offset = int(q.get("offset", "0"))
            except ValueError:
                return web.json_response({"error": "bad_request"}, status=400)
            try:
                result = await self._catalog.list_items(
                    principal.user_id, limit=limit, offset=offset, sort=sort,
                    filters=filters or None,
                )
            except ProviderError:
                return web.json_response({"error": "pricing_unavailable"}, status=503)
            return web.json_response(result)

    async def get_handler(self, req):
        with request_context(provider="api"):
            try:
                principal = self._auth(req)
            except InvalidInitData as exc:
                return web.json_response({"error": "unauthorized", "detail": str(exc)}, status=401)
            item_id = req.match_info.get("id", "")
            try:
                item = await self._catalog.get_item(principal.user_id, item_id)
            except ProviderError:
                return web.json_response({"error": "pricing_unavailable"}, status=503)
            if item is None:
                return web.json_response({"error": "not_found"}, status=404)
            return web.json_response(item)

    async def categories_handler(self, req):
        try:
            self._auth(req)
        except InvalidInitData as exc:
            return web.json_response({"error": "unauthorized", "detail": str(exc)}, status=401)
        return web.json_response({"categories": self._catalog.categories()})

    async def facets_handler(self, req):
        try:
            self._auth(req)
        except InvalidInitData as exc:
            return web.json_response({"error": "unauthorized", "detail": str(exc)}, status=401)
        return web.json_response(self._catalog.facets())


def attach_public_routes(app: "web.Application", api: PublicApi) -> None:
    """يُرفِق مسارات الكتالوج العامة على تطبيق aiohttp قائم."""
    app.router.add_get("/api/v1/collectibles", api.list_handler)
    app.router.add_get("/api/v1/collectibles/{id}", api.get_handler)
    app.router.add_get("/api/v1/categories", api.categories_handler)
    app.router.add_get("/api/v1/facets", api.facets_handler)
