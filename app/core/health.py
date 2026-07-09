"""
مجمّع الصحة (Health Aggregator).

يبني لقطة موحّدة لحالة الخدمة للتشخيص الإنتاجي. **لا يحوي إطلاقاً** أي قيمة
عمولة أو سعر عميل — فقط مؤشّرات تشغيلية. تُقرأ عبر /health.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from app.core.media_service import MediaService
from app.core.metrics import Metrics, resolve
from app.core.sync_engine import (
    META_CACHED_COUNT,
    META_LAST_DURATION,
    META_LAST_ERROR,
    META_LAST_SYNC,
)
from app.infra.gram_price import GramPrice
from app.ports.cache_repository import CacheRepository
from app.ports.provider_port import ProviderPort

# حدّ قِدَم الكاش لاعتبار الحالة "متدهورة" (ثوانٍ)
DEFAULT_STALE_AFTER = 1800.0


class HealthService:
    def __init__(
        self,
        provider: ProviderPort,
        cache_repo: CacheRepository,
        media_service: MediaService,
        gram_price: GramPrice,
        metrics: Metrics,
        *,
        stale_after: float = DEFAULT_STALE_AFTER,
    ):
        self._provider = provider
        self._repo = cache_repo
        self._media = media_service
        self._gram = gram_price
        self._m = resolve(metrics)
        self._stale_after = stale_after

    def _meta_float(self, key: str) -> Optional[float]:
        v = self._repo.get_meta(key)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        last_sync = self._meta_float(META_LAST_SYNC)
        age = (now - last_sync) if last_sync else None
        last_error = self._repo.get_meta(META_LAST_ERROR) or ""
        cached = self._repo.get_meta(META_CACHED_COUNT)

        prov_health: Dict[str, Any] = {}
        if hasattr(self._provider, "health"):
            try:
                prov_health = self._provider.health()  # type: ignore[attr-defined]
            except Exception:
                prov_health = {}

        gram_cached = self._gram.cached()
        degraded = bool(last_error) or (age is not None and age > self._stale_after)

        return {
            "status": "degraded" if degraded else "ok",
            "time": now,
            "sync": {
                "last_sync_ts": last_sync,
                "age_seconds": round(age, 1) if age is not None else None,
                "last_duration": self._meta_float(META_LAST_DURATION),
                "last_error": last_error,
            },
            "cache": {"cached_items": int(cached) if cached and cached.isdigit() else cached},
            "gram_price": {"value": str(gram_cached) if gram_cached is not None else None},
            "token": {"has_token": prov_health.get("has_token")},
            "provider": {
                "name": prov_health.get("provider"),
                "version": prov_health.get("version"),
                "breaker": prov_health.get("breaker"),
            },
            "media": self._media.stats(),
        }

    def metrics_snapshot(self) -> Dict[str, Any]:
        """المقاييس المجمّعة (عدّادات/مقاييس/مؤقّتات) — بلا أي قيمة نقدية عميل."""
        return self._m.snapshot()
