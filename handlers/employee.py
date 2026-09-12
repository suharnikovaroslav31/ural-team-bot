from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import context
import keyboards as kb
import texts
from database.crud import db
from database.models import TaskStatus, User
from services.logger import notify_admin_report, notify_admins
from services.nft_calc import NFTCalcError, evaluate
from services.ton import wallet_is_usable
from ui import edit_screen, restore_caption, show_home

log = logging.getLogger("employee")
router = Router(name="employee")

TAG_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_]{1,24}$")
TON_FRIENDLY = re.compile(r"^(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$")
TON_RAW = re.compile(r"^0:[a-fA-F0-9]{64}$")


class WithdrawSG(StatesGroup):
    amount = State()


class TagSG(StatesGroup):
    wait = State()


class WalletSG(StatesGroup):
    wait = State()


class ReportSG(StatesGroup):
    deal = State()
    screens = State()


class NFTSG(StatesGroup):
    url = State()


def is_ton_wallet(value: str) -> bool:
    addr = value.strip()
    return bool(TON_FRIENDLY.fullmatch(addr) or TON_RAW.fullmatch(addr))


async def load_user(tg_id: int, *, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None) -> Optional[User]:
    await db.upsert_user(
        tg_id,
        username,
        first_name,
        last_name,
        as_admin=config.is_admin(tg_id),
    )
    return await db.user(tg_id)


async def require_admin(event: Message | CallbackQuery) -> bool:
    user = event.from_user
    if user and config.is_admin(user.id):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=True)
    return False


