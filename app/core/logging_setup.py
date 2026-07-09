"""
تهيئة السجلّات المهيكلة (Structured Logging) مع تتبّع request_id والمزوّد.

يستخدم contextvars لحمل المعرّفات عبر الاستدعاءات غير المتزامنة، وفلتر
يحقنها في كل سجل. request_context مدير سياق يضبطها ويعيدها تلقائياً.
"""
from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from typing import List, Optional, Tuple

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
provider_var: contextvars.ContextVar[str] = contextvars.ContextVar("provider", default="-")

_ROOT_NAME = "collectibles"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.provider = provider_var.get()
        return True


def setup_logging(level: str = "INFO", stream=None) -> logging.Logger:
    """يهيّئ جذر تسجيل الخدمة (idempotent)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(provider)s] [rid=%(request_id)s] %(name)s: %(message)s"
        )
    )
    handler.addFilter(_ContextFilter())
    root = logging.getLogger(_ROOT_NAME)
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    """يعيد مسجّلاً فرعياً تحت جذر الخدمة."""
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


class request_context:
    """مدير سياق يضبط request_id/provider ويعيدهما عند الخروج."""

    def __init__(self, provider: Optional[str] = None, request_id: Optional[str] = None):
        self.provider = provider
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self._tokens: List[Tuple[contextvars.ContextVar, contextvars.Token]] = []

    def __enter__(self) -> "request_context":
        self._tokens.append((request_id_var, request_id_var.set(self.request_id)))
        if self.provider is not None:
            self._tokens.append((provider_var, provider_var.set(self.provider)))
        return self

    def __exit__(self, *exc) -> None:
        for var, token in reversed(self._tokens):
            var.reset(token)
        self._tokens.clear()
