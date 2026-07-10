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
    AsyncRateLimiter,
    AuthError,
    CircuitBreaker,
    ProviderError,
    RateLimitError,
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
        limiter: Optional[AsyncRateLimiter] = None,
        referer: str = "https://cdn.tgmrkt.io/",
        origin: str = "",
        user_agent: str = "",
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
        # محدِّد معدّل داخلي (يمنع تجاوز rps المسموح، خاصةً في المزامنة الأولى)
        self._limiter = limiter or AsyncRateLimiter(rate=1.0, burst=1)
        self._referer = referer
        self._origin = origin
        self._user_agent = user_agent

    def _headers(self, token: str) -> Dict[str, str]:
        """
        ترويسات مطابقة **حرفياً** للمرجع الرسمي العامل:
            {'Authorization': token, 'Referer': 'https://cdn.tgmrkt.io/'}
        التوكن خام بلا بادئة Bearer. لا Origin ولا User-Agent ولا Cookie —
        المرجع لا يرسلها؛ تُضاف فقط إن ضُبطت صراحةً في البيئة.
        """
        h = {"Authorization": token, "Referer": self._referer}
        if self._origin:
            h["Origin"] = self._origin
        if self._user_agent:
            h["User-Agent"] = self._user_agent
        return h

    def _log_outgoing(self, method: str, path: str, body: Optional[Any], token: str) -> None:
        """
        يسجّل الطلب الصادر **حرفياً** كما سيُرسَل: URL، الطريقة، بايتات JSON
        المسلسلة، وكل الترويسات (مع إخفاء التوكن) — للمقارنة بالمرجع.
        """
        import hashlib
        import json as _json

        from app.providers.mrkt.v1.token_manager import mask_secret

        headers = self._headers(token)
        safe_headers = {
            k: (mask_secret(v) if k.lower() == "authorization" else v)
            for k, v in headers.items()
        }
        if body is None:
            raw = b""
        else:
            raw = _json.dumps(body).encode("utf-8")
        _log.info(
            "OUTGOING >>> %s %s%s\n"
            "  headers = %s\n"
            "  body_bytes(len=%s, sha256=%s) = %s",
            method, self._base, path,
            safe_headers,
            len(raw), hashlib.sha256(raw).hexdigest()[:16],
            raw.decode("utf-8") if raw else "<لا جسم>",
        )
        _log.info(
            "  transport = %s | Authorization scheme = %s",
            type(self._http).__name__,
            "Bearer" if token.lower().startswith("bearer ") else "raw (بلا بادئة)",
        )

    def _log_auth_header(self, token: str, path: str) -> None:
        """يسجّل قيمة Authorization المُرسَلة فعلياً (مع إخفاء معظم التوكن)."""
        from app.providers.mrkt.v1.token_manager import mask_secret

        scheme = "Bearer" if token.lower().startswith("bearer ") else "<خام بلا بادئة>"
        headers = self._headers(token)
        _log.info(
            "REQUEST %s | Authorization: %s | الصيغة=%s | ترويسات مرسلة=%s",
            path, mask_secret(token), scheme, sorted(headers.keys()),
        )
        # تشخيص الكوكيز: لا نبني ترويسة Cookie يدوياً (كالمرجع) — نُظهر ما ستفعله الجلسة
        _log.info(
            "COOKIE HEADER المُرسَلة يدوياً: %s",
            headers.get("Cookie", "<لا شيء — لا نبنيها يدوياً، مطابق للمرجع>"),
        )
        snap = None
        if hasattr(self._http, "cookie_snapshot"):
            try:
                snap = self._http.cookie_snapshot(f"{self._base}{path}")
            except Exception:
                snap = None
        if snap is not None:
            _log.info(
                "COOKIE JAR: محتويات=%s | ستُرسَل تلقائياً لهذا الطلب=%s",
                snap.get("jar") or "<فارغ>",
                snap.get("would_send") or "<لا شيء>",
            )

    @staticmethod
    def _parse_retry_after(resp: HttpResponse) -> Optional[float]:
        """يقرأ Retry-After (ثوانٍ) من الاستجابة إن وُجدت."""
        hdrs = resp.headers or {}
        for k, v in hdrs.items():
            if k.lower() == "retry-after":
                try:
                    return float(str(v).strip())
                except (TypeError, ValueError):
                    return None
        return None

    # ── طلب مصادَق مع تجديد 401 لمرة واحدة ──
    async def _authed_request(
        self, method: str, path: str, json: Optional[Any] = None
    ) -> HttpResponse:
        with request_context(provider="mrkt") as ctx:
            async def _do() -> HttpResponse:
                token = await self._tokens.get_token()
                self._log_outgoing(method, path, json, token)
                self._log_auth_header(token, path)
                await self._limiter.acquire()          # لا نتجاوز الحدّ المسموح
                self._m.inc("api_requests")
                with self._m.timer("provider_latency"):
                    resp = await self._http.request(
                        method,
                        f"{self._base}{path}",
                        json=json,
                        headers=self._headers(token),
                        timeout=self._timeout,
                    )
                if resp.status == 401:
                    self._m.inc("auth_401")
                    _log.warning("401 received; refreshing token and retrying %s", path)
                    self._tokens.invalidate()
                    token = await self._tokens.refresh()
                    await self._limiter.acquire()      # الطلب المُعاد يُحتسَب أيضاً
                    self._m.inc("api_requests")
                    with self._m.timer("provider_latency"):
                        resp = await self._http.request(
                            method,
                            f"{self._base}{path}",
                            json=json,
                            headers=self._headers(token),
                            timeout=self._timeout,
                        )
                self._raise_for_status(resp, limiter=self._limiter)
                return resp

            async def _guarded() -> HttpResponse:
                return await self._breaker.call(_do)

            def _on_retry(attempt: int) -> None:
                self._m.inc("retries")
                _log.warning(
                    "إعادة محاولة #%s لـ %s %s (بعد خطأ عابر — انظر سجل الاستجابة أعلاه)",
                    attempt, method, path,
                )

            try:
                resp = await retry_async(
                    _guarded,
                    retries=self._retries,
                    base_delay=self._base_delay,
                    max_delay=self._max_delay,
                    retry_on=(TransientError,),
                    on_retry=_on_retry,
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

    # ── تشخيص: ترويسات مفيدة عند الأخطاء (لا أسرار — كلها ترويسات استجابة) ──
    _DIAG_HEADERS = (
        "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
        "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset",
        "X-Rate-Limit-Limit", "X-Rate-Limit-Remaining", "X-Rate-Limit-Reset",
        "Content-Type", "Server", "Date", "CF-Ray", "X-Request-Id", "Via",
    )
    _DIAG_BODY_LIMIT = 800

    @staticmethod
    def _diag_body(resp: HttpResponse) -> str:
        """نص جسم الاستجابة مقتطعاً وآمناً للطباعة."""
        try:
            raw = resp.body or b""
            if not raw:
                return "<empty>"
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return "<undecodable>"
        text = text.strip().replace("\n", " ")
        if len(text) > MrktClient._DIAG_BODY_LIMIT:
            return text[: MrktClient._DIAG_BODY_LIMIT] + f"… (+{len(text) - MrktClient._DIAG_BODY_LIMIT} حرفاً)"
        return text

    @staticmethod
    def _diag_headers(resp: HttpResponse) -> Dict[str, str]:
        """الترويسات التشخيصية الموجودة فقط (مع مطابقة غير حسّاسة لحالة الأحرف)."""
        hdrs = resp.headers or {}
        lower = {k.lower(): v for k, v in hdrs.items()}
        found = {}
        for name in MrktClient._DIAG_HEADERS:
            val = lower.get(name.lower())
            if val is not None:
                found[name] = val
        return found

    @staticmethod
    def _log_error_response(resp: HttpResponse) -> None:
        """يسجّل الاستجابة الكاملة (حالة + ترويسات مهمة + جسم مقتطع) قبل رفع الاستثناء."""
        hdrs = MrktClient._diag_headers(resp)
        retry_after = hdrs.get("Retry-After")
        _log.error(
            "استجابة خطأ من المزوّد: status=%s retry_after=%s headers=%s body=%s",
            resp.status,
            retry_after if retry_after is not None else "-",
            hdrs if hdrs else "{}",
            MrktClient._diag_body(resp),
        )
        if resp.status == 429:
            # كل ترويسات الاستجابة عند 429 (لالتقاط أسماء غير قياسية للحدّ)
            all_hdrs = resp.headers or {}
            _log.error("429 — كامل ترويسات الاستجابة: %s", dict(all_hdrs) if all_hdrs else "{}")

    @staticmethod
    def _raise_for_status(resp: HttpResponse, limiter: Optional[AsyncRateLimiter] = None) -> None:
        if resp.status == 401:
            # 401 متوقّع في مسار التجديد؛ يُسجَّل عند الفشل النهائي فقط
            raise AuthError("401 بعد تجديد التوكن")
        if resp.status == 429:
            MrktClient._log_error_response(resp)
            retry_after = MrktClient._parse_retry_after(resp)
            # أوقف الإصدار مؤقتاً للمدّة المطلوبة (أو مهلة تحفّظية إن غابت)
            if limiter is not None:
                limiter.pause(retry_after if retry_after else 5.0)
            raise RateLimitError(
                f"حدّ معدّل من المزوّد (429){f' — Retry-After={retry_after}s' if retry_after else ''}",
                retry_after=retry_after,
            )
        if resp.status >= 500:
            MrktClient._log_error_response(resp)
            raise TransientError(f"حالة عابرة {resp.status}")
        if resp.status >= 400:
            MrktClient._log_error_response(resp)
            raise ProviderError(f"حالة غير قابلة لإعادة المحاولة {resp.status}")

    # ── نقاط القراءة ──
    #: الحدّ الأقصى لـ count لدى MRKT هو 20 (موثّق في المرجع الرسمي)
    MAX_SALING_COUNT = 20

    async def fetch_listings(self, cursor: str = "") -> tuple[List[Dict[str, Any]], str]:
        # جسم الطلب مطابق **حرفياً** للمرجع الرسمي العامل (مفاتيح camelCase + كل الحقول)
        body = {
            "collectionNames": [],
            "modelNames": [],
            "backdropNames": [],
            "symbolNames": [],
            "ordering": "Price",
            "lowToHigh": True,
            "maxPrice": None,
            "minPrice": None,
            "mintable": None,
            "number": None,
            "count": min(self._saling_count, self.MAX_SALING_COUNT),
            "cursor": cursor or "",
            "query": None,
            "promotedFirst": False,
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

    def limiter_snapshot(self) -> dict:
        return self._limiter.snapshot()

    async def aclose(self) -> None:
        """إغلاق رشيق لعميل HTTP الأساسي."""
        await self._http.close()
