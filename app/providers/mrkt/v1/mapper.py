"""
مِحوَل التطبيع (Mapper): من استجابة MRKT الخام إلى النموذج الموحّد.

أسماء الحقول مؤكَّدة من عيّنة العقد الحقيقية:
  id, giftIdString, title/collectionTitle, collectionName, modelName,
  backdropName, symbolName, number, modelStickerThumbnailKey,
  salePrice (nanoTON), isOnSale, isLocked, unlockDate.

لا I/O هنا — تحويل صرف. thumbnail_url يُملأ لاحقاً في طبقة REST/الوسائط.
"""
from __future__ import annotations

from typing import Any, Dict

from app.core.models import CanonicalCollectible

PROVIDER_NAME = "mrkt"


def _s(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def map_gift(raw: Dict[str, Any]) -> CanonicalCollectible:
    """يحوّل عنصر MRKT خاماً إلى CanonicalCollectible موحّد."""
    item = CanonicalCollectible(
        provider=PROVIDER_NAME,
        provider_item_id=_s(raw.get("id")),
        external_id=_s(raw.get("giftIdString") or raw.get("giftId")),
        title=_s(raw.get("title") or raw.get("collectionTitle")),
        collection=_s(raw.get("collectionName")),
        model=_s(raw.get("modelName")),
        backdrop=_s(raw.get("backdropName")),
        symbol=_s(raw.get("symbolName")),
        number=raw.get("number") if isinstance(raw.get("number"), int) else None,
        media_key=_s(raw.get("modelStickerThumbnailKey")),
        thumbnail_url="",  # يُبنى لاحقاً من media_key عبر خدمة الوسائط
        price_nano=int(raw.get("salePrice") or 0),
        is_on_sale=bool(raw.get("isOnSale")),
        is_locked=bool(raw.get("isLocked")),
        unlock_date=raw.get("unlockDate"),
        raw=raw,
    )
    return item.stamp()
