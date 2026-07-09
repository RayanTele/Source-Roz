"""
نقطة تشغيل خدمة المقتنيات (main).

تُشغّل:
  - جدول مزامنة دوري (SyncEngine.run_once كل SYNC_INTERVAL_SECONDS، بأول جولة فورية).
  - خادم HTTP للصحة/المقاييس/الوسائط (داخلي).
إيقاف رشيق عند SIGINT/SIGTERM: إلغاء المجدول، إيقاف الخادم، إغلاق HTTP.

التشغيل:  python -m app.main
"""
from __future__ import annotations

import asyncio
import signal

from app.bootstrap import build_service
from app.core.logging_setup import get_logger, setup_logging
from app.infra.web_server import WebServer, build_app

_log = get_logger("main")


async def _scheduler(ctx, interval: float, stop: asyncio.Event) -> None:
    """حلقة مزامنة دورية معزولة (لا تُسقط الخدمة عند الفشل)."""
    first = True
    while not stop.is_set():
        try:
            result = await ctx.sync.run_once()
            if not result.ok and result.error:
                _log.warning("scheduled sync reported: %s", result.error)
        except Exception as exc:  # حماية إضافية
            _log.error("scheduler iteration crashed: %s", exc)
        if first:
            first = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run() -> None:
    settings = None
    ctx = build_service(settings)
    setup_logging(ctx.settings.log_level)
    _log.info("collectibles service starting…")

    web = WebServer(
        build_app(ctx.health, ctx.media, ctx.public_api),
        ctx.settings.service_host,
        ctx.settings.service_port,
    )
    await web.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # بعض المنصات
            pass

    sync_task = asyncio.create_task(
        _scheduler(ctx, float(getattr(ctx.settings, "sync_interval", 300.0)), stop)
    )

    await stop.wait()
    _log.info("shutdown signal received; stopping…")
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass
    await web.stop()
    await ctx.aclose()
    _log.info("collectibles service stopped cleanly")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
