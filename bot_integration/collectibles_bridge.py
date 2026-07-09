"""
جسر عملة المقتنيات (يُنشر داخل مجلد البوت: /root/telegram-bot/).

خادم HTTP داخلي بسيط بالمكتبة القياسية فقط (بلا أي تبعية جديدة على البوت)،
يعمل في خيط منفصل (daemon) فلا يتداخل مع حلقة asyncio الخاصة بـ PTB.

يخدم فقط:  GET /internal/user/{id}/currency
الرد:      {"currency": "<code>", "rate": <float>}
الحماية:   ترويسة X-Internal-Secret يجب أن تطابق السرّ المشترك.

القراءة فقط — لا يكتب أي شيء في قاعدة البيانات. resolver يُحقَن من البوت
(يقرأ عملة المستخدم وسعر صرفها عبر دوال db القائمة)، فيبقى هذا الملف
مستقلاً وقابلاً للاختبار وحده.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple

# resolver(user_id:int) -> (currency:str, rate:float) | None
Resolver = Callable[[int], Optional[Tuple[str, float]]]

_PATH_RE = re.compile(r"^/internal/user/(\d+)/currency$")


def make_handler(resolver: Resolver, secret: str):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # كتم سجلّ http.server الافتراضي
            pass

        def _json(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if secret and self.headers.get("X-Internal-Secret") != secret:
                return self._json(401, {"error": "unauthorized"})
            m = _PATH_RE.match(self.path.split("?", 1)[0])
            if not m:
                return self._json(404, {"error": "not_found"})
            user_id = int(m.group(1))
            try:
                result = resolver(user_id)
            except Exception:
                return self._json(500, {"error": "resolver_error"})
            if not result:
                return self._json(404, {"error": "user_not_found"})
            currency, rate = result
            return self._json(200, {"currency": currency, "rate": float(rate)})

    return _Handler


def start_currency_server(
    resolver: Resolver,
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
    secret: str = "",
) -> ThreadingHTTPServer:
    """يبدأ الخادم في خيط daemon ويعيد كائن الخادم (للإيقاف عند الحاجة)."""
    server = ThreadingHTTPServer((host, port), make_handler(resolver, secret))
    t = threading.Thread(target=server.serve_forever, name="collectibles-currency", daemon=True)
    t.start()
    logging.info("✅ جسر عملة المقتنيات يعمل على %s:%s", host, port)
    return server
