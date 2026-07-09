"""
خادم HTTP خفيف للخدمة (aiohttp) — يعرض:
  GET /health          لقطة الصحة المجمّعة
  GET /metrics         المقاييس المجمّعة
  GET /media/{key}     تقديم صورة مكاشة (localhost حتى nginx في Phase G)

يُربَط داخلياً (127.0.0.1) افتراضياً. لا يعرض أي قيمة عمولة/سعر عميل.
هذا ليس واجهة الكتالوج العامة (تلك Phase F).
"""
from __future__ import annotations

import mimetypes
import os
from typing import Optional

from aiohttp import web

from app.core.health import HealthService
from app.core.logging_setup import get_logger
from app.core.media_service import MediaService
from app.infra.public_api import PublicApi, attach_public_routes

_log = get_logger("web")


def build_app(health: HealthService, media: MediaService, public_api: "PublicApi" = None) -> "web.Application":
    app = web.Application()

    async def health_handler(_req):
        return web.json_response(health.snapshot())

    async def metrics_handler(_req):
        return web.json_response(health.metrics_snapshot())

    async def media_handler(req):
        key = req.match_info.get("key", "")
        w = req.rel_url.query.get("w")
        path: Optional[str] = None
        if w and w.isdigit():
            path = media.resized_path(key, int(w))
        else:
            path = media.local_path(key)
        if not path or not os.path.isfile(path):
            return web.json_response({"error": "not found"}, status=404)
        ctype, _ = mimetypes.guess_type(key)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": ctype or "application/octet-stream",
                "Cache-Control": "public, max-age=604800, immutable",
            },
        )

    # ── داخلي (localhost فقط؛ لا يُعرَّض عبر nginx) ──
    app.router.add_get("/health", health_handler)
    app.router.add_get("/metrics", metrics_handler)
    # ── عام (يُعرَّض عبر nginx في Phase G): /media + /api ──
    app.router.add_get("/media/{key:.*}", media_handler)
    if public_api is not None:
        attach_public_routes(app, public_api)
    return app


class WebServer:
    """يدير دورة حياة خادم aiohttp (بدء/إيقاف رشيق)."""

    def __init__(self, app: "web.Application", host: str, port: int):
        self._app = app
        self._host = host
        self._port = port
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        _log.info("web server started on %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            _log.info("web server stopped")
