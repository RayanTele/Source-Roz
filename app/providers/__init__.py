"""
سجل المزوّدين + محمّل الإضافات (Plugin Registry & Loader).

كل مزوّد حزمة فرعية داخل providers/ تُعرّف دالة register() تستدعي
register_provider(name, factory). يكتشفها load_providers() تلقائياً.

إضافة مزوّد مستقبلاً = إسقاط حزمة جديدة تُنفّذ ProviderPort + register()،
دون تغيير أي جزء آخر من النظام.

ملاحظة تسمية: أي providers/fragment/ مستقبلي هو مِحوَل مقتنيات منفصل تماماً
عن نظام Fragment (النجوم/المميز) القائم في البوت — لا علاقة بينهما.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Callable, Dict, List

from app.ports.provider_port import ProviderPort

# مصنع يعيد نسخة مزوّد جاهزة
ProviderFactory = Callable[[], ProviderPort]

_REGISTRY: Dict[str, ProviderFactory] = {}

# حزم داخلية ليست مزوّدات (تُتجاهَل عند الاكتشاف)
_SKIP = {"base"}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """يسجّل مصنع مزوّد تحت اسم فريد."""
    if not name or not callable(factory):
        raise ValueError("register_provider يتطلّب اسماً ومصنعاً صالحين")
    _REGISTRY[name] = factory


def get_provider(name: str) -> ProviderPort:
    """يعيد نسخة مزوّد مسجّل بالاسم، أو يرفع KeyError."""
    if name not in _REGISTRY:
        raise KeyError(f"مزوّد غير مسجّل: {name!r}")
    return _REGISTRY[name]()


def available_providers() -> List[str]:
    """أسماء المزوّدين المسجّلين حالياً."""
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """يفرّغ السجل (للاختبارات فقط)."""
    _REGISTRY.clear()


def load_providers() -> List[str]:
    """
    يكتشف الحزم الفرعية داخل providers/ ويستدعي register() لكل منها إن وُجدت.
    يعيد قائمة المزوّدين المسجّلين بعد التحميل. آمن حين لا توجد مزوّدات بعد.
    """
    for mod in pkgutil.iter_modules(__path__):
        if mod.name in _SKIP or not mod.ispkg:
            # نكتفي بالحزم الفرعية (المزوّدات حزم، لا وحدات مفردة)
            continue
        try:
            module = importlib.import_module(f"{__name__}.{mod.name}")
        except Exception:
            # فشل استيراد مزوّد لا يُسقط بقية النظام
            continue
        register = getattr(module, "register", None)
        if callable(register):
            try:
                register()
            except Exception:
                continue
    return available_providers()
