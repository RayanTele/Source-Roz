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
    ):
        self._http = http
        self._base = base_url.rstrip("/")
        self._idp = init_data_provider
        self._app_id = app_id
        self._auth_path = auth_path
        self._timeout = timeout
        self._m = resolve(metrics)
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

    async def _authenticate(self) -> str:
        init_data = await self._idp.get_init_data()
        if not init_data:
            raise AuthError("initData فارغ — تعذّر توليد المصادقة")
        payload = {"data": init_data, "appId": self._app_id}
        resp = await self._http.request(
            "POST", f"{self._base}{self._auth_path}", json=payload, timeout=self._timeout
        )
        if resp.status == 401:
            raise AuthError("رُفضت المصادقة (401) عند /auth")
        if resp.status >= 400:
            raise ProviderError(f"فشل /auth بحالة {resp.status}")
        data = resp.json() or {}
        token = data.get("token")
        if not token:
            raise ProviderError("استجابة /auth بلا حقل token")
        self._m.inc("token_refreshes")
        _log.info("token refreshed successfully")
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
