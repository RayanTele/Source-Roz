"""
سجل المقاييس (Metrics) — عدّادات/مقاييس لحظية/مؤقّتات، آمن عبر الخيوط.

يغطّي: تجديدات التوكن، إعادات المحاولة، حالة قاطع الدائرة، مدة المزامنة،
إصابات/إخفاقات الكاش، وزمن استجابة المزوّد. بلا واجهة عرض (تُضاف لاحقاً)،
لكن البنية تدعمها من الآن. NullMetrics لعدم الحاجة للتغيير في الاختبارات.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class Metrics:
    """سجل مقاييس في الذاكرة."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, float] = {}
        self._gauges: Dict[str, float] = {}
        self._timers: Dict[str, Dict[str, Any]] = {}

    def inc(self, name: str, n: float = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            t = self._timers.setdefault(
                name, {"count": 0, "sum": 0.0, "min": None, "max": None, "last": None}
            )
            t["count"] += 1
            t["sum"] += value
            t["last"] = value
            t["min"] = value if t["min"] is None else min(t["min"], value)
            t["max"] = value if t["max"] is None else max(t["max"], value)

    def timer(self, name: str) -> "_Timer":
        return _Timer(self, name)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers": {k: dict(v) for k, v in self._timers.items()},
            }


class _Timer:
    """مؤقّت سياقي يقيس المدة ويسجّلها في observe."""

    def __init__(self, metrics: Metrics, name: str):
        self._metrics = metrics
        self._name = name
        self._start = 0.0

    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._metrics.observe(self._name, time.perf_counter() - self._start)


class NullMetrics(Metrics):
    """تنفيذ لا-عمليّ (no-op) للاستخدام الافتراضي وفي الاختبارات."""

    def inc(self, name: str, n: float = 1) -> None:  # noqa: D401
        pass

    def gauge(self, name: str, value: float) -> None:
        pass

    def observe(self, name: str, value: float) -> None:
        pass

    def snapshot(self) -> Dict[str, Any]:
        return {"counters": {}, "gauges": {}, "timers": {}}


# مقياس افتراضي لا-عمليّ (تُحقَن نسخة حقيقية إنتاجياً)
def resolve(metrics: Optional[Metrics]) -> Metrics:
    return metrics if metrics is not None else NullMetrics()


# رموز حالة القاطع للـ gauge
CB_STATE_CODE = {"closed": 0, "half_open": 1, "open": 2}
