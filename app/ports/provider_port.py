"""
منفذ المزوّد (ProviderPort) — العقد الذي يُنفّذه أي مزوّد مقتنيات (plugin).

إضافة مزوّد جديد مستقبلاً = تنفيذ هذا العقد فقط، دون تغيير في:
محرّك التسعير / الكاش / WebApp / البوت / منطق الأعمال.

الدوال غير المتزامنة (async) لأن التنفيذ الفعلي (Phase C) يستخدم I/O شبكياً.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Sequence, Tuple

from app.core.models import CanonicalCollectible


class ProviderPort(ABC):
    """واجهة موحّدة لأي مزوّد مقتنيات."""

    #: اسم المزوّد الفريد (يُستخدم في التسجيل والتخزين)، مثل "mrkt"
    name: str = "base"
    #: إصدار المِحوَل، مثل "v1"
    version: str = "v0"

    @abstractmethod
    async def authenticate(self) -> str:
        """يعيد توكن مصادقة صالحاً (يجدّده داخلياً عند الحاجة)."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_listings(self, cursor: str = "") -> Tuple[List[Dict[str, Any]], str]:
        """يجلب صفحة من المعروض. يعيد (عناصر خام، cursor التالي)."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_by_ids(self, ids: Sequence[str]) -> List[Dict[str, Any]]:
        """يجلب عناصر محدّدة بمعرّفاتها (خام)."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_one(self, item_id: str) -> Dict[str, Any]:
        """يجلب عنصراً مفرداً بمعرّفه (خام)."""
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: Dict[str, Any]) -> CanonicalCollectible:
        """يحوّل عنصراً خاماً إلى النموذج الموحّد (بلا I/O)."""
        raise NotImplementedError
