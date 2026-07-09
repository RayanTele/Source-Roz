"""
مدير التوكن (Token Manager) لـ MRKT.

دورة حياة التوكن:
  1) عند الحاجة لتوكن ولا يوجد صالح: نطلب initData من InitDataProvider.
  2) POST /api/v1/auth  body={"data": initData, "appId": <app_id|null>}
     → {"token": "<UUID>"}.
  3) يُخزَّن التوكن ويُرسَل خاماً (بلا Bearer) في ترويسة Authorization لبقية REST.
  4) عند 401 من أي طلب: نُبطِل التوكن ونجدّده مرة واحدة ثم نعيد الطلب.

عزل Telethon: توليد initData خلف واجهة InitDataProvider، فالتوكن مانجر
قابل للاختبار بمزوّد وهمي دون Telegram حيّ. Telethon يُستورَد بكسل داخل
التنفيذ الحقيقي فقط (مسار بارد نادر).
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional

from app.core.logging_setup import get_logger
from app.core.metrics import Metrics, resolve
from app.infra.http_client import AsyncHttpClient
from app.infra.resilience import AuthError, ProviderError

_log = get_logger("mrkt.token")


def mask_secret(value: str, keep: int = 6) -> str:
    """يُخفي معظم السرّ للتسجيل الآمن: abc123…(len=36)."""
    if not value:
        return "<فارغ>"
    v = str(value)
    return f"{v[:keep]}…(len={len(v)})"


class InitDataProvider(ABC):
    """واجهة توليد initData (تُنفَّذ بـ Telethon إنتاجياً، أو وهمياً للاختبار)."""

    @abstractmethod
    async def get_init_data(self) -> str:
        raise NotImplementedError


class TokenManager:
    """يدير الحصول على توكن MRKT وتجديده."""

    def __init__(
        self,
        http: AsyncHttpClient,
        base_url: str,
        init_data_provider: InitDataProvider,
        app_id: Optional[str] = None,
        auth_path: str = "/api/v1/auth",
        timeout: float = 30.0,
        metrics: Optional[Metrics] = None,
        limiter=None,
        referer: str = "https://cdn.tgmrkt.io/",
        origin: str = "",
        user_agent: str = "",
    ):
        self._http = http
        self._base = base_url.rstrip("/")
        self._idp = init_data_provider
        self._app_id = app_id
        self._auth_path = auth_path
        self._timeout = timeout
        self._m = resolve(metrics)
        self._limiter = limiter
        # ترويسات مطابقة للعميل الرسمي عند /auth أيضاً
        # المرجع يرسل Referer فقط (+ Content-Type من مكتبة HTTP)
        self._auth_headers = {"Referer": referer, "Content-Type": "application/json"}
        if origin:
            self._auth_headers["Origin"] = origin
        if user_agent:
            self._auth_headers["User-Agent"] = user_agent
        self._token: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def current(self) -> Optional[str]:
        return self._token

    def invalidate(self) -> None:
        """يُبطِل التوكن الحالي (يُستدعى عند 401)."""
        self._token = None

    async def get_token(self) -> str:
        """يعيد توكناً صالحاً، مجدِّداً إياه إن لزم (آمن ضد التزامن)."""
        if self._token is not None:
            return self._token
        async with self._lock:
            if self._token is not None:  # فحص مزدوج بعد القفل
                return self._token
            self._token = await self._authenticate()
            return self._token

    async def refresh(self) -> str:
        """يجبر تجديد التوكن (يُستخدم بعد 401)."""
        async with self._lock:
            self._token = await self._authenticate()
            return self._token

    def _log_auth_response(self, resp) -> None:
        """يسجّل استجابة /auth كاملة مع إخفاء القيم السرّية."""
        import json as _json
        body_preview = "<فارغ>"
        try:
            raw = (resp.body or b"").decode("utf-8", errors="replace")
            if raw:
                try:
                    obj = _json.loads(raw)
                    if isinstance(obj, dict):
                        safe = {
                            k: (mask_secret(v) if k.lower() in
                                ("token", "access_token", "accesstoken", "jwt", "authtoken", "refreshtoken")
                                else v)
                            for k, v in obj.items()
                        }
                        body_preview = _json.dumps(safe, ensure_ascii=False)[:600]
                    else:
                        body_preview = raw[:400]
                except ValueError:
                    body_preview = raw[:400]
        except Exception:
            body_preview = "<غير قابل للقراءة>"
        hdrs = resp.headers or {}
        interesting = {k: v for k, v in hdrs.items()
                       if k.lower() in ("content-type", "set-cookie", "server", "cf-ray", "date")}
        if "set-cookie" in {k.lower() for k in hdrs}:
            _log.warning("تنبيه: /auth أرسل Set-Cookie — المرجع لا يستخدم كوكيز")
        _log.info(
            "AUTH RESPONSE: status=%s | ترويسات=%s | الجسم=%s",
            resp.status, interesting if interesting else "{}", body_preview,
        )

    async def _authenticate(self) -> str:
        init_data = await self._idp.get_init_data()
        if not init_data:
            raise AuthError("initData فارغ — تعذّر توليد المصادقة")
        # المرجع الرسمي يرسل {"data": init_data} فقط — بلا appId إطلاقاً.
        # نُدرج appId فقط إذا ضُبط صراحةً (MRKT_AUTH_APP_ID).
        payload = {"data": init_data}
        if self._app_id:
            payload["appId"] = self._app_id

        _log.info(
            "AUTH REQUEST: %s%s | مفاتيح الجسم=%s | طول initData=%s | ترويسات=%s",
            self._base, self._auth_path, sorted(payload.keys()),
            len(init_data), sorted(self._auth_headers.keys()),
        )
        if self._limiter is not None:
            await self._limiter.acquire()
        resp = await self._http.request(
            "POST", f"{self._base}{self._auth_path}", json=payload,
            headers=self._auth_headers, timeout=self._timeout,
        )
        self._log_auth_response(resp)

        if resp.status == 401:
            raise AuthError("رُفضت المصادقة (401) عند /auth")
        if resp.status >= 400:
            raise ProviderError(f"فشل /auth بحالة {resp.status}")
        data = resp.json() or {}

        # اسم حقل التوكن: المرجع يستخدم "token". نبحث عن البدائل للتشخيص فقط.
        token = None
        field_used = None
        for candidate in ("token", "access_token", "accessToken", "jwt", "authToken"):
            if isinstance(data, dict) and data.get(candidate):
                token, field_used = data[candidate], candidate
                break
        token_type = data.get("token_type") or data.get("tokenType") if isinstance(data, dict) else None
        _log.info(
            "AUTH PARSED: حقل التوكن=%s | token_type=%s | طول التوكن=%s | كل حقول الاستجابة=%s",
            field_used, token_type if token_type else "<غير موجود>",
            len(token) if token else 0,
            sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )
        if not token:
            raise ProviderError("استجابة /auth بلا حقل token")
        if field_used != "token":
            _log.warning("تنبيه: التوكن جاء في حقل %r لا 'token' — يخالف المرجع", field_used)
        if token_type:
            _log.warning("تنبيه: الاستجابة تحوي token_type=%r — المرجع لا يستخدمه", token_type)

        self._m.inc("token_refreshes")
        _log.info("token refreshed successfully (%s)", mask_secret(token))
        return token


class TelethonInitDataProvider(InitDataProvider):
    """
    تنفيذ إنتاجي يولّد initData عبر Telethon.

    مهم: MRKT هو «Mini App برابط مباشر» (t.me/mrkt/app) ولا يملك زر WebView في
    قائمة المرفقات، لذلك يجب استخدام RequestAppWebViewRequest مع
    InputBotAppShortName(short_name="app"). استخدام RequestWebViewRequest معه
    يُرجع BotInvalidError. نُبقي RequestWebView كمسار احتياطي فقط.

    ملاحظة تحقّق: لا يُنفَّذ في المختبر (يتطلّب Telegram حيّاً + جلسة حقيقية).
    الأسرار (api_id/api_hash/session) تُقرأ من .env. Telethon يُستورَد بكسل.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session: str,
        bot_username: str = "mrkt",
        webview_url: str = "",
        app_short_name: str = "app",
    ):
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._bot = bot_username
        self._webview_url = webview_url
        self._app_short_name = app_short_name

    async def get_init_data(self) -> str:  # pragma: no cover - يتطلّب Telegram حيّاً
        if not (self._api_id and self._api_hash and self._session):
            raise AuthError("إعدادات Telethon ناقصة (api_id/api_hash/session) في .env")
        try:
            from telethon import TelegramClient, functions, types
            from telethon.sessions import StringSession
        except ImportError as exc:
            raise RuntimeError("telethon غير مثبّت — مطلوب لتوليد initData") from exc

        client = TelegramClient(StringSession(self._session), self._api_id, self._api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise AuthError("جلسة Telethon غير مُصرَّح بها — جدّد TG_ASSISTANT_SESSION")

            peer = await client.get_input_entity(self._bot)
            bot_user = await client.get_entity(self._bot)
            input_user = types.InputUser(
                user_id=bot_user.id, access_hash=bot_user.access_hash
            )

            # المسار الصحيح لـ Mini App بالرابط المباشر
            try:
                result = await client(
                    functions.messages.RequestAppWebViewRequest(
                        peer=peer,
                        app=types.InputBotAppShortName(
                            bot_id=input_user, short_name=self._app_short_name
                        ),
                        platform="android",
                    )
                )
            except Exception as exc_app:
                # مسار احتياطي (بوتات ذات زر WebView تقليدي)
                _log.warning(
                    "RequestAppWebView فشل (%s) — محاولة RequestWebView الاحتياطية",
                    type(exc_app).__name__,
                )
                result = await client(
                    functions.messages.RequestWebViewRequest(
                        peer=peer,
                        bot=input_user,
                        platform="android",
                        url=self._webview_url or None,
                    )
                )

            init_data = _extract_init_data(result.url)
            if not init_data:
                raise AuthError("تعذّر استخراج initData من رابط WebView")
            return init_data
        finally:
            await client.disconnect()


def _extract_init_data(url: str) -> str:
    """
    يستخرج سلسلة initData من رابط WebView الناتج عن Telethon.

    الرابط يأتي عادةً بالشكل:
      https://cdn.tgmrkt.io/...#tgWebAppData=<encoded>&tgWebAppVersion=7.0&...
    نُعيد السلسلة بعد فكّ ترميز واحد (كما تتوقّعها /auth).
    """
    import urllib.parse as up

    if "tgWebAppData=" not in url:
        return ""
    raw = url.split("tgWebAppData=", 1)[1]
    # اقطع عند أول معامل تالٍ خاص بـ tgWebApp
    for sep in ("&tgWebAppVersion", "&tgWebAppPlatform", "&tgWebAppThemeParams", "&tgWebAppBotInline"):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
    return up.unquote(raw)
