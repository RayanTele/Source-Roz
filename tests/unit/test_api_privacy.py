"""اختبارات وحدة: تحقّق initData + خصوصية تمثيل الكتالوج."""
import hashlib
import hmac
import json
import time
import unittest
import urllib.parse

from app.core.catalog_service import public_item
from app.core.models import CanonicalCollectible
from app.core.pricing_engine import PricingEngine
from app.telegram.init_data import verify_init_data, InvalidInitData

TOKEN = "123456:TEST-BOT-TOKEN"


def make_init_data(token, user_id, auth_date=None):
    if auth_date is None:
        auth_date = str(int(time.time()))
    user = json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":"))
    fields = {"auth_date": auth_date, "query_id": "abc", "user": user}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class TestInitData(unittest.TestCase):
    def test_valid(self):
        p = verify_init_data(make_init_data(TOKEN, 1770929898), TOKEN)
        self.assertEqual(p.user_id, 1770929898)
        self.assertEqual(p.user["first_name"], "T")

    def test_tampered_hash_rejected(self):
        raw = make_init_data(TOKEN, 5) + "0"
        with self.assertRaises(InvalidInitData):
            verify_init_data(raw, TOKEN)

    def test_wrong_token_rejected(self):
        with self.assertRaises(InvalidInitData):
            verify_init_data(make_init_data(TOKEN, 5), "999:OTHER")

    def test_expired_rejected(self):
        raw = make_init_data(TOKEN, 5, auth_date="1000000000")  # قديم جداً
        with self.assertRaises(InvalidInitData):
            verify_init_data(raw, TOKEN, max_age_seconds=3600, now=1783000000)

    def test_missing_hash_rejected(self):
        with self.assertRaises(InvalidInitData):
            verify_init_data("user=%7B%22id%22%3A1%7D&auth_date=1", TOKEN)


class TestCatalogPrivacy(unittest.TestCase):
    def _item_and_price(self):
        item = CanonicalCollectible(
            provider="mrkt", provider_item_id="uuid-1", external_id="5931453499106658162",
            title="Liberty", collection="Liberty", model="Moonwalker", backdrop="Amber",
            symbol="Box", number=42, media_key="k.webp", thumbnail_url="https://d/media/k.webp",
            price_nano=14950000000, is_on_sale=True, is_locked=True,
            raw={"salePrice": 14950000000, "giftId": 5931453499106658162, "collectionFloor": 999},
        )
        b = PricingEngine.price(item.price_nano, "2.05", "1.5", "YER", "540")
        return item, b

    def test_public_item_only_final_price_and_display(self):
        item, b = self._item_and_price()
        pub = public_item(item, b.customer_view())
        blob = json.dumps(pub).lower()
        for forbidden in ("commission", "gram", "usd", "saleprice", "price_nano",
                          "giftid", "external_id", "raw", "rate", "nano", "5931453499106658162",
                          "14950000000"):
            self.assertNotIn(forbidden, blob, f"leaked: {forbidden}")

    def test_public_item_shape(self):
        item, b = self._item_and_price()
        pub = public_item(item, b.customer_view())
        self.assertEqual(pub["id"], "uuid-1")  # معرّف معتم
        self.assertEqual(set(pub["price"].keys()), {"currency", "amount", "amount_exact"})
        self.assertEqual(pub["price"]["currency"], "YER")
        # حقول العرض المسموحة موجودة
        for k in ("title", "collection", "model", "backdrop", "symbol", "thumbnail_url"):
            self.assertIn(k, pub)
        # لا حقول ممنوعة
        self.assertNotIn("external_id", pub)
        self.assertNotIn("price_nano", pub)


if __name__ == "__main__":
    unittest.main()