async def guard(event: Message | CallbackQuery) -> Optional[User]:
    user = event.from_user
    if not user:
        return None
    row = await load_user(user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
    if not row:
        return None
    if row.is_banned:
        if isinstance(event, CallbackQuery):
            await event.answer("Доступ ограничен", show_alert=True)
        else:
            await event.answer("Доступ ограничен.")
        return None
    if row.is_archived:
        if isinstance(event, CallbackQuery):
            await event.answer("Профиль в архиве", show_alert=True)
        else:
            await event.answer("Профиль архивирован. Обратитесь к администратору.")
        return None
    return row


async def profile_caption(user_id: int) -> str:
    row = await db.user(user_id)
    mentor = await db.mentor_label(row.referrer_id if row else None)
    paid = await db.paid_sum(user_id)
    currency = await db.setting("currency", "TON")
    return texts.profile_text(row, mentor, paid, currency)


async def cabinet_caption(user_id: int) -> str:
    row = await db.user(user_id)
    tasks = [t for t in await db.tasks(assigned_to=user_id) if t.status != TaskStatus.done.value]
    reports = await db.reports_for(user_id, limit=50)
    pending = await db.pending_withdraw_sum(user_id)
    return texts.cabinet_text(row, len(tasks), len(reports), pending)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    if not message.from_user:
        return
    user = message.from_user
    ref = None
    if command.args and command.args.isdigit():
        ref = int(command.args)
    created = await db.upsert_user(
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        ref,
        as_admin=config.is_admin(user.id),
    )
    if context.bot:
        bound = await db.main_chat_id()
        if bound:
            try:
                member = await context.bot.get_chat_member(bound, user.id)
                if member.status in {"creator", "administrator", "member", "restricted"}:
                    await db.mark_in_chat(user.id, True)
            except TelegramBadRequest:
                pass
    row = await db.user(user.id)
    if row and row.is_banned:
        await message.answer("Доступ ограничен.")
        return
    if created and ref and ref != user.id and context.bot:
        try:
            await context.bot.send_message(
                ref,
                f"👥 Новый реферал: {texts.display_name({'username': user.username, 'first_name': user.first_name, 'last_name': user.last_name, 'tg_id': user.id})}",
            )
        except TelegramBadRequest:
            pass
    await show_home(message, new=True)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await guard(message):
        return
    await show_home(message, new=True)


@router.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await guard(call):
        return
    await call.answer()
    await show_home(call)


@router.callback_query(F.data == "cabinet")
async def cb_cabinet(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await require_admin(call) or not await guard(call):
        return
    await call.answer()
    await edit_screen(call, await cabinet_caption(call.from_user.id), kb.admin_kb())


@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not await guard(call):
        return
    await call.answer()
    await edit_screen(call, await profile_caption(call.from_user.id), kb.profile_kb())


@router.callback_query(F.data == "set_tag")
async def cb_set_tag(call: CallbackQuery, state: FSMContext) -> None:
    row = await guard(call)
    if not row:
        return
    await state.set_state(TagSG.wait)
    await call.answer()
    await edit_screen(call, texts.tag_setup_text(row.tag), kb.tag_kb())


@router.message(TagSG.wait)
async def tag_save(message: Message, state: FSMContext) -> None:
    if not await guard(message):
        return
    raw = (message.text or "").strip().lstrip("#")
    if not TAG_RE.fullmatch(raw):
        await message.answer("Тег: 1–24 символа, буквы, цифры и _")
        return
    await db.set_tag(message.from_user.id, raw)
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await restore_caption(message.chat.id, await profile_caption(message.from_user.id), kb.profile_kb())


@router.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery) -> None:
    if not await guard(call):
        return
    raw = await db.setting("about")
    channel = await db.setting("channel")
    chat = await db.setting("chat")
    await call.answer()
    await edit_screen(call, texts.about_text(raw, channel, chat), kb.about_kb(chat))


@router.callback_query(F.data == "wallet")
async def cb_wallet(call: CallbackQuery, state: FSMContext) -> None:
    if not await guard(call):
        return
    await state.set_state(WalletSG.wait)
    await call.answer()
    await edit_screen(call, texts.wallet_text(), kb.wallet_kb())


@router.message(WalletSG.wait)
async def wallet_save(message: Message, state: FSMContext) -> None:
    if not await guard(message):
        return
    address = (message.text or "").strip()
    if not is_ton_wallet(address):
        await message.answer("Это не TON-кошелек. Отправь адрес одним сообщением.")
        return
    ok, status = await wallet_is_usable(address)
    if not ok and "недоступен" not in status.lower():
        await message.answer(f"Адрес не прошёл проверку TON RPC: {escape(status)}")
        return
    await db.set_wallet(message.from_user.id, address)
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await restore_caption(message.chat.id, await profile_caption(message.from_user.id), kb.profile_kb())


@router.callback_query(F.data == "deposit")
async def cb_deposit(call: CallbackQuery) -> None:
    if not await guard(call):
        return
    address = await db.setting("deposit_address")
    hint = await db.setting("deposit_hint")
    network = await db.setting("network")
    await call.answer()
    await edit_screen(call, texts.deposit_text(address, hint, network), kb.deposit_kb(bool(address)))


@router.callback_query(F.data == "deposit_done")
async def cb_deposit_done(call: CallbackQuery) -> None:
    row = await guard(call)
    if not row or context.bot is None:
        return
    ticket = await db.add_deposit_ticket(call.from_user.id)
    user = call.from_user
    uname = f"@{user.username}" if user.username else user.full_name
    await notify_admins(
        context.bot,
        f"💳 Заявка на пополнение #{ticket}\n👤 {escape(uname)}\n🆔 <code>{user.id}</code>",
    )
    await call.answer("Заявка отправлена администратору", show_alert=True)


@router.callback_query(F.data == "mentors")
async def cb_mentors(call: CallbackQuery) -> None:
    if not await guard(call):
        return
    rows = await db.mentors()
    await call.answer()
    await edit_screen(call, texts.mentors_text(rows), kb.home_back_kb())


@router.callback_query(F.data == "leaderboard")
async def cb_leaderboard(call: CallbackQuery) -> None:
    if not await guard(call):
        return
    await call.answer()
    await edit_screen(call, texts.leaderboard_menu_text(), kb.leaderboard_kb())


@router.callback_query(F.data.startswith("lb:"))
async def cb_lb_period(call: CallbackQuery) -> None:
    if not await guard(call):
        return
    period = (call.data or "lb:all").split(":")[1]
    if period not in {"day", "week", "month", "all"}:
        period = "all"
    rows = await db.leaderboard(period)
    await call.answer()
    await edit_screen(call, texts.leaderboard_text(rows, period), kb.leaderboard_result_kb())


@router.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery) -> None:
    if not await require_admin(call) or not await guard(call):
        return
    rows = await db.tasks(assigned_to=call.from_user.id)
    active = [t for t in rows if t.status != TaskStatus.done.value]
    await call.answer()
    await edit_screen(call, texts.tasks_text(active), kb.cabinet_back_kb())


