"""
أساس المزوّدين (BaseProvider).

يوفّر تخزين الاسم/الإصدار المشترك؛ كل مِحوَل فعلي (مثل MRKT في Phase C)
يرث منه ويُنفّذ الدوال المجرّدة في ProviderPort.
"""
from __future__ import annotations

from app.ports.provider_port import ProviderPort


class BaseProvider(ProviderPort):
    """أساس مشترك خفيف للمزوّدين — لا يُنفّذ الدوال المجرّدة (تبقى على المِحوَل)."""

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version

    def __repr__(self) -> str:  # pragma: no cover - تمثيل مساعد فقط
        return f"<Provider {self.name}/{self.version}>"
