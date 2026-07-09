"""
خدمة الكتالوج (Catalog Service) — القراءة العامة المُسعّرة.

تقرأ العناصر الموحّدة من الكاش (مصدر الحقيقة)، تُسعّرها بعملة المستخدم عبر
PricingService، وتُخرج **تمثيلاً آمناً للعميل فقط**: لا عمولة، لا سعر GRAM،
لا سعر MRKT الخام، لا حسابات داخلية، لا حقول خاصة بالمزوّد.

نطاق السعر (price_min/price_max) يُستقبَل بعملة العميل ويُحوَّل عكسياً إلى حدود
nano خادمياً (يبقى الفرز/الترقيم في قاعدة البيانات، فيتوسّع لآلاف العناصر).

مستقلة عن المزوّد تماماً (تتعامل مع CanonicalCollectible فقط).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.models import CanonicalCollectible
from app.core.pricing_engine import NANO
from app.core.pricing_service import PricingService
from app.ports.cache_repository import CacheRepository

# فئات الواجهة (chips) — ديناميكية عبر الـ API
_CATEGORIES = [
    {"id": "all", "label": "الكل", "kind": "sort", "sort": "newest"},
    {"id": "trending", "label": "الرائج", "kind": "sort", "sort": "newest"},  # popular: مستقبلاً
    {"id": "new", "label": "جديد", "kind": "sort", "sort": "newest"},
    {"id": "cheapest", "label": "الأرخص", "kind": "sort", "sort": "cheapest"},
    {"id": "expensive", "label": "الأغلى", "kind": "sort", "sort": "expensive"},
    {"id": "collections", "label": "المجموعات", "kind": "collections"},
]


def public_item(item: CanonicalCollectible, price: Dict[str, Any]) -> Dict[str, Any]:
    """تمثيل آمن للعميل. price = ناتج customer_view() (عملة + سعر نهائي فقط)."""
    base = item.thumbnail_url or ""
    if base:
        sep = "&" if "?" in base else "?"
        images = {
            "thumb": f"{base}{sep}w=160",
            "medium": f"{base}{sep}w=480",
            "original": base,
        }
    else:
        images = {"thumb": "", "medium": "", "original": ""}
    return {
        "id": item.provider_item_id,
        "title": item.title,
        "collection": item.collection,
        "model": item.model,
        "backdrop": item.backdrop,
        "symbol": item.symbol,
        "number": item.number,
        "thumbnail_url": images["thumb"],   # توافق: أصغر نسخة للشبكة
        "images": images,
        "is_locked": item.is_locked,
        "unlock_date": item.unlock_date,
        "price": {
            "currency": price["currency"],
            "amount": price["amount"],
            "amount_exact": price["amount_exact"],
        },
    }


class CatalogService:
    def __init__(self, cache_repo: CacheRepository, pricing: PricingService, provider_name: str = "mrkt"):
        self._repo = cache_repo
        self._pricing = pricing
        self._provider = provider_name

    async def _price_bounds_to_nano(
        self, user_id: int, price_min: Optional[str], price_max: Optional[str]
    ) -> Dict[str, int]:
        """يحوّل حدود السعر بعملة العميل إلى حدود nano (عكس خط التسعير)."""
        if price_min is None and price_max is None:
            return {}
        gram = await self._pricing._gram.get()
        currency = await self._pricing._currency.get(user_id)
        commission = self._pricing.get_commission_usd()
        rate = self._pricing._rate_for(currency)
        if gram <= 0 or rate <= 0:
            return {}
        out: Dict[str, int] = {}

        def to_nano(amount: str) -> int:
            final = Decimal(str(amount))
            nano = ((final / rate) - commission) / gram * NANO
            return max(0, int(nano))

        try:
            if price_min is not None:
                out["price_nano_min"] = to_nano(price_min)
            if price_max is not None:
                out["price_nano_max"] = to_nano(price_max)
        except Exception:
            return {}
        return out

    async def list_items(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        f = dict(filters or {})
        # حوّل نطاق السعر (بعملة العميل) إلى nano
        bounds = await self._price_bounds_to_nano(
            user_id, f.pop("price_min", None), f.pop("price_max", None)
        )
        f.update(bounds)
        items = self._repo.list_for_sale(limit=limit, offset=offset, filters=f, sort=sort)
        total = self._repo.count_for_sale(filters=f)
        out: List[Dict[str, Any]] = []
        for it in items:
            breakdown = await self._pricing.price_item(it, user_id)
            out.append(public_item(it, breakdown.customer_view()))
        return {
            "items": out,
            "paging": {"limit": limit, "offset": offset, "total": total,
                       "has_more": offset + len(out) < total},
        }

    async def get_item(self, user_id: int, item_id: str) -> Optional[Dict[str, Any]]:
        it = self._repo.get_item(self._provider, item_id)
        if it is None or not it.is_on_sale:
            return None
        breakdown = await self._pricing.price_item(it, user_id)
        return public_item(it, breakdown.customer_view())

    def categories(self) -> List[Dict[str, Any]]:
        return list(_CATEGORIES)

    def facets(self) -> Dict[str, List[str]]:
        return {
            "collections": self._repo.distinct_values("collection", self._provider),
            "models": self._repo.distinct_values("model", self._provider),
            "backdrops": self._repo.distinct_values("backdrop", self._provider),
            "symbols": self._repo.distinct_values("symbol", self._provider),
        }
