"""
منفذ الوسائط (MediaStore) — عقد تخزين الصور مستقل عن التقنية.

التنفيذ الأول على نظام الملفات (FilesystemMediaStore)، وقابل للاستبدال
لاحقاً بـ S3/CDN دون تغيير خدمة الوسائط أو بقية المنطق.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class MediaStore(ABC):
    """واجهة تخزين/استرجاع الصور المكاشة بمفتاح المصدر."""

    @abstractmethod
    def has(self, key: str) -> bool:
        """هل الصورة المقابلة للمفتاح مخزّنة محلياً؟"""
        raise NotImplementedError

    @abstractmethod
    def get_path(self, key: str) -> Optional[str]:
        """المسار المحلي للصورة المخزّنة، أو None إن لم تكن موجودة."""
        raise NotImplementedError

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        """يخزّن بيانات الصورة تحت المفتاح ويعيد المسار المحلي."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> bool:
        """يحذف صورة مخزّنة. يعيد True إن حُذفت فعلاً."""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """إحصاءات للصحة: عدد الملفات وإجمالي الحجم بالبايت."""
        raise NotImplementedError
