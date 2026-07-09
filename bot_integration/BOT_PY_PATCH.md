# تعديل bot.py — جسر عملة المقتنيات (إضافة رفيعة)

> ⚠️ **مستوى التحقّق: فحص كود ثابت فقط (static analysis).**
> لا يمكنني تشغيل fork الخاص بـ PTB 22.7 في بيئتي، لذلك **لم أُنفّذ import/startup
> validation على bot.py**. يجب تنفيذها على الـ VPS قبل اعتماد هذا التعديل.

## 1) نسخ الملف
انسخ `collectibles_bridge.py` إلى مجلد البوت: `/root/telegram-bot/collectibles_bridge.py`

## 2) إضافة واحدة داخل `_post_init` في `bot.py`
أضِف هذه الكتلة **داخل** دالة `async def _post_init(app):` (مثلاً قبل سطر
`# إرسال DB لقناة السجل`)، وهي معزولة بـ try/except فلا تُسقط البوت أبداً:

```python
    # ── جسر عملة المقتنيات (خدمة MRKT المستقلة) ──
    try:
        import os
        import db as _db
        from collectibles_bridge import start_currency_server

        def _currency_resolver(uid: int):
            u = _db.get_user(uid)
            if not u:
                return None
            cur = u.get("currency") or "USD"
            return (cur, float(_db.get_rate(cur)))

        start_currency_server(
            _currency_resolver,
            host=os.getenv("BRIDGE_HOST", "127.0.0.1"),
            port=int(os.getenv("BRIDGE_PORT", "8090")),
            secret=os.getenv("INTERNAL_SHARED_SECRET", ""),
        )
    except Exception as _e:
        logging.warning("تعذّر تشغيل جسر عملة المقتنيات: %s", _e)
```

## 3) البيئة (.env للبوت)
```
INTERNAL_SHARED_SECRET=<نفس القيمة في خدمة المقتنيات>
BRIDGE_PORT=8090
```

## لماذا هذا آمن
- **بلا تبعية جديدة:** يستخدم `http.server` القياسية فقط.
- **بلا مساس بأي منطق قائم:** إضافة فقط داخل `_post_init`، لا تعديل على handlers/مالية/DB.
- **قراءة فقط:** يستدعي `db.get_user`/`db.get_rate` الموجودتين — لا كتابة.
- **معزول:** خيط daemon منفصل + `try/except` شامل؛ أي فشل يُسجَّل ولا يُسقط البوت.
- **لا هجرة قاعدة بيانات.**

## التحقّق المطلوب منك على الـ VPS
```bash
python3 -c "import ast; ast.parse(open('bot.py').read()); print('bot.py parses OK')"
# بعد التشغيل:
curl -s -H "X-Internal-Secret: <secret>" http://127.0.0.1:8090/internal/user/<user_id>/currency
```
