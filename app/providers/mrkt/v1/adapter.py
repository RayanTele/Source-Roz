"""
مِحوَل MRKT v1 (MrktProvider) — يُنفّذ ProviderPort ويربط العميل بالمِحوَل.

يبقى ضمن معمارية الإضافات: يُسجَّل باسم "mrkt" عبر register() في حزمة mrkt.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from app.core.metrics import Metrics, resolve
from app.core.models import CanonicalCollectible
from app.providers.base import BaseProvider
from app.providers.mrkt.v1 import mapper
from app.providers.mrkt.v1.client import MrktClient
from app.providers.mrkt.v1.token_manager import TokenManager


class MrktProvider(BaseProvider):
    """مزوّد MRKT للقراءة (مزامنة الكتالوج فقط)."""

    def __init__(self, client: MrktClient, token_manager: TokenManager, metrics=None):
        super().__init__(name="mrkt", version="v1")
        self._client = client
        self._tokens = token_manager
        self._m = resolve(metrics)

    async def authenticate(self) -> str:
        return await self._tokens.get_token()

    async def fetch_listings(self, cursor: str = "") -> Tuple[List[Dict[str, Any]], str]:
        return await self._client.fetch_listings(cursor=cursor)

    async def fetch_by_ids(self, ids: Sequence[str]) -> List[Dict[str, Any]]:
        return await self._client.fetch_by_ids(ids)

    async def fetch_one(self, item_id: str) -> Dict[str, Any]:
        return await self._client.fetch_one(item_id)

    def normalize(self, raw: Dict[str, Any]) -> CanonicalCollectible:
        return mapper.map_gift(raw)

    def health(self) -> dict:
        """لقطة صحة مبدئية للمزوّد (تُستكمَل في Phase E)."""
        return {
            "provider": self.name,
            "version": self.version,
            "has_token": self._tokens.has_token,
            "breaker": self._client.breaker_snapshot(),
            "metrics": self._m.snapshot(),
        }

    async def aclose(self) -> None:
        """إغلاق رشيق للموارد الأساسية."""
        await self._client.aclose()
