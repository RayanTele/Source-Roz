"""
عميل HTTP بديل مبني على curl_cffi (impersonate) — نفس منفذ AsyncHttpClient.

لماذا: المرجع الرسمي العامل يستخدم curl_cffi، وهو يزوّر بصمة TLS/JA3 وترتيب
ترويسات متصفح حقيقي. الخادم خلف Cloudflare، وقد يرفض بصمة aiohttp (بصمة بايثون
واضحة) حتى مع توكن صالح.

هذا الملف لا يغيّر أي واجهة: ينفّذ نفس عقد AsyncHttpClient، ويُختار عبر
HTTP_CLIENT=curl_cffi في .env. عند غياب الحزمة يفشل بوضوح عند الإنشاء فقط.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.infra.http_client import HttpResponse


class CurlCffiClient:
    """تنفيذ AsyncHttpClient فوق curl_cffi مع انتحال بصمة متصفح."""

    def __init__(self, timeout: float = 30.0, impersonate: str = "chrome"):
        try:
            from curl_cffi.requests import AsyncSession  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "curl_cffi غير مثبّت — نفّذ: pip install curl_cffi --break-system-packages"
            ) from exc
        from curl_cffi.requests import AsyncSession

        self._AsyncSession = AsyncSession
        self._default_timeout = timeout
        self._impersonate = impersonate
        self._session = None

    async def _ensure_session(self):
        if self._session is None:
            # جلسة واحدة مُعاد استخدامها → cookie_jar تلقائي كما في المرجع
            self._session = self._AsyncSession(impersonate=self._impersonate)
        return self._session

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        session = await self._ensure_session()
        resp = await session.request(
            method.upper(), url, json=json, headers=headers,
            timeout=timeout or self._default_timeout,
        )
        return HttpResponse(
            status=resp.status_code,
            body=resp.content or b"",
            headers={k: v for k, v in resp.headers.items()},
        )

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    def cookie_snapshot(self, url: str = "") -> dict:
        """تشخيص الكوكيز (قيم مُخفاة)."""
        def _mask(v: str) -> str:
            v = str(v)
            return f"{v[:6]}…(len={len(v)})" if v else "<فارغ>"

        if self._session is None:
            return {"jar": [], "would_send": {}, "note": "لا جلسة بعد"}
        jar = []
        try:
            for c in self._session.cookies.jar:
                jar.append({"name": c.name, "value": _mask(c.value),
                            "domain": c.domain, "path": c.path})
        except Exception:
            pass
        return {"jar": jar, "would_send": {}}