@router.callback_query(F.data == "reports")
async def cb_reports(call: CallbackQuery) -> None:
    if not await require_admin(call) or not await guard(call):
        return
    rows = await db.reports_for(call.from_user.id)
    await call.answer()
    await edit_screen(call, texts.reports_text(rows), kb.reports_kb())


@router.callback_query(F.data == "nft")
async def cb_nft(call: CallbackQuery, state: FSMContext) -> None:
    if not await require_admin(call) or not await guard(call):
        return
    await state.set_state(NFTSG.url)
    await call.answer()
    await edit_screen(call, texts.nft_prompt_text(), kb.cabinet_back_kb())


@router.message(NFTSG.url, F.text)
async def nft_url(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    row = await guard(message)
    if not row:
        return
    try:
        result = await evaluate(message.text or "", Decimal(str(row.payout_percent)))
    except NFTCalcError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        log.exception("nft evaluate failed")
        await message.answer("Не удалось получить котировку. Попробуйте позже.")
        return
    saved = await db.save_valuation(
        user_id=row.tg_id,
        url=result.url,
        collection=result.collection,
        item_address=result.item_address,
        floor_price=result.floor_price,
        payout_rate=result.payout_rate,
        employee_share=result.employee_share,
        total_value=result.total_value,
    )
    await db.log_tx(
        user_id=row.tg_id,
        kind="valuation",
        title=f"Оценка NFT #{saved.id}",
        amount=float(result.employee_share),
        payload=result.as_payload(),
    )
    await state.clear()
    if context.bot:
        await notify_admins(
            context.bot,
            texts.header("🖼 Новая оценка NFT")
            + f"#{saved.id} · {texts.display_name(row)}\n"
            + f"Floor {texts.money4(result.floor_price)} · доля {texts.money4(result.employee_share)} TON\n"
            + f"<code>/credit {saved.id}</code>",
        )
    await message.answer(texts.nft_result_text(result))
    await show_home(message, new=True)


@router.callback_query(F.data == "payouts")
async def cb_payouts(call: CallbackQuery, state: FSMContext) -> None:
    if not await guard(call):
        return
    await state.set_state(ReportSG.deal)
    await call.answer()
    await edit_screen(call, texts.payouts_deal_text(), kb.home_back_kb())


@router.message(ReportSG.deal, F.text)
async def report_deal(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    if not await guard(message):
        return
    deal = (message.text or "").strip()
    if not deal or len(deal) > 64 or "\n" in deal:
        await message.answer("Укажи номер сделки одним сообщением.")
        return
    await state.update_data(deal=deal, shots=[])
    await state.set_state(ReportSG.screens)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await restore_caption(message.chat.id, texts.payouts_screens_text(), kb.home_back_kb())


@router.message(ReportSG.deal)
async def report_deal_wrong(message: Message) -> None:
    await message.answer("📄 Укажи номер сделки для выплаты.")


@router.message(ReportSG.screens, F.photo)
async def report_shot(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    if not await guard(message):
        return
    fid = message.photo[-1].file_id
    gid = message.media_group_id
    if gid:
        key = f"{message.from_user.id}:{gid}"
        first = key not in context.album_buf
        context.album_buf.setdefault(key, []).append(fid)
        if not first:
            return
        await asyncio.sleep(1.2)
        shots = context.album_buf.pop(key, [])
        data = await state.get_data()
        await finish_report(message, state, data.get("deal", ""), shots)
        return

    data = await state.get_data()
    shots = list(data.get("shots") or [])
    shots.append(fid)
    await state.update_data(shots=shots)
    n = len(shots)
    if n < 2:
        await message.answer(f"Нужно ещё минимум {2 - n}.")
        return
    await asyncio.sleep(2.2)
    data = await state.get_data()
    if await state.get_state() != ReportSG.screens.state:
        return
    shots2 = list(data.get("shots") or [])
    if len(shots2) == n:
        await finish_report(message, state, data.get("deal", ""), shots2[:10])


@router.message(ReportSG.screens, F.document)
async def report_shot_doc(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    if not await guard(message):
        return
    doc = message.document
    if not doc or not (doc.mime_type or "").startswith("image/"):
        await message.answer("📋 Загрузи от 2 до 10 скриншотов.")
        return
    data = await state.get_data()
    shots = list(data.get("shots") or [])
    shots.append(doc.file_id)
    await state.update_data(shots=shots[:10])
    n = len(shots)
    if n < 2:
        await message.answer(f"Нужно ещё минимум {2 - n}.")
        return
    if n >= 10:
        await finish_report(message, state, data.get("deal", ""), shots[:10])
        return
    await asyncio.sleep(2.2)
    data = await state.get_data()
    if await state.get_state() != ReportSG.screens.state:
        return
    shots2 = list(data.get("shots") or [])
    if len(shots2) == n:
        await finish_report(message, state, data.get("deal", ""), shots2[:10])


@router.message(ReportSG.screens)
async def report_shot_wrong(message: Message) -> None:
    await message.answer("📋 Загрузи от 2 до 10 скриншотов.")


async def finish_report(message: Message, state: FSMContext, deal: str, shots: list[str]) -> None:
    unique: list[str] = []
    for fid in shots:
        if fid not in unique:
            unique.append(fid)
    if len(unique) < 2:
        await message.answer("Нужно от 2 до 10 скриншотов.")
        return
    unique = unique[:10]
    await state.clear()
    rid = await db.add_deal_payout(message.from_user.id, deal, unique)
    user = message.from_user
    uname = f"@{user.username}" if user.username else user.full_name
    caption = (
        f"📨 Отчёт #{rid}\n"
        f"📌 Задача: <code>{escape(deal)}</code>\n"
        f"👤 {escape(uname)}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🖼 Файлов: {len(unique)}"
    )
    if context.bot:
        await notify_admin_report(
            context.bot,
            caption=caption,
            file_ids=unique,
            reply_markup=kb.admin_report_kb(rid),
        )
    await message.answer("Заявка на выплату отправлена.")
    await show_home(message, new=True)


@router.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery, state: FSMContext) -> None:
    row = await guard(call)
    if not row:
        return
    min_amount = float(await db.setting("min_withdraw", "0.1"))
    await state.set_state(WithdrawSG.amount)
    await call.answer()
    await edit_screen(call, texts.withdraw_text(float(row.balance), min_amount), kb.home_back_kb())


@router.callback_query(F.data == "wd_cancel")
async def cb_wd_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Отменено")
    await show_home(call)


@router.message(WithdrawSG.amount)
async def wd_amount(message: Message, state: FSMContext) -> None:
    row = await guard(message)
    if not row:
        return
    min_amount = float(await db.setting("min_withdraw", "0.1"))
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        await message.answer("Введите число, например <code>25.5</code>")
        return
    if amount < min_amount:
        await message.answer(f"Минимум для вывода — {texts.fmt_min(min_amount)} TON")
        return
    if amount > float(row.balance):
        await message.answer(
            f"На балансе только {texts.money(float(row.balance))} TON. "
            "Сначала админ начислит выплату на баланс."
        )
        return
    wallet = (row.wallet or "").strip()
    if not wallet:
        await message.answer("Сначала укажи TON-кошелек в разделе «Кошелек»")
        await state.clear()
        await show_home(message, new=True)
        return
    await state.clear()
    try:
        wd_id = await db.create_withdraw(message.from_user.id, amount, wallet)
    except ValueError as exc:
        msg = "Недостаточно средств на балансе." if str(exc) == "insufficient" else "Не удалось создать заявку."
        await message.answer(msg)
        await show_home(message, new=True)
        return
    user = message.from_user
    uname = f"@{user.username}" if user.username else user.full_name
    from services.logger import notify_payout
    from services.ton_send import TonSendError, payouts_enabled, send_ton

    if await payouts_enabled():
        try:
            sent = await send_ton(wallet, amount, comment=f"payout #{wd_id}")
        except TonSendError as exc:
            if context.bot:
                await notify_admins(
                    context.bot,
                    f"⚠️ Автовыплата #{wd_id} не прошла: {escape(str(exc))}\n"
                    f"👤 {escape(uname)}\n"
                    f"💎 {texts.money(amount)} TON\n"
                    f"📍 <code>{escape(wallet)}</code>\n"
                    "Одобрите вручную или отклоните.",
                    reply_markup=kb.admin_wd_kb(wd_id),
                )
            await message.answer(
                f"Заявка <b>#{wd_id}</b> создана, но сеть TON не приняла перевод.\n"
                "Админ проверит и отправит вручную."
            )
            await show_home(message, new=True)
            return
        await db.finish_withdraw(wd_id, True, tx_hash=sent.tx_hash)
        if context.bot:
            await notify_payout(
                context.bot,
                user_id=user.id,
                username=uname,
                amount=texts.money(amount),
                status="ОТПРАВЛЕНО",
                wallet=wallet,
                payout_id=wd_id,
            )
            await notify_admins(
                context.bot,
                f"⚡ Автовыплата #{wd_id}\n"
                f"👤 {escape(uname)}\n"
                f"💎 {texts.money(amount)} TON\n"
                f"📍 <code>{escape(wallet)}</code>\n"
                f"tx: <code>{escape(sent.tx_hash)}</code>",
            )
        link = f"https://tonviewer.com/{wallet}"
        await message.answer(
            f"✅ Выплата <b>#{wd_id}</b> отправлена в TON.\n"
            f"{texts.money(amount)} TON → <code>{escape(wallet)}</code>\n"
            f"<a href=\"{link}\">Открыть в обозревателе</a>"
        )
        await show_home(message, new=True)
        return

    if context.bot:
        await notify_admins(
            context.bot,
            f"📤 Заявка на выплату #{wd_id}\n"
            f"👤 {escape(uname)}\n"
            f"🆔 <code>{user.id}</code>\n"
            f"💎 {texts.money(amount)} TON\n"
            f"📍 <code>{escape(wallet)}</code>",
            reply_markup=kb.admin_wd_kb(wd_id),
        )
    await message.answer(
        f"✅ Заявка <b>#{wd_id}</b> создана.\n"
        f"{texts.money(amount)} TON сняты с баланса и уйдут на кошелёк после отправки."
    )
    await show_home(message, new=True)


@router.message(StateFilter(None), F.text.regexp(r"^https?://"))
async def maybe_nft_link(message: Message) -> None:
    """Allow pasting a Getgems/Tonkeeper URL outside FSM."""
    if not message.from_user or not config.is_admin(message.from_user.id):
        return
    row = await db.user(message.from_user.id)
    if not row or row.is_banned or row.is_archived:
        return
    text = message.text or ""
    if "getgems.io" not in text and "tonkeeper.com" not in text and "tonviewer.com" not in text:
        return
    try:
        result = await evaluate(text, Decimal(str(row.payout_percent)))
    except Exception:
        return
    saved = await db.save_valuation(
        user_id=row.tg_id,
        url=result.url,
        collection=result.collection,
        item_address=result.item_address,
        floor_price=result.floor_price,
        payout_rate=result.payout_rate,
        employee_share=result.employee_share,
        total_value=result.total_value,
    )
    await message.answer(texts.nft_result_text(result) + f"\n\nID оценки: <code>{saved.id}</code>")
