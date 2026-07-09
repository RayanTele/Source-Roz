"""
التحقق من initData الخاص بـ Telegram WebApp وتحليله.

يتبع مواصفة Telegram: المفتاح السرّي = HMAC_SHA256("WebAppData", bot_token)،
و data_check_string = كل الحقول (عدا hash) مرتّبة أبجدياً بصيغة key=value
مفصولة بأسطر. صحّة التوقيع = HMAC_SHA256(secret, check_string) == hash.

مستقل عن أي مزوّد مقتنيات — طبقة مصادقة عميل فقط.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional


class InvalidInitData(Exception):
    """initData غير صالح أو توقيعه لا يطابق."""


@dataclass
class InitDataPrincipal:
    user_id: int
    user: Dict[str, Any]
    auth_date: int


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> InitDataPrincipal:
    """يتحقق من initData ويعيد هوية المستخدم، أو يرفع InvalidInitData."""
    if not init_data or not bot_token:
        raise InvalidInitData("initData أو bot_token مفقود")

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InvalidInitData("لا يوجد حقل hash")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        raise InvalidInitData("توقيع hash لا يطابق")

    auth_date = int(data.get("auth_date", "0") or 0)
    if max_age_seconds is not None and auth_date > 0:
        current = now if now is not None else time.time()
        if current - auth_date > max_age_seconds:
            raise InvalidInitData("انتهت صلاحية initData (auth_date قديم)")

    try:
        user = json.loads(data.get("user", "{}"))
    except (ValueError, TypeError):
        user = {}
    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise InvalidInitData("لا يوجد معرّف مستخدم صالح")

    return InitDataPrincipal(user_id=user_id, user=user, auth_date=auth_date)
