# خدمة المقتنيات الرقمية (Collectibles Service)

خدمة مستقلة تحتوي كل منطق أعمال المقتنيات، ويكلّمها بوت متجر ROZ عبر REST داخلي.
مخزنها مستقل تماماً (`data/collectibles.db`) ولا تمسّ `store.db` الخاص بالبوت.

## البنية (طبقات معزولة)

```
app/
  config.py            إعدادات من البيئة فقط (بلا أسرار في الكود)
  core/
    models.py          النموذج الموحّد CanonicalCollectible (+ تسلسل)
  ports/               الواجهات (تجريدات)
    provider_port.py   عقد المزوّد (plugin)
    cache_repository.py عقد الكاش (مستقل عن التقنية)
    media_store.py     عقد تخزين الوسائط
  infra/               التنفيذ القابل للاستبدال
    cache_sqlite.py    SQLiteCacheRepository (collectibles.db)
    media_fs.py        FilesystemMediaStore (كاش صور على القرص)
  providers/           إضافات المزوّدين (plugins)
    __init__.py        السجل + المحمّل التلقائي
    base.py            أساس مشترك خفيف
data/                  المخزن + كاش الوسائط (يُنشأ وقت التشغيل)
```

## حالة المراحل

| المرحلة | المحتوى | الحالة |
|---|---|---|
| B | الهيكل + الواجهات + SQLite + Media FS | ✅ هذه النسخة |
| C | مِحوَل MRKT v1 + Token Manager + الصمود | ✅ هذه النسخة |
| D | GramPrice + Pricing Engine + كاش العملة + مقاييس/سجلّات + اختبارات | ✅ هذه النسخة |
| E | Sync Engine + Media Service + Health/Metrics + تشغيل VPS | ✅ هذه النسخة |
| F | REST API عام (كتالوج) + جسر عملة البوت | ✅ هذه النسخة |
| G | WebApp + صور متعددة + كاش أوفلاين + مفضّلة + وصولية + لقطات | ✅ هذه النسخة |
| H | ربط البوت (تمديد digital_collectibles) | لاحقاً |
| I | كرت إدارة المقتنيات | لاحقاً |

## ملاحظة أمنية
كل الأسرار (جلسة Telethon، توكن البوت، مفاتيح…) تُقرأ من `.env` فقط.
لا يوجد أي نقطة تشغيل (main.py) في Phase B — هذه طبقة مكتبة (scaffolding).
