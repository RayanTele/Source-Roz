#!/usr/bin/env python3
"""
مِسبار MRKT — يحسم سبب 401 تجريبياً، لا تخميناً.

يرسل **نفس** الطلب المرجعي بالضبط (نفس URL، نفس الجسم، نفس الترويسات، نفس
التوكن) عبر ناقلَين مختلفين:
    1) aiohttp     (ما نستخدمه الآن)
    2) curl_cffi   (ما يستخدمه المرجع الرسمي — بصمة TLS لمتصفح)

إن نجح curl_cffi وفشل aiohttp بـ 401/403 → السبب بصمة TLS لدى Cloudflare.
إن فشل الاثنان → السبب في التوكن أو الحساب، لا في الناقل.

التشغيل على الـ VPS:
    cd /root/collectibles-service
    # (أ) بتوكن جاهز:
    MRKT_TOKEN=<التوكن> python3 tools/mrkt_probe.py
    # (ب) أو بتوليد التوكن عبر Telethon من .env:
    python3 tools/mrkt_probe.py --auth

لا يكتب شيئاً، لا يمسّ قاعدة بيانات، للقراءة فقط.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.getenv("MRKT_API_URL", "https://api.tgmrkt.io")
SALING = f"{BASE}/api/v1/gifts/saling"

# جسم الطلب مطابق حرفياً للمرجع
REF_BODY = {
    "collectionNames": [],
    "modelNames": [],
    "backdropNames": [],
    "symbolNames": [],
    "ordering": "Price",
    "lowToHigh": True,
    "maxPrice": None,
    "minPrice": None,
    "mintable": None,
    "number": None,
    "count": 20,
    "cursor": "",
    "query": None,
    "promotedFirst": False,
}


def mask(t: str) -> str:
    return f"{t[:6]}…(len={len(t)})" if t else "<فارغ>"


def ref_headers(token: str) -> dict:
    return {"Authorization": token, "Referer": "https://cdn.tgmrkt.io/"}


async def get_token_via_telethon() -> str:
    from app.config import load_settings
    from app.infra.http_client import AiohttpClient
    from app.providers.mrkt.v1.token_manager import (
        TelethonInitDataProvider,
        TokenManager,
    )

    s = load_settings()
    http = AiohttpClient(timeout=s.mrkt_timeout)
    idp = TelethonInitDataProvider(
        api_id=s.tg_api_id, api_hash=s.tg_api_hash, session=s.tg_assistant_session,
        bot_username=s.mrkt_bot_username, app_short_name=s.mrkt_app_short_name,
    )
    tm = TokenManager(http, s.mrkt_base_url, idp, app_id=s.mrkt_auth_app_id)
    try:
        return await tm.get_token()
    finally:
        await http.close()


async def try_aiohttp(token: str):
    try:
        import aiohttp
    except ImportError:
        return ("aiohttp", "غير مثبّت", "")
    async with aiohttp.ClientSession() as s:
        async with s.post(SALING, json=REF_BODY, headers=ref_headers(token)) as r:
            body = (await r.read())[:300].decode("utf-8", "replace")
            return ("aiohttp", r.status, body)


async def try_curl_cffi(token: str, impersonate: str = "chrome"):
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return (f"curl_cffi({impersonate})", "غير مثبّت — pip install curl_cffi", "")
    async with AsyncSession(impersonate=impersonate) as s:
        r = await s.post(SALING, json=REF_BODY, headers=ref_headers(token))
        body = (r.content or b"")[:300].decode("utf-8", "replace")
        return (f"curl_cffi({impersonate})", r.status_code, body)


async def main() -> int:
    token = os.getenv("MRKT_TOKEN", "")
    if "--auth" in sys.argv or not token:
        print("… توليد التوكن عبر Telethon من .env")
        token = await get_token_via_telethon()
    print(f"التوكن: {mask(token)}")
    print(f"الهدف : POST {SALING}")
    print(f"الجسم : {json.dumps(REF_BODY, ensure_ascii=False)}")
    print(f"الترويسات: Authorization=<masked>, Referer=https://cdn.tgmrkt.io/")
    print("=" * 68)

    results = []
    for coro in (try_aiohttp(token), try_curl_cffi(token, "chrome"),
                 try_curl_cffi(token, "safari")):
        try:
            results.append(await coro)
        except Exception as exc:
            results.append(("?", f"استثناء: {type(exc).__name__}: {exc}", ""))

    for name, status, body in results:
        mark = "✅" if status == 200 else "❌"
        print(f"{mark} {name:24} → {status}")
        if body:
            print(f"     الجسم: {body[:160]}")

    print("=" * 68)
    codes = {n: s for n, s, _ in results}
    ok_curl = any(s == 200 for n, s, _ in results if n.startswith("curl_cffi"))
    ok_aio = codes.get("aiohttp") == 200
    if ok_curl and not ok_aio:
        print("الاستنتاج: السبب بصمة TLS/HTTP لدى Cloudflare.")
        print("الحل    : HTTP_CLIENT=curl_cffi في .env  (+ pip install curl_cffi)")
    elif ok_aio:
        print("الاستنتاج: aiohttp يعمل — المشكلة إذن في مكان آخر (الجسم/التوكن في الخدمة).")
    else:
        print("الاستنتاج: كلا الناقلين فشل → المشكلة في التوكن/الحساب لا في الناقل.")
        print("           تحقّق: هل الحساب المساعد دخل تطبيق MRKT مرة واحدة يدوياً؟")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
