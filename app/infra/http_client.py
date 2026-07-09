"""
تجريد عميل HTTP غير المتزامن + تنفيذ مبني على aiohttp.

الغرض من التجريد: اختبار المزوّد محلياً بعميل وهمي دون شبكة،
مع تنفيذ إنتاجي حقيقي على aiohttp. يُستورَد aiohttp بكسل (lazy) كي
تُستورَد الحزمة حتى لو لم تُثبَّت الحزمة بعد (فحص الاستيراد لا يفشل).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass
class HttpResponse:
    """استجابة HTTP مبسّطة موحّدة."""

    status: int
    body: bytes = b""
    headers: Optional[Dict[str, str]] = None
    _json: Any = None

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        import json as _j

        return _j.loads(self.body.decode("utf-8")) if self.body else None


class AsyncHttpClient(Protocol):
    """واجهة عميل HTTP غير متزامن."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        ...

    async def close(self) -> None:
        ...


class AiohttpClient:
    """تنفيذ إنتاجي عبر aiohttp (يُستورَد داخل __init__ فقط)."""

    def __init__(self, timeout: float = 30.0):
        try:
            import aiohttp  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "aiohttp غير مثبّت — أضِفه إلى requirements وثبّته قبل التشغيل الإنتاجي"
            ) from exc
        self._aiohttp = __import__("aiohttp")
        self._default_timeout = timeout
        self._session = None

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = self._aiohttp.ClientSession()
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
        to = self._aiohttp.ClientTimeout(total=timeout or self._default_timeout)
        async with session.request(method, url, json=json, headers=headers, timeout=to) as resp:
            body = await resp.read()
            return HttpResponse(
                status=resp.status,
                body=body,
                headers={k: v for k, v in resp.headers.items()},
            )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
