from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

import config
import texts
from database import db, fmt_dt
from services.marketplace import ingest_deal
from services.logger import notify_payout
from services.ton import mask_wallet

log = logging.getLogger("panel")


def employee_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.PANEL_SECRET, salt="ural-employee")


def make_employee_token(tg_id: int) -> str:
    return employee_signer().dumps({"tg_id": tg_id})


def read_employee_token(token: str, max_age: int = 60 * 60 * 24 * 7) -> Optional[int]:
    try:
        data = employee_signer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    tg_id = data.get("tg_id")
    return int(tg_id) if tg_id else None


def create_app() -> FastAPI:
    app = FastAPI(title="Ural Team Panel", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(config.TEMPLATES))
    templates.env.filters["money"] = texts.money
    templates.env.filters["money4"] = texts.money4
    templates.env.filters["dt"] = fmt_dt
    templates.env.filters["pct"] = texts.pct
    templates.env.filters["mask"] = mask_wallet

    if config.STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(config.STATIC)), name="static")
    if config.ASSETS.exists():
        app.mount("/assets", StaticFiles(directory=str(config.ASSETS)), name="assets")

    def is_admin(request: Request) -> bool:
        return bool(request.session.get("admin"))

    def employee_id(request: Request) -> Optional[int]:
        value = request.session.get("employee_id")
        return int(value) if value else None

    def render(request: Request, name: str, **ctx):
        ctx.update(
            request=request,
            path=str(request.url.path),
            is_admin=is_admin(request),
            employee_id=employee_id(request),
        )
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if is_admin(request):
            return RedirectResponse("/", status_code=302)
        return render(request, "login.html", error=None)

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if password == config.ADMIN_PASSWORD:
            request.session.clear()
            request.session["admin"] = True
            return RedirectResponse("/", status_code=302)
        return render(request, "login.html", error="Неверный пароль")

    @app.get("/c/{token}")
    async def employee_enter(request: Request, token: str):
        tg_id = read_employee_token(token)
        if not tg_id:
            return RedirectResponse("/login", status_code=302)
        user = await db.user(tg_id)
        if not user or user.is_banned or user.is_archived:
            return RedirectResponse("/login", status_code=302)
        request.session.clear()
        request.session["employee_id"] = tg_id
        return RedirectResponse("/me", status_code=302)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    @app.post("/api/marketplace/deals")
    async def api_deals(
        request: Request,
        x_marketplace_secret: str = Header(default="", alias="X-Marketplace-Secret"),
    ):
        expected = config.settings.marketplace_secret
        if not expected or x_marketplace_secret != expected:
            return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
        external_id = str(body.get("id") or body.get("external_id") or "").strip()
        if not external_id:
            return JSONResponse({"ok": False, "error": "id required"}, status_code=400)
        from context import bot

        try:
            deal = await ingest_deal(
                bot,
                external_id=external_id,
                status=str(body.get("status") or "pending"),
                title=str(body.get("title") or ""),
                amount=float(body["amount"]) if body.get("amount") not in (None, "") else None,
                user_id=int(body["user_id"]) if body.get("user_id") else None,
                payload=body if isinstance(body, dict) else None,
            )
        except Exception as exc:
            log.exception("marketplace ingest failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, "id": deal.id, "status": deal.status}

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        open_prefixes = ("/static", "/assets", "/login", "/c/", "/api/marketplace")
        if path.startswith(open_prefixes) or path == "/login":
            return await call_next(request)
        if path.startswith("/me"):
            if employee_id(request) or is_admin(request):
                return await call_next(request)
            return RedirectResponse("/login", status_code=302)
        if not is_admin(request):
            return RedirectResponse("/login", status_code=302)
        return await call_next(request)

    @app.get("/me", response_class=HTMLResponse)
    async def me(request: Request):
        uid = employee_id(request)
        if is_admin(request) and request.query_params.get("as"):
            uid = int(request.query_params["as"])
        if not uid:
            return RedirectResponse("/", status_code=302)
        user = await db.user(uid)
        if not user:
            return RedirectResponse("/login", status_code=302)
        tasks = await db.tasks(assigned_to=uid)
        reports = await db.reports_for(uid)
        pending = await db.pending_withdraw_sum(uid)
        paid = await db.paid_sum(uid)
        return render(
            request,
            "employee.html",
            user=user,
            tasks=tasks,
            reports=reports,
            pending=pending,
            paid=paid,
        )

    @app.post("/me/payout")
    async def me_payout(request: Request, amount: str = Form(...)):
        uid = employee_id(request)
        if not uid:
            return RedirectResponse("/login", status_code=302)
        user = await db.user(uid)
        if not user or not (user.wallet or "").strip():
            return RedirectResponse("/me", status_code=302)
        try:
            value = float(amount.replace(",", ".").replace(" ", ""))
        except ValueError:
            return RedirectResponse("/me", status_code=302)
        min_amount = float(await db.setting("min_withdraw", "0.1"))
        if value < min_amount:
            return RedirectResponse("/me", status_code=302)
        try:
            wd_id = await db.create_withdraw(uid, value, user.wallet.strip())
        except ValueError:
            return RedirectResponse("/me", status_code=302)
        from context import bot
        from services.logger import notify_admins
        import keyboards as kb

        uname = f"@{user.username}" if user.username else (user.first_name or str(uid))
        if bot:
            await notify_admins(
                bot,
                f"📤 Заявка на выплату #{wd_id}\n👤 {uname}\n🆔 <code>{uid}</code>\n"
                f"💎 {texts.money(value)} TON\n📍 <code>{user.wallet}</code>",
                reply_markup=kb.admin_wd_kb(wd_id),
            )
        return RedirectResponse("/me", status_code=302)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        stats = await db.stats()
        pending = await db.withdrawals("pending")
        deals = await db.deals(8)
        return render(request, "dashboard.html", stats=stats, pending=pending[:8], deals=deals)

    @app.get("/users", response_class=HTMLResponse)
    async def users(request: Request, q: str = "", archived: str = ""):
        rows = await db.users(q=q, archived=(archived == "1"))
        return render(request, "users.html", users=rows, q=q, archived=archived)

    @app.get("/users/{tg_id}", response_class=HTMLResponse)
    async def user_card(request: Request, tg_id: int):
        user = await db.user(tg_id)
        if not user:
            return RedirectResponse("/users", status_code=302)
        refs = await db.refs_count(tg_id)
        logs = await db.logs(limit=20, user_id=tg_id)
        valuations = await db.valuations(limit=10, user_id=tg_id)
        return render(request, "user.html", user=user, refs=refs, logs=logs, valuations=valuations)

    @app.post("/users/{tg_id}/balance")
    async def user_balance(tg_id: int, amount: str = Form(...)):
        try:
            value = float(amount.replace(",", ".").replace(" ", ""))
        except ValueError:
            return RedirectResponse(f"/users/{tg_id}", status_code=302)
        await db.set_balance(tg_id, value, as_earn=value > 0)
        try:
            from context import bot

            sign = "+" if value >= 0 else ""
            if bot:
                await bot.send_message(
                    tg_id,
                    f"💎 Баланс изменён на <b>{sign}{texts.money(value)} TON</b>",
                )
        except Exception:
            log.warning("Could not notify user %s", tg_id)
        return RedirectResponse(f"/users/{tg_id}", status_code=302)

    @app.post("/users/{tg_id}/status")
    async def user_status(tg_id: int, status: str = Form(...)):
        await db.set_status(tg_id, status.strip() or "Сотрудник")
        return RedirectResponse(f"/users/{tg_id}", status_code=302)

    @app.post("/users/{tg_id}/ban")
    async def user_ban(tg_id: int, banned: int = Form(...)):
        await db.set_banned(tg_id, bool(banned))
        return RedirectResponse(f"/users/{tg_id}", status_code=302)

    @app.post("/users/{tg_id}/archive")
    async def user_archive(tg_id: int, archived: int = Form(...)):
        await db.set_archived(tg_id, bool(archived))
        return RedirectResponse(f"/users/{tg_id}", status_code=302)

    @app.post("/users/{tg_id}/profile")
    async def user_profile(
        tg_id: int,
        tag: str = Form("Аноним"),
        mentor_percent: str = Form("0"),
        payout_percent: str = Form("70"),
        wallet: str = Form(""),
    ):
        try:
            mp = float(mentor_percent.replace(",", "."))
            pp = float(payout_percent.replace(",", "."))
        except ValueError:
            return RedirectResponse(f"/users/{tg_id}", status_code=302)
        await db.set_tag(tg_id, tag.strip().lstrip("#") or "Аноним")
        await db.set_percents(tg_id, mp, pp, note="web panel")
        await db.set_wallet(tg_id, wallet.strip())
        return RedirectResponse(f"/users/{tg_id}", status_code=302)

    @app.get("/withdrawals", response_class=HTMLResponse)
    async def withdrawals(request: Request, status: str = ""):
        rows = await db.withdrawals(status or None)
        return render(request, "withdrawals.html", items=rows, status=status)

    @app.post("/withdrawals/{wd_id}/decide")
    async def decide_wd(wd_id: int, action: str = Form(...), tx_hash: str = Form("")):
        row = await db.finish_withdraw(wd_id, approve=(action == "ok"), tx_hash=tx_hash.strip() or None)
        if row:
            try:
                from context import bot

                if action == "ok":
                    text = (
                        f"✅ Ваша заявка #{wd_id} одобрена.\n"
                        f"Выплачено: <b>{texts.money(row.amount)} TON</b>"
                    )
                    status = "ОДОБРЕНО"
                else:
                    text = (
                        f"❌ Заявка #{wd_id} отклонена.\n"
                        f"{texts.money(row.amount)} TON возвращены на баланс."
                    )
                    status = "ОТКЛОНЕНО"
                if bot:
                    await bot.send_message(row.user_id, text)
                    uname = f"@{row.username}" if row.username else (row.first_name or str(row.user_id))
                    await notify_payout(
                        bot,
                        user_id=row.user_id,
                        username=uname,
                        amount=texts.money(row.amount),
                        status=status,
                        wallet=row.address,
                        payout_id=wd_id,
                    )
            except Exception:
                log.warning("Could not notify withdraw %s", wd_id)
        return RedirectResponse("/withdrawals?status=pending", status_code=302)

    @app.get("/mentors", response_class=HTMLResponse)
    async def mentors(request: Request):
        return render(request, "mentors.html", mentors=await db.mentors())

    @app.post("/mentors")
    async def add_mentor(
        name: str = Form(...),
        username: str = Form(...),
        role: str = Form("Наставник"),
        sort_order: int = Form(0),
    ):
        await db.add_mentor(name.strip(), username.strip(), role.strip() or "Наставник", sort_order)
        return RedirectResponse("/mentors", status_code=302)

    @app.post("/mentors/{mid}/delete")
    async def del_mentor(mid: int):
        await db.delete_mentor(mid)
        return RedirectResponse("/mentors", status_code=302)

    @app.get("/payouts", response_class=HTMLResponse)
    async def payouts(request: Request):
        return render(request, "payouts.html", items=await db.payouts(100))

    @app.post("/payouts")
    async def add_payout(username: str = Form(...), amount: str = Form(...)):
        try:
            value = float(amount.replace(",", ".").replace(" ", ""))
        except ValueError:
            return RedirectResponse("/payouts", status_code=302)
        uname = username.strip()
        if uname and not uname.startswith("@"):
            uname = "@" + uname
        await db.add_payout(uname, value)
        return RedirectResponse("/payouts", status_code=302)

    @app.post("/payouts/{pid}/delete")
    async def del_payout(pid: int):
        await db.delete_payout(pid)
        return RedirectResponse("/payouts", status_code=302)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return render(request, "settings.html", s=await db.settings_all())

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        keys = (
            "welcome",
            "about",
            "channel",
            "chat",
            "min_withdraw",
            "network",
            "deposit_address",
            "deposit_hint",
        )
        for key in keys:
            if key in form:
                await db.set_setting(key, str(form[key]))
        return RedirectResponse("/settings", status_code=302)

    @app.get("/broadcast", response_class=HTMLResponse)
    async def broadcast_page(request: Request, result: str = ""):
        return render(request, "broadcast.html", result=result)

    @app.post("/broadcast")
    async def broadcast(text: str = Form(...)):
        from context import bot

        ids = await db.all_user_ids()
        ok = fail = 0
        if bot:
            for uid in ids:
                try:
                    await bot.send_message(uid, text)
                    ok += 1
                except Exception:
                    fail += 1
        return RedirectResponse(f"/broadcast?result=отправлено {ok}, ошибок {fail}", status_code=302)

    @app.get("/deals", response_class=HTMLResponse)
    async def deals_page(request: Request):
        return render(request, "deals.html", items=await db.deals(200))

    @app.post("/deals")
    async def deals_add(
        external_id: str = Form(...),
        status: str = Form("pending"),
        title: str = Form(""),
        amount: str = Form(""),
        user_id: str = Form(""),
    ):
        from context import bot

        amt = None
        uid = None
        try:
            if amount.strip():
                amt = float(amount.replace(",", "."))
            if user_id.strip():
                uid = int(user_id)
        except ValueError:
            return RedirectResponse("/deals", status_code=302)
        await ingest_deal(
            bot,
            external_id=external_id.strip(),
            status=status,
            title=title.strip(),
            amount=amt,
            user_id=uid,
        )
        return RedirectResponse("/deals", status_code=302)

    @app.get("/valuations", response_class=HTMLResponse)
    async def valuations_page(request: Request):
        return render(request, "valuations.html", items=await db.valuations(200))

    @app.post("/valuations/{vid}/credit")
    async def valuation_credit(vid: int):
        row = await db.credit_valuation(vid)
        if row and row.credited:
            try:
                from context import bot

                if bot:
                    await bot.send_message(
                        row.user_id,
                        f"💎 На баланс зачислена оценка NFT #{row.id}: "
                        f"<b>{texts.money4(row.employee_share)} TON</b>",
                    )
            except Exception:
                log.warning("Could not notify valuation %s", vid)
        return RedirectResponse("/valuations", status_code=302)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return render(request, "logs.html", items=await db.logs(200))

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_page(request: Request):
        return render(request, "tasks.html", items=await db.tasks())

    @app.post("/tasks")
    async def tasks_add(
        title: str = Form(...),
        description: str = Form(""),
        assigned_to: str = Form(""),
    ):
        uid = int(assigned_to) if assigned_to.strip().isdigit() else None
        task = await db.add_task(title.strip(), description.strip(), uid)
        if uid:
            try:
                from context import bot

                if bot:
                    await bot.send_message(
                        uid,
                        f"📋 Новая задача #{task.id}: <b>{title}</b>\n{description}",
                    )
            except Exception:
                log.warning("Could not notify task assignee")
        return RedirectResponse("/tasks", status_code=302)

    @app.post("/tasks/{tid}/status")
    async def task_status(tid: int, status: str = Form(...)):
        await db.set_task_status(tid, status)
        return RedirectResponse("/tasks", status_code=302)

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.PANEL_SECRET,
        session_cookie="ural_session",
        same_site="lax",
        https_only=False,
    )
    return app
