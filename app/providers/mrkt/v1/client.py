"""
عميل MRKT REST (v1) — طبقة النداءات الخام مع الصمود + المقاييس + السجلّات.

النقاط المستخدمة (مؤكَّدة من العقد):
  POST /api/v1/auth                    (عبر TokenManager)
  POST /api/v1/gifts/saling            body={count,cursor,ModelNames,SymbolNames,BackdropNames,CollectionNames}
  POST /api/v1/gifts/saling/by-ids     body={ids:[uuid,...]}
  GET  /api/v1/gifts/gift/{uuid}

المصادقة: التوكن الخام في ترويسة Authorization (بلا Bearer).
عند 401: نُبطِل التوكن، نجدّده مرة واحدة، ونعيد الطلب.
الأخطاء العابرة (شبكة/5xx/429) تمرّ عبر retry أُسّي، والكل داخل قاطع دائرة.
كل طلب يحمل request_id ويُسجَّل زمنه ومقاييسه.

للقراءة فقط — لا شراء ولا تنفيذ طلبات.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.core.logging_setup import get_logger, request_context
from app.core.metrics import CB_STATE_CODE, Metrics, resolve
from app.infra.http_client import AsyncHttpClient, HttpResponse
from app.infra.resilience import (
    AuthError,
    CircuitBreaker,
    ProviderError,
    TransientError,
    retry_async,
)
from app.providers.mrkt.v1.token_manager import TokenManager

_SALING_PATH = "/api/v1/gifts/saling"
_BYIDS_PATH = "/api/v1/gifts/saling/by-ids"
_GIFT_PATH = "/api/v1/gifts/gift/{uuid}"

_log = get_logger("mrkt.client")


class MrktClient:
    """عميل قراءة لسوق MRKT مع تجديد توكن وصمود ومقاييس."""

    def __init__(
        self,
        http: AsyncHttpClient,
        base_url: str,
        token_manager: TokenManager,
        *,
        breaker: Optional[CircuitBreaker] = None,
        retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        timeout: float = 30.0,
        saling_count: int = 20,
        metrics: Optional[Metrics] = None,
    ):
        self._http = http
        self._base = base_url.rstrip("/")
        self._tokens = token_manager
        self._breaker = breaker or CircuitBreaker()
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._timeout = timeout
        self._saling_count = saling_count
        self._m = resolve(metrics)

    # ── طلب مصادَق مع تجديد 401 لمرة واحدة ──
    async def _authed_request(
        self, method: str, path: str, json: Optional[Any] = None
    ) -> HttpResponse:
        with request_context(provider="mrkt") as ctx:
            async def _do() -> HttpResponse:
                token = await self._tokens.get_token()
                self._m.inc("api_requests")
                with self._m.timer("provider_latency"):
                    resp = await self._http.request(
                        method,
                        f"{self._base}{path}",
                        json=json,
                        headers={"Authorization": token},
                        timeout=self._timeout,
                    )
                if resp.status == 401:
                    self._m.inc("auth_401")
                    _log.warning("401 received; refreshing token and retrying %s", path)
                    self._tokens.invalidate()
                    token = await self._tokens.refresh()
                    self._m.inc("api_requests")
                    with self._m.timer("provider_latency"):
                        resp = await self._http.request(
                            method,
                            f"{self._base}{path}",
                            json=json,
                            headers={"Authorization": token},
                            timeout=self._timeout,
                        )
                self._raise_for_status(resp)
                return resp

            async def _guarded() -> HttpResponse:
                return await self._breaker.call(_do)

            try:
                resp = await retry_async(
                    _guarded,
                    retries=self._retries,
                    base_delay=self._base_delay,
                    max_delay=self._max_delay,
                    retry_on=(TransientError,),
                    on_retry=lambda a: self._m.inc("retries"),
                )
                _log.info("%s %s -> %s", method, path, resp.status)
                return resp
            except Exception as exc:
                self._m.inc("api_failures")
                _log.error("%s %s failed: %s", method, path, type(exc).__name__)
                raise
            finally:
                self._m.gauge(
                    "circuit_breaker_state",
                    CB_STATE_CODE.get(self._breaker.state, -1),
                )
                _ = ctx

    @staticmethod
    def _raise_for_status(resp: HttpResponse) -> None:
        if resp.status == 401:
            raise AuthError("401 بعد تجديد التوكن")
        if resp.status == 429 or resp.status >= 500:
            raise TransientError(f"حالة عابرة {resp.status}")
        if resp.status >= 400:
            raise ProviderError(f"حالة غير قابلة لإعادة المحاولة {resp.status}")

    # ── نقاط القراءة ──
    async def fetch_listings(self, cursor: str = "") -> tuple[List[Dict[str, Any]], str]:
        body = {
            "count": self._saling_count,
            "cursor": cursor or "",
            "ModelNames": [],
            "SymbolNames": [],
            "BackdropNames": [],
            "CollectionNames": [],
        }
        resp = await self._authed_request("POST", _SALING_PATH, json=body)
        data = resp.json() or {}
        gifts = data.get("gifts") or []
        next_cursor = data.get("cursor") or ""
        # ملاحظة: الحقل total غير موثوق (يرجع 0 دوماً) — لا نعتمد عليه.
        return gifts, next_cursor

    async def fetch_by_ids(self, ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        resp = await self._authed_request("POST", _BYIDS_PATH, json={"ids": list(ids)})
        data = resp.json()
        return list(data) if isinstance(data, list) else (data.get("gifts") or [])

    async def fetch_one(self, item_id: str) -> Dict[str, Any]:
        path = _GIFT_PATH.format(uuid=item_id)
        resp = await self._authed_request("GET", path)
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def breaker_snapshot(self) -> dict:
        return self._breaker.snapshot()

    async def aclose(self) -> None:
        """إغلاق رشيق لعميل HTTP الأساسي."""
        await self._http.close()
