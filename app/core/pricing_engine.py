"""
محرّك التسعير (Pricing Engine) — مستقل تماماً عن أي مزوّد.

يستهلك أوّليات فقط (سعر nano + سعر GRAM + عمولة + عملة + سعر صرف)،
ولا يعرف أي شيء عن MRKT أو غيره. كل الحسابات النقدية بـ Decimal لتفادي
أخطاء دقّة العوائم.

خط الأنابيب:
    gram      = price_nano / 1e9
    usd_base  = gram * gram_usdt
    usd_total = usd_base + commission_usd
    final     = usd_total * rate[currency]
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

NANO = Decimal(1_000_000_000)


def _dec(value: Any) -> Decimal:
    """تحويل آمن إلى Decimal عبر str (يتفادى تلوّث العوائم)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class PriceBreakdown:
    """تفصيل السعر بكل مراحله (كلها Decimal)."""

    price_nano: int
    gram: Decimal
    gram_usdt: Decimal
    usd_base: Decimal
    commission_usd: Decimal
    usd_total: Decimal
    currency: str
    rate: Decimal
    final_amount: Decimal

    def rounded(self, places: int = 2) -> Decimal:
        """السعر النهائي مقرّباً لعدد المنازل المطلوب (للعرض)."""
        q = Decimal(10) ** (-places)
        return self.final_amount.quantize(q, rounding=ROUND_HALF_UP)

    def customer_view(self, places: int = 2) -> Dict[str, Any]:
        """
        الإسقاط الآمن للعميل — السعر النهائي والعملة فقط.
        لا يحوي العمولة ولا أي قيم داخلية (USD/GRAM/rate). هذا هو الشكل
        الوحيد المسموح إرساله للـ WebApp/APIs/البوت.
        """
        return {
            "currency": self.currency,
            "amount": str(self.rounded(places)),
            "amount_exact": str(self.final_amount),
        }

    def to_dict(self) -> Dict[str, Any]:
        """
        تمثيل داخلي كامل (يحوي العمولة والقيم الوسيطة) — للإدارة/التشخيص فقط.
        ممنوع إرساله لأي عميل خارجي؛ استخدم customer_view() لذلك.
        """
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
        d["final_rounded"] = str(self.rounded())
        return d


class PricingEngine:
    """حساب سعر خالص (بلا I/O، بلا معرفة بالمزوّد)."""

    @staticmethod
    def price(
        price_nano: int,
        gram_usdt: Any,
        commission_usd: Any,
        currency: str,
        rate: Any,
    ) -> PriceBreakdown:
        gram = _dec(int(price_nano)) / NANO
        gram_usdt_d = _dec(gram_usdt)
        commission_d = _dec(commission_usd)
        rate_d = _dec(rate)

        usd_base = gram * gram_usdt_d
        usd_total = usd_base + commission_d
        final = usd_total * rate_d

        return PriceBreakdown(
            price_nano=int(price_nano),
            gram=gram,
            gram_usdt=gram_usdt_d,
            usd_base=usd_base,
            commission_usd=commission_d,
            usd_total=usd_total,
            currency=currency,
            rate=rate_d,
            final_amount=final,
        )
