"""
منفذ الكاش (CacheRepository) — عقد تخزين مستقل عن التقنية.

منطق الأعمال (المزامنة/التسعير) يعتمد على هذا العقد فقط، لا على SQLite.
يمكن لاحقاً استبدال التنفيذ بـ Postgres/Redis دون تغيير منطق الأعمال
(Dependency Inversion).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.models import CanonicalCollectible


class CacheRepository(ABC):
    """واجهة مستودع كاش المقتنيات + البيانات المصاحبة (meta / أسعار الصرف)."""

    @abstractmethod
    def init_schema(self) -> None:
        """ينشئ الجداول إن لم تكن موجودة (idempotent)."""
        raise NotImplementedError

    # ── عناصر المقتنيات ──
    @abstractmethod
    def upsert_items(self, items: Sequence[CanonicalCollectible]) -> int:
        """يُدرج/يحدّث دفعة عناصر. يعيد عدد الصفوف المتأثّرة."""
        raise NotImplementedError

    @abstractmethod
    def get_item(self, provider: str, provider_item_id: str) -> Optional[CanonicalCollectible]:
        """يعيد عنصراً بمعرّفه أو None."""
        raise NotImplementedError

    @abstractmethod
    def list_for_sale(
        self,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> List[CanonicalCollectible]:
        """يعيد قائمة العناصر المعروضة للبيع (مع ترقيم وفرز داخليين)."""
        raise NotImplementedError

    @abstractmethod
    def distinct_values(self, field: str, provider: Optional[str] = None) -> List[str]:
        """قيم متمايزة لحقل عرض (collection/model/backdrop/symbol) للفلاتر."""
        raise NotImplementedError

    @abstractmethod
    def count_for_sale(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """يعيد عدد العناصر المعروضة للبيع المطابقة للفلاتر."""
        raise NotImplementedError

    @abstractmethod
    def prune_stale(self, before_ts: float, provider: Optional[str] = None) -> int:
        """يحذف العناصر التي لم تُزامَن منذ before_ts (مقيّدة بمزوّد إن حُدِّد). يعيد عدد المحذوف."""
        raise NotImplementedError

    # ── بيانات وصفية (meta): آخر مزامنة، cursor، إلخ ──
    @abstractmethod
    def get_meta(self, key: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None:
        raise NotImplementedError

    # ── نسخة قراءة لأسعار الصرف (البوت هو المصدر الوحيد للكتابة الأصلية) ──
    @abstractmethod
    def get_rate(self, currency: str) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    def upsert_rates(self, rates: Dict[str, float]) -> int:
        """يحدّث نسخة الأسعار المحلية. يعيد عدد المفاتيح المكتوبة."""
        raise NotImplementedError
