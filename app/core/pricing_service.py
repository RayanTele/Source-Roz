"""
خدمة التسعير (Pricing Service) — تنسّق مصادر السعر حول المحرّك الخالص.

مصدر الحقيقة للأسعار والعمولة هو الكاش (CacheRepository):
  - أسعار الصرف: collectibles_rates (نسخة قراءة يزامنها البوت لاحقاً).
  - العمولة (USD): collectibles_meta['commission_usd'] (تملكها الخدمة).
سعر GRAM من GramPrice، وعملة المستخدم من CurrencyCache.

مستقلة عن المزوّد: تستقبل CanonicalCollectible فقط (بعد التطبيع في المِحوَل).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.core.currency_cache import CurrencyCache
from app.core.metrics import Metrics, resolve
from app.core.models import CanonicalCollectible
from app.core.pricing_engine import PriceBreakdown, PricingEngine
from app.infra.gram_price import GramPrice
from app.ports.cache_repository import CacheRepository

_COMMISSION_KEY = "commission_usd"


class PricingService:
    def __init__(
        self,
        cache_repo: CacheRepository,
        gram_price: GramPrice,
        currency_cache: CurrencyCache,
        *,
        default_commission_usd: str = "0",
        default_currency: str = "USD",
        metrics: Optional[Metrics] = None,
    ):
        self._repo = cache_repo
        self._gram = gram_price
        self._currency = currency_cache
        self._default_commission = Decimal(str(default_commission_usd))
        self._default_currency = default_currency
        self._m = resolve(metrics)

    # ── العمولة (تملكها الخدمة، تُخزَّن في meta) ──
    def get_commission_usd(self) -> Decimal:
        val = self._repo.get_meta(_COMMISSION_KEY)
        if val is None:
            return self._default_commission
        try:
            return Decimal(val)
        except Exception:
            return self._default_commission

    def set_commission_usd(self, amount) -> Decimal:
        amt = Decimal(str(amount))
        self._repo.set_meta(_COMMISSION_KEY, str(amt))
        return amt

    # ── سعر الصرف من النسخة المحلية ──
    def _rate_for(self, currency: str) -> Decimal:
        rate = self._repo.get_rate(currency)
        if rate is None:
            # عملة غير معروفة في النسخة → الدولار (1:1) كسلوك آمن
            return Decimal("1")
        return Decimal(str(rate))

    # ── تسعير عنصر لمستخدم معيّن ──
    async def price_item(self, item: CanonicalCollectible, user_id: int) -> PriceBreakdown:
        currency = await self._currency.get(user_id)
        gram_usdt = await self._gram.get()
        commission = self.get_commission_usd()
        rate = self._rate_for(currency)
        breakdown = PricingEngine.price(
            price_nano=item.price_nano,
            gram_usdt=gram_usdt,
            commission_usd=commission,
            currency=currency,
            rate=rate,
        )
        self._m.inc("prices_computed")
        return breakdown

    # ── تسعير بعملة صريحة (دون مستخدم) — للوحة الإدارة/الاختبار ──
    async def price_in_currency(self, item: CanonicalCollectible, currency: str) -> PriceBreakdown:
        gram_usdt = await self._gram.get()
        commission = self.get_commission_usd()
        rate = self._rate_for(currency)
        return PricingEngine.price(
            price_nano=item.price_nano,
            gram_usdt=gram_usdt,
            commission_usd=commission,
            currency=currency,
            rate=rate,
        )
