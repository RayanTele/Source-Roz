"""اختبار وحدة: كائن الصور (thumb/medium/original) + تصغير Pillow مع تدهور رشيق."""
import io
import os
import tempfile
import unittest

from app.core.catalog_service import public_item
from app.core.media_service import MediaService
from app.core.models import CanonicalCollectible
from app.infra.media_fs import FilesystemMediaStore


class _Http:
    async def request(self, *a, **k): return None
    async def close(self): pass


class TestImages(unittest.TestCase):
    def test_public_item_images_object(self):
        it = CanonicalCollectible("mrkt", "u1", "9", title="T",
                                  thumbnail_url="/media/gifts/k.webp",
                                  price_nano=1_000_000_000, is_on_sale=True)
        pub = public_item(it, {"currency": "YER", "amount": "540.00", "amount_exact": "540.00"})
        self.assertEqual(pub["images"]["thumb"], "/media/gifts/k.webp?w=160")
        self.assertEqual(pub["images"]["medium"], "/media/gifts/k.webp?w=480")
        self.assertEqual(pub["images"]["original"], "/media/gifts/k.webp")
        self.assertEqual(pub["thumbnail_url"], pub["images"]["thumb"])

    def test_images_empty_when_no_media(self):
        it = CanonicalCollectible("mrkt", "u2", "1", price_nano=0, is_on_sale=True)
        pub = public_item(it, {"currency": "USD", "amount": "0", "amount_exact": "0"})
        self.assertEqual(pub["images"], {"thumb": "", "medium": "", "original": ""})

    def test_resize_and_fallback(self):
        store = FilesystemMediaStore(tempfile.mkdtemp())
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow غير مثبّت")
        buf = io.BytesIO(); Image.new("RGB", (600, 600), (200, 120, 140)).save(buf, format="WEBP")
        store.put("gifts/k.webp", buf.getvalue())
        ms = MediaService(store, _Http(), cdn_base_url="https://cdn")
        p = ms.resized_path("gifts/k.webp", 160)
        self.assertTrue(os.path.isfile(p))
        with Image.open(p) as im:
            self.assertLessEqual(max(im.size), 160)
        # عرض غير مسموح → يعود للأصل
        self.assertEqual(ms.resized_path("gifts/k.webp", 9999), store.get_path("gifts/k.webp"))
        # مفتاح غير موجود → None
        self.assertIsNone(ms.resized_path("nope.webp", 160))


if __name__ == "__main__":
    unittest.main()
