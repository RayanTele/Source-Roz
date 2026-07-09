"""
محرّك المزامنة (Sync Engine).

يجلب المعروض صفحةً صفحة عبر cursor حتى النفاد (total مُهمَل)، يطبّع كل عنصر
للنموذج الموحّد، يضمن صوره عبر خدمة الوسائط، يخزّنه في الكاش (مصدر الحقيقة)،
ثم يحذف ما لم يُرَ في هذه الجولة (مقيّداً بالمزوّد). يسجّل المقاييس وبيانات
الصحة (آخر مزامنة/المدّة/جديد/محدَّث/أخطاء).

مستقل عن المزوّد: يتعامل مع ProviderPort وCanonicalCollectible فقط.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.core.logging_setup import get_logger, request_context
from app.core.metrics import Metrics, resolve
from app.core.media_service import MediaService
from app.ports.cache_repository import CacheRepository
from app.ports.provider_port import ProviderPort

_log = get_logger("sync")

# مفاتيح meta للصحة
META_LAST_SYNC = "last_sync_ts"
META_LAST_DURATION = "last_sync_duration"
META_LAST_ERROR = "last_sync_error"
META_LAST_NEW = "last_sync_new"
META_LAST_UPDATED = "last_sync_updated"
META_CACHED_COUNT = "cached_count"


@dataclass
class SyncResult:
    ok: bool
    pages: int
    new: int
    updated: int
    pruned: int
    total_seen: int
    duration: float
    error: Optional[str] = None


class SyncEngine:
    def __init__(
        self,
        provider: ProviderPort,
        cache_repo: CacheRepository,
        *,
        media_service: Optional[MediaService] = None,
        max_pages: int = 500,
        metrics: Optional[Metrics] = None,
    ):
        self._provider = provider
        self._repo = cache_repo
        self._media = media_service
        self._max_pages = max_pages
        self._m = resolve(metrics)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_once(self) -> SyncResult:
        """جولة مزامنة كاملة واحدة. لا ترفع استثناءً — تُرجع نتيجة تحمل الخطأ."""
        if self._running:
            return SyncResult(False, 0, 0, 0, 0, 0, 0.0, error="sync already running")
        self._running = True
        start = time.time()
        new = updated = pages = total_seen = 0
        error: Optional[str] = None
        with request_context(provider=getattr(self._provider, "name", "?")):
            try:
                cursor = ""
                while pages < self._max_pages:
                    raw_items, cursor = await self._provider.fetch_listings(cursor)
                    if not raw_items:
                        break
                    canon = [self._provider.normalize(r) for r in raw_items]
                    for c in canon:
                        if self._media is not None and c.media_key:
                            c.thumbnail_url = await self._media.ensure(c.media_key)
                        existing = self._repo.get_item(c.provider, c.provider_item_id)
                        if existing is None:
                            new += 1
                        else:
                            updated += 1
                    self._repo.upsert_items(canon)
                    total_seen += len(canon)
                    pages += 1
                    if not cursor:
                        break

                pruned = self._repo.prune_stale(
                    before_ts=start, provider=getattr(self._provider, "name", None)
                )
                duration = time.time() - start
                self._record_success(new, updated, total_seen, duration)
                _log.info(
                    "sync ok: pages=%s new=%s updated=%s pruned=%s in %.2fs",
                    pages, new, updated, pruned, duration,
                )
                return SyncResult(True, pages, new, updated, pruned, total_seen, duration)
            except Exception as exc:  # عزل: لا يُسقط الخدمة
                duration = time.time() - start
                error = f"{type(exc).__name__}: {exc}"
                self._m.inc("sync_failures")
                self._repo.set_meta(META_LAST_ERROR, error)
                _log.error("sync failed after %.2fs: %s", duration, error)
                return SyncResult(False, pages, new, updated, 0, total_seen, duration, error=error)
            finally:
                self._running = False

    def _record_success(self, new: int, updated: int, total_seen: int, duration: float) -> None:
        self._m.inc("sync_runs")
        self._m.observe("sync_duration", duration)
        self._m.inc("items_new", new)
        self._m.inc("items_updated", updated)
        count = total_seen
        try:
            count = self._repo.count_items(getattr(self._provider, "name", None))  # type: ignore[attr-defined]
        except AttributeError:
            pass
        self._m.gauge("cached_items", count)
        self._repo.set_meta(META_LAST_SYNC, str(time.time()))
        self._repo.set_meta(META_LAST_DURATION, f"{duration:.3f}")
        self._repo.set_meta(META_LAST_NEW, str(new))
        self._repo.set_meta(META_LAST_UPDATED, str(updated))
        self._repo.set_meta(META_CACHED_COUNT, str(count))
        self._repo.set_meta(META_LAST_ERROR, "")
