"""
النموذج الموحّد للمقتنيات (Canonical Model).

هذا النموذج محايد تماماً عن أي مزوّد. كل مِحوَل مزوّد (MRKT، مستقبلاً غيره)
يُحوّل استجابته الخام إلى هذا النموذج، فيستهلكه بقية النظام (كاش/تسعير/REST/WebApp)
دون معرفة أي تفاصيل خاصة بمزوّد بعينه.

الأسعار تُخزَّن بوحدة nanoTON (int64) كما يُرجعها المصدر؛ التحويل للعملة
يحدث في محرّك التسعير لاحقاً (Phase D)، لا هنا.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class CanonicalCollectible:
    """عنصر مقتنى موحّد، مستقل عن المزوّد."""

    # ── الهوية ──
    provider: str                       # اسم المزوّد، مثل "mrkt"
    provider_item_id: str               # معرّف المصدر (uuid في MRKT: الحقل id)
    external_id: str                    # giftIdString — يُستخدم بدل الرقم لتفادي فقد الدقة

    # ── العرض ──
    title: str = ""                     # collectionTitle / title
    collection: str = ""                # collectionName
    model: str = ""                     # modelName
    backdrop: str = ""                  # backdropName
    symbol: str = ""                    # symbolName
    number: Optional[int] = None        # رقم النسخة

    # ── الوسائط ──
    media_key: str = ""                 # مفتاح الصورة الخام من المصدر (thumbnail key)
    thumbnail_url: str = ""             # رابط /media الخاص بنا (يُملأ في طبقة REST لاحقاً)

    # ── السعر والحالة ──
    price_nano: int = 0                 # salePrice بوحدة nanoTON (int64)
    is_on_sale: bool = False
    is_locked: bool = False
    unlock_date: Optional[str] = None   # ISO-8601 كما يأتي من المصدر (أو None)

    # ── مرجعية وتتبّع ──
    raw: Dict[str, Any] = field(default_factory=dict)   # النسخة الخام للمرجعية
    synced_at: float = 0.0              # طابع زمني (epoch) للمزامنة

    def stamp(self) -> "CanonicalCollectible":
        """يضبط طابع المزامنة إلى اللحظة الحالية ويعيد الكائن نفسه (سلسلة)."""
        self.synced_at = time.time()
        return self

    # ── التسلسل للتخزين (SQLite) ──
    def to_row(self) -> Dict[str, Any]:
        """يحوّل الكائن لصف قابل للتخزين (raw كنص JSON)."""
        d = asdict(self)
        d["raw"] = json.dumps(self.raw, ensure_ascii=False)
        d["is_on_sale"] = 1 if self.is_on_sale else 0
        d["is_locked"] = 1 if self.is_locked else 0
        return d

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CanonicalCollectible":
        """يعيد بناء الكائن من صف مخزَّن."""
        raw_val = row.get("raw") or "{}"
        try:
            raw_obj = json.loads(raw_val) if isinstance(raw_val, str) else dict(raw_val)
        except (ValueError, TypeError):
            raw_obj = {}
        return cls(
            provider=row["provider"],
            provider_item_id=row["provider_item_id"],
            external_id=row.get("external_id", ""),
            title=row.get("title", "") or "",
            collection=row.get("collection", "") or "",
            model=row.get("model", "") or "",
            backdrop=row.get("backdrop", "") or "",
            symbol=row.get("symbol", "") or "",
            number=row.get("number"),
            media_key=row.get("media_key", "") or "",
            thumbnail_url=row.get("thumbnail_url", "") or "",
            price_nano=int(row.get("price_nano") or 0),
            is_on_sale=bool(row.get("is_on_sale")),
            is_locked=bool(row.get("is_locked")),
            unlock_date=row.get("unlock_date"),
            raw=raw_obj,
            synced_at=float(row.get("synced_at") or 0.0),
        )

    def to_public_dict(self) -> Dict[str, Any]:
        """تمثيل عام آمن للعرض (بلا الحقل raw الثقيل)."""
        return {
            "provider": self.provider,
            "id": self.provider_item_id,
            "external_id": self.external_id,
            "title": self.title,
            "collection": self.collection,
            "model": self.model,
            "backdrop": self.backdrop,
            "symbol": self.symbol,
            "number": self.number,
            "thumbnail_url": self.thumbnail_url,
            "price_nano": self.price_nano,
            "is_on_sale": self.is_on_sale,
            "is_locked": self.is_locked,
            "unlock_date": self.unlock_date,
            "synced_at": self.synced_at,
        }
