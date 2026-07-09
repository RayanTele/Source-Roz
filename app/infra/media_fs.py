"""
تنفيذ كاش الوسائط على نظام الملفات (FilesystemMediaStore).

المفاتيح تأتي بصيغة مثل: gifts/stickers/thumbnails/<hex>.webp
لتفادي أي مسار غير آمن (path traversal) نشتقّ اسم الملف من sha256(key)
مع الحفاظ على الامتداد، ونوزّعه على أدلة فرعية (sharding) بأول حرفين.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional

from app.ports.media_store import MediaStore


class FilesystemMediaStore(MediaStore):
    """تخزين الصور المكاشة على القرص، بمسار مشتقّ آمن من المفتاح."""

    def __init__(self, media_dir: str):
        self._root = os.path.abspath(media_dir)
        os.makedirs(self._root, exist_ok=True)

    # ── اشتقاق المسار الآمن ──
    @staticmethod
    def _ext_of(key: str) -> str:
        base = key.rsplit("/", 1)[-1]
        if "." in base:
            ext = base.rsplit(".", 1)[-1]
            # امتداد نظيف فقط (أحرف/أرقام قصيرة)
            if ext.isalnum() and len(ext) <= 5:
                return "." + ext.lower()
        return ""

    def _rel_path(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(digest[:2], digest + self._ext_of(key))

    def _abs_path(self, key: str) -> str:
        return os.path.join(self._root, self._rel_path(key))

    # ── واجهة MediaStore ──
    def has(self, key: str) -> bool:
        return os.path.isfile(self._abs_path(key))

    def get_path(self, key: str) -> Optional[str]:
        p = self._abs_path(key)
        return p if os.path.isfile(p) else None

    def put(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        p = self._abs_path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # كتابة ذرّية عبر ملف مؤقت ثم إعادة تسمية
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
        return p

    def delete(self, key: str) -> bool:
        p = self._abs_path(key)
        if os.path.isfile(p):
            os.remove(p)
            return True
        return False

    def stats(self) -> Dict[str, Any]:
        count = 0
        total = 0
        for dirpath, _dirs, files in os.walk(self._root):
            for name in files:
                if name.endswith(".tmp"):
                    continue
                fp = os.path.join(dirpath, name)
                try:
                    total += os.path.getsize(fp)
                    count += 1
                except OSError:
                    pass
        return {"files": count, "bytes": total, "root": self._root}
