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
    تنفيذ إنتاجي يولّد initData عبر Telethon (RequestWebViewRequest على @mrkt).

    ملاحظة تحقّق: لا يُنفَّذ في المختبر (يتطلّب Telegram حيّاً + جلسة حقيقية).
    كل الأسرار (api_id/api_hash/session) تُمرَّر من الإعدادات المقروءة من .env.
    Telethon يُستورَد داخل الدالة (كسل) كي لا يُلزِم الحزمة به وقت الاستيراد.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session: str,
        bot_username: str = "mrkt",
        webview_url: str = "",
    ):
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._bot = bot_username
        self._webview_url = webview_url

    async def get_init_data(self) -> str:  # pragma: no cover - يتطلّب Telegram حيّاً
        if not (self._api_id and self._api_hash and self._session):
            raise AuthError("إعدادات Telethon ناقصة (api_id/api_hash/session) في .env")
        try:
            from telethon import TelegramClient, functions
            from telethon.sessions import StringSession
        except ImportError as exc:
            raise RuntimeError("telethon غير مثبّت — مطلوب لتوليد initData") from exc

        client = TelegramClient(StringSession(self._session), self._api_id, self._api_hash)
        await client.connect()
        try:
            result = await client(
                functions.messages.RequestWebViewRequest(
                    peer=self._bot,
                    bot=self._bot,
                    platform="android",
                    url=self._webview_url or None,
                )
            )
            # يُستخرَج initData من الـ URL الناتج (جزء الاستعلام بعد #tgWebAppData=)
            return _extract_init_data(result.url)
        finally:
            await client.disconnect()


def _extract_init_data(url: str) -> str:
    """يستخرج سلسلة initData من رابط WebView الناتج عن Telethon."""
    import urllib.parse as up

    frag = url.split("#", 1)[1] if "#" in url else url
    params = up.parse_qs(frag)
    data = params.get("tgWebAppData", [""])[0]
    return data
