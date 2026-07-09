"""
خدمة الوسائط (Media Service).

بدل أن يطلب الـ WebApp الصور من tgmrkt.io مباشرةً، تجلبها الخدمة من الـ CDN
وتخزّنها عبر MediaStore وتبني رابط /media الخاص بنا. عند فشل الجلب لا نُسقط
المزامنة — نترك الجلب كسولاً لاحقاً. مستقلة عن أي مزوّد (تأخذ مفتاح صورة فقط).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logging_setup import get_logger
from app.core.metrics import Metrics, resolve
from app.infra.http_client import AsyncHttpClient
from app.ports.media_store import MediaStore

_log = get_logger("media")


class MediaService:
    def __init__(
        self,
        store: MediaStore,
        http: AsyncHttpClient,
        *,
        cdn_base_url: str,
        media_base_url: str = "",
        timeout: float = 20.0,
        metrics: Optional[Metrics] = None,
    ):
        self._store = store
        self._http = http
        self._cdn = cdn_base_url.rstrip("/")
        self._media_base = media_base_url.rstrip("/")
        self._timeout = timeout
        self._m = resolve(metrics)

    def public_url(self, media_key: str) -> str:
        """يبني رابط الوسائط الخاص بنا من المفتاح (نسبي إن لم يُضبط النطاق بعد)."""
        if not media_key:
            return ""
        base = self._media_base
        return f"{base}/media/{media_key}" if base else f"/media/{media_key}"

    async def ensure(self, media_key: str) -> str:
        """
        يضمن وجود الصورة في الكاش (يجلبها عند الغياب) ويعيد رابطنا العام.
        فشل الجلب لا يرفع استثناءً — يُعاد الرابط والجلب يبقى كسولاً لاحقاً.
        """
        if not media_key:
            return ""
        try:
            if self._store.has(media_key):
                self._m.inc("media_hits")
            else:
                self._m.inc("media_misses")
                data = await self._download(media_key)
                if data is not None:
                    self._store.put(media_key, data)
                    self._m.inc("media_fetched")
        except Exception:
            self._m.inc("media_fetch_failures")
            _log.warning("media fetch failed for key (lazy retry later)")
        return self.public_url(media_key)

    async def _download(self, media_key: str) -> Optional[bytes]:
        url = f"{self._cdn}/{media_key.lstrip('/')}"
        resp = await self._http.request("GET", url, timeout=self._timeout)
        if resp.status >= 400:
            self._m.inc("media_fetch_failures")
            return None
        return resp.body

    def local_path(self, media_key: str) -> Optional[str]:
        """المسار المحلي للصورة (لخدمة /media)، أو None."""
        return self._store.get_path(media_key)

    # عروض مسموحة لمتغيّرات الحجم (thumb/medium)
    ALLOWED_WIDTHS = (160, 480)

    def resized_path(self, media_key: str, width: int) -> Optional[str]:
        """
        يعيد مسار نسخة مصغّرة بعرض width (مكاشة). يتطلّب Pillow؛ عند غيابه أو
        فشله يعيد الأصل (تدهور رشيق). العرض محصور بقائمة مسموحة.
        """
        orig = self._store.get_path(media_key)
        if not orig or not width or width not in self.ALLOWED_WIDTHS:
            return orig
        variant_key = f"{media_key}@w{width}"
        existing = self._store.get_path(variant_key)
        if existing:
            self._m.inc("media_variant_hits")
            return existing
        try:
            import io
            from PIL import Image

            with Image.open(orig) as im:
                fmt = (im.format or "WEBP")
                im.thumbnail((width, width))
                buf = io.BytesIO()
                im.save(buf, format=fmt)
            self._store.put(variant_key, buf.getvalue())
            self._m.inc("media_variant_created")
            return self._store.get_path(variant_key)
        except Exception:
            self._m.inc("media_variant_failures")
            return orig

    def stats(self) -> Dict[str, Any]:
        return self._store.stats()
