"""HTTP-приём событий от внешних ботов (gg_sel).

Bothost проксирует публичный домен бота на порт из переменной PORT, поэтому
слушаем 0.0.0.0:PORT. Если PORT не задан — сервер просто не поднимается и бот
работает как раньше. Доступ по секрету MARKETPLACE_SECRET.
"""

from __future__ import annotations

import hmac
import logging

from aiohttp import web

from config import settings
from services.marketplace import ingest_external_deal

log = logging.getLogger("api")


def _secret_ok(request: web.Request) -> bool:
    expected = (settings.marketplace_secret or "").strip()
    if not expected:
        return False
    got = (
        request.headers.get("X-Api-Secret")
        or request.headers.get("X-Marketplace-Secret")
        or request.headers.get("Authorization", "").replace("Bearer", "", 1).strip()
        or request.query.get("secret", "")
    ).strip()
    return bool(got) and hmac.compare_digest(got, expected)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "ural-panel"})


async def deals(request: web.Request) -> web.Response:
    if not _secret_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "object_required"}, status=400)

    import context

    try:
        deal = await ingest_external_deal(context.bot, body)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        log.exception("Не смог принять сделку %s", body.get("id") or body.get("code"))
        return web.json_response({"ok": False, "error": "internal"}, status=500)
    return web.json_response({"ok": True, "id": deal.id, "status": deal.status})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/api/deals", deals)
    app.router.add_post("/api/marketplace/deals", deals)
    return app


async def start_api() -> web.AppRunner | None:
    port = int(settings.api_port or 0)
    if port <= 0:
        log.info("PORT не задан — HTTP-приём сделок выключен")
        return None
    if not settings.marketplace_secret.strip():
        log.warning("MARKETPLACE_SECRET пуст — приём сделок будет отвечать 403")
    runner = web.AppRunner(build_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("HTTP-приём сделок слушает 0.0.0.0:%s", port)
    return runner
