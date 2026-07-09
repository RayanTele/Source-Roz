"""
تجميع الخدمة (Bootstrap) — يبني الرسم الكامل بمقاييس وعميل HTTP مشتركين.

يجمع: المزوّد (MRKT) + GramPrice + MediaService + الكاش + SyncEngine + Health
+ (Phase F) العملة/التسعير/الكتالوج + الواجهة العامة. عميل HTTP واحد مشترك
يُغلَق عند الإيقاف الرشيق.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings, load_settings
from app.core.catalog_service import CatalogService
from app.core.currency_cache import CurrencyCache
from app.core.health import HealthService
from app.core.media_service import MediaService
from app.core.metrics import Metrics
from app.core.pricing_service import PricingService
from app.core.sync_engine import SyncEngine
from app.infra.bot_client import BotCurrencyProvider
from app.infra.cache_sqlite import SQLiteCacheRepository
from app.infra.gram_price import GramPrice
from app.infra.http_client import AiohttpClient
from app.infra.media_fs import FilesystemMediaStore
from app.infra.public_api import PublicApi
from app.providers.mrkt import build_mrkt_provider


@dataclass
class ServiceContext:
    settings: Settings
    metrics: Metrics
    http: AiohttpClient
    provider: Any
    gram: GramPrice
    media: MediaService
    repo: SQLiteCacheRepository
    sync: SyncEngine
    health: HealthService
    pricing: PricingService
    catalog: CatalogService
    public_api: PublicApi

    async def aclose(self) -> None:
        try:
            await self.http.close()
        except Exception:
            pass


def build_service(settings: Settings = None) -> ServiceContext:
    """يبني سياق الخدمة الكامل (بلا تشغيل شبكي — الإقلاع فقط)."""
    s = settings or load_settings()
    metrics = Metrics()
    http = AiohttpClient(timeout=s.mrkt_timeout)

    provider = build_mrkt_provider(s, metrics, http)
    gram = GramPrice(
        http, base_url=s.binance_base_url, symbol=s.gram_symbol,
        ttl=s.gram_price_ttl, metrics=metrics,
    )
    media_store = FilesystemMediaStore(s.media_dir)
    media = MediaService(
        media_store, http, cdn_base_url=s.mrkt_cdn_url,
        media_base_url=s.media_base_url, metrics=metrics,
    )
    repo = SQLiteCacheRepository(s.db_path)
    repo.init_schema()
    sync = SyncEngine(provider, repo, media_service=media, metrics=metrics)
    health = HealthService(provider, repo, media, gram, metrics)

    # ── Phase F: العملة → التسعير → الكتالوج → الواجهة العامة ──
    currency_provider = BotCurrencyProvider(
        http, s.bot_internal_url, s.internal_shared_secret,
        default_currency=s.default_currency, cache_repo=repo,
    )
    currency_cache = CurrencyCache(
        currency_provider, ttl=s.currency_cache_ttl,
        default_currency=s.default_currency, metrics=metrics,
    )
    pricing = PricingService(
        repo, gram, currency_cache,
        default_commission_usd=s.default_commission_usd,
        default_currency=s.default_currency, metrics=metrics,
    )
    catalog = CatalogService(repo, pricing, provider_name=getattr(provider, "name", "mrkt"))
    public_api = PublicApi(catalog, s.storeroz_bot_token)

    return ServiceContext(
        settings=s, metrics=metrics, http=http, provider=provider,
        gram=gram, media=media, repo=repo, sync=sync, health=health,
        pricing=pricing, catalog=catalog, public_api=public_api,
    )
