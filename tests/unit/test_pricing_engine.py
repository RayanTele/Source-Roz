"""اختبارات وحدة لمحرّك التسعير — صحّة Decimal ودقّة خط الأنابيب."""
import unittest
from decimal import Decimal

from app.core.pricing_engine import PricingEngine, NANO


class TestPricingEngine(unittest.TestCase):
    def test_full_pipeline_example(self):
        # مثال ريان: 2 GRAM، سعر GRAM=2.05$، عمولة 1$، YER=540
        # price_nano لـ 2 GRAM = 2 * 1e9
        b = PricingEngine.price(
            price_nano=2 * 1_000_000_000,
            gram_usdt="2.05",
            commission_usd="1",
            currency="YER",
            rate="540",
        )
        self.assertEqual(b.gram, Decimal("2"))
        self.assertEqual(b.usd_base, Decimal("4.10"))
        self.assertEqual(b.usd_total, Decimal("5.10"))
        self.assertEqual(b.final_amount, Decimal("2754.00"))

    def test_nano_division_precision(self):
        b = PricingEngine.price(14950000000, "2.0", "0", "USD", "1")
        self.assertEqual(b.gram, Decimal("14.95"))
        self.assertEqual(b.final_amount, Decimal("29.90"))

    def test_decimal_no_float_error(self):
        # 0.1 + 0.2 مع العوائم = 0.30000000000000004؛ مع Decimal يجب أن يكون دقيقاً
        b = PricingEngine.price(100000000, "0.1", "0.2", "USD", "1")  # 0.1 GRAM
        # gram=0.1 ; usd_base=0.1*0.1=0.01 ; +0.2 = 0.21
        self.assertEqual(b.usd_total, Decimal("0.21"))

    def test_zero_price(self):
        b = PricingEngine.price(0, "2.05", "1", "YER", "540")
        self.assertEqual(b.usd_base, Decimal("0"))
        self.assertEqual(b.final_amount, Decimal("540.00"))  # فقط العمولة × السعر

    def test_rounded_display(self):
        b = PricingEngine.price(1_234_567_890, "2.05", "0.33", "SAR", "3.757")
        self.assertEqual(b.rounded(2), b.final_amount.quantize(Decimal("0.01")))
        d = b.to_dict()
        self.assertIn("final_rounded", d)
        self.assertIsInstance(d["gram"], str)  # مسلسل كنص

    def test_nano_constant(self):
        self.assertEqual(NANO, Decimal(1_000_000_000))


if __name__ == "__main__":
    unittest.main()
