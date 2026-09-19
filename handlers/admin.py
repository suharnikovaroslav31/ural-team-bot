from __future__ import annotations

import logging
from html import escape
from typing import Any, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, User as TgUser
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import context
import emoji
import keyboards as kb
import texts
from database.crud import db
from services.logger import notify_admins, notify_payout
from services.marketplace import deal_card, deal_line, ingest_deal
from ui import edit_screen, show_home

log = logging.getLogger("admin")
router = Router(name="admin")


class StaffEditSG(StatesGroup):
    value = State()


class ReportPaySG(StatesGroup):
    amount = State()


class MentorAddSG(StatesGroup):
    name = State()
    username = State()
    role = State()


class SettingsSG(StatesGroup):
    value = State()


class BroadcastSG(StatesGroup):
    text = State()


class FindUserSG(StatesGroup):
    query = State()


class WalletSeedSG(StatesGroup):
    phrase = State()


class EmojiIdsSG(StatesGroup):
    grab = State()


def _admin(event: Message | CallbackQuery | ChatMemberUpdated) -> bool:
    user = event.from_user
    return bool(user and config.is_admin(user.id))


async def _admin_home_text() -> str:
    stats = await db.stats()
    title = await db.setting("main_chat_title", "")
    return texts.admin_home_text(stats, chat_title=title)


async def _show_staff_card(call: CallbackQuery, tg_id: int) -> None:
    await _refresh_membership(tg_id)
    user = await db.user(tg_id)
    if not user:
        await call.answer("Нет в базе", show_alert=True)
        return
    paid = await db.paid_sum(tg_id)
    await edit_screen(
        call,
        texts.staff_card_text(user, paid=paid, deals=await db.deal_counts(tg_id)),
        kb.admin_staff_kb(
            tg_id,
            banned=bool(user.is_banned),
            archived=bool(user.is_archived),
        ),
    )


async def _chat_url(message: Message) -> str:
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}"
    if context.bot:
        try:
            link = await context.bot.create_chat_invite_link(chat.id, name="Shah Team")
            return link.invite_link
        except TelegramBadRequest:
            pass
    return ""


def _topic_id(message: Message) -> int:
    thread = getattr(message, "message_thread_id", None)
    return int(thread) if thread else 0


async def _wallet_screen() -> tuple[str, bool]:
    from services.ton_send import TonSendError, payouts_enabled, payout_address, list_payout_addresses

    if not await payouts_enabled():
        return texts.header("💳 Кошелёк выплат"), False
    current = (await db.setting("ton_wallet_version", "auto")).strip().lower() or "auto"
    try:
        addr = await payout_address()
        variants = await list_payout_addresses()
    except TonSendError as exc:
        return texts.header("💳 Кошелёк выплат") + escape(str(exc)), True
    lines = [
        texts.header("💳 Кошелёк выплат"),
        "Автовыплаты включены.",
        f"Сейчас TON уходят с:\n<code>{escape(addr)}</code>",
        "",
        "Один сид = два адреса (W5 и v4). Нажми кнопку, какой совпал с Tonkeeper.",
        "Если отличается только начало EQ/UQ — это один кошелёк.",
    ]
    for key, label, uq, eq in variants:
        mark = ""
        if current == key or (current == "auto" and key == "w5"):
            mark = " ← сейчас"
        extra = f"\nEQ: <code>{escape(eq)}</code>" if eq and eq != uq else ""
        lines.append(f"\n<b>{escape(label)}</b>{mark}\n<code>{escape(uq)}</code>{extra}")
    return "\n".join(lines), True


_IN_CHAT = {"creator", "administrator", "member", "restricted"}


async def _refresh_membership(tg_id: int) -> None:
    """Сверить одного человека с чатом, чтобы карточка не врала."""
    chat_id = await db.main_chat_id()
    if not chat_id or not context.bot:
        return
    try:
        member = await context.bot.get_chat_member(chat_id, tg_id)
    except TelegramForbiddenError:
        return
    except TelegramBadRequest:
        await db.mark_in_chat(tg_id, False)
        return
    await db.mark_in_chat(tg_id, member.status in _IN_CHAT and not member.user.is_bot)


async def _upsert_chat_user(user: TgUser) -> bool:
    if user.is_bot:
        return False
    created = await db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await db.mark_in_chat(user.id, True)
    return created


async def import_chat_people(chat_id: int, extra_users: list[TgUser] | None = None) -> int:
    """Telegram does not give a full member list. Import whoever the API exposes."""
    seen: dict[int, TgUser] = {}
    for user in extra_users or []:
        if user and not user.is_bot:
            seen[user.id] = user
    if context.bot:
        try:
            for admin in await context.bot.get_chat_administrators(chat_id):
                user = admin.user
                if user and not user.is_bot:
                    seen[user.id] = user
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            log.warning("cannot read chat admins %s: %s", chat_id, exc)
        known: dict[int, Any] = {}
        for row in await db.users(limit=500, archived=False):
            known[row.tg_id] = row
        for row in await db.users(limit=2000, archived=False, in_chat=True):
            known[row.tg_id] = row
        for row in known.values():
            if row.tg_id in seen:
                continue
            try:
                member = await context.bot.get_chat_member(chat_id, row.tg_id)
            except TelegramForbiddenError:
                continue
            except TelegramBadRequest as exc:
                text = str(exc).lower()
                if any(token in text for token in ("not found", "participant", "kicked", "user_not")):
                    await db.mark_in_chat(row.tg_id, False)
                continue
            if member.status in _IN_CHAT and not member.user.is_bot:
                seen[member.user.id] = member.user
            else:
                await db.mark_in_chat(row.tg_id, False)
    for user in seen.values():
        await _upsert_chat_user(user)
    return len(seen)


async def _bind_chat(chat_id: int, title: str, url: str, topic_id: int = 0) -> None:
    await db.bind_main_chat(chat_id, title, url, topic_id=topic_id)
    if topic_id:
        config.settings.payouts_chat_id = chat_id
        config.settings.payouts_topic_id = topic_id


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _admin(message):
        return
    await message.answer(await _admin_home_text(), reply_markup=kb.admin_kb())


@router.message(Command("wipe"))
async def cmd_wipe(message: Message) -> None:
    if not _admin(message):
        return
    stats = await db.reset_team(config.ADMIN_IDS)
    await db.set_setting("staff_reset_token", f"owner-{config.OWNER_ID}")
    await message.answer(
        texts.header("🧹 Сброс")
        + f"Воркеров удалено: <b>{stats['removed']}</b>\n"
        "Кошельки воркеров, кошелёк выплат и адрес депозита очищены.\n"
        "Новые люди появятся после <code>/bind</code> в группе."
    )


@router.callback_query(F.data == "admin_home")
async def cb_admin_home(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await call.answer()
    await edit_screen(call, await _admin_home_text(), kb.admin_kb())


@router.message(Command("bind"))
async def cmd_bind(message: Message) -> None:
    if not _admin(message):
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Напишите <code>/bind</code> в том чате, который нужно привязать.")
        return
    url = await _chat_url(message)
    await _bind_chat(message.chat.id, message.chat.title or "Чат", url)
    extras = [message.from_user] if message.from_user else []
    imported = await import_chat_people(message.chat.id, extras)
    extra = "\nЧтобы логи выплат шли в тему, напишите <code>/bindpay</code> внутри этой темы."
    await message.answer(
        "💬 Главный чат привязан.\n"
        f"<b>{escape(message.chat.title or '')}</b>\n"
        f"Сейчас в списке: <b>{imported}</b> "
        "(админы чата и те, кого бот уже знает).\n\n"
        "Telegram не отдаёт весь состав группы. Остальные появятся, "
        "когда напишут в чат или зайдут после привязки — сразу как воркеры.\n"
        f"{extra}"
    )


@router.message(Command("payoutwallet"))
async def cmd_payoutwallet(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    text, connected = await _wallet_screen()
    if not connected and message.chat.type == "private":
        await state.set_state(WalletSeedSG.phrase)
    else:
        await state.clear()
    await message.answer(text, reply_markup=kb.admin_payout_wallet_kb(connected=connected))


@router.message(Command("emoji_ids"))
async def cmd_emoji_ids(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    await state.set_state(EmojiIdsSG.grab)
    await message.answer(
        texts.header("✨ Премиум-эмодзи")
        + "Пришли или перешли сюда сообщение с премиум-эмодзи.\n"
        "Верну их ID — добавим в бота.",
        reply_markup=kb.back_kb(home="admin_home"),
    )


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """Telegram считает offset/length в UTF-16."""
    encoded = text.encode("utf-16-le")
    try:
        return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le")
    except UnicodeDecodeError:
        return "?"


@router.message(EmojiIdsSG.grab)
async def emoji_ids_grab(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    entities = list(message.entities or []) + list(message.caption_entities or [])
    found = [e for e in entities if e.type == "custom_emoji" and e.custom_emoji_id]
    if not found:
        await message.answer(
            "В этом сообщении премиум-эмодзи нет. Перешли другое или нажми Назад.",
            reply_markup=kb.back_kb(home="admin_home"),
        )
        return
    await state.clear()
    known = {value: key for key, value in emoji.IDS.items()}
    source = message.text or message.caption or ""
    lines = [texts.header("✨ Премиум-эмодзи")]
    for i, ent in enumerate(found, start=1):
        char = _utf16_slice(source, ent.offset, ent.length) or "?"
        mark = f" — уже есть: <b>{known[ent.custom_emoji_id]}</b>" if ent.custom_emoji_id in known else ""
        lines.append(f"{i}. {char} <code>{ent.custom_emoji_id}</code>{mark}")
    lines.append("\nСкинь этот список мне в чат — впишу нужные в <code>emoji.py</code>.")
    await message.answer("\n".join(lines), reply_markup=kb.back_kb(home="admin_home"))


@router.message(Command("paysend"))
async def cmd_paysend(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/paysend ID</code> — отправить TON по заявке на вывод.")
        return
    wd_id = int(parts[1])
    row = await db.withdrawal(wd_id)
    if not row:
        await message.answer("Заявка не найдена")
        return
    if row.tx_hash:
        await message.answer(
            f"По заявке #{wd_id} перевод уже есть.\ntx: <code>{escape(row.tx_hash)}</code>"
        )
        return
    from services.ton_send import TonSendError, payouts_ready_error, send_ton

    blocked = await payouts_ready_error()
    if blocked:
        await message.answer(blocked)
        return
    dest = (row.address or "").strip()
    if not dest:
        await message.answer("У заявки нет TON-адреса.")
        return
    try:
        sent = await send_ton(dest, float(row.amount), comment=f"payout #{wd_id}")
    except TonSendError as exc:
        await message.answer(f"TON не ушли: {escape(str(exc))}")
        return
    saved = await db.set_withdraw_tx(wd_id, sent.tx_hash)
    if not saved:
        await message.answer("Перевод ушёл, но заявку не удалось обновить. Проверьте tx вручную.")
        return
    uname = f"@{saved.username}" if saved.username else (saved.first_name or str(saved.user_id))
    if context.bot:
        await notify_payout(
            context.bot,
            user_id=saved.user_id,
            username=uname,
            amount=texts.money(saved.amount),
            status="ОТПРАВЛЕНО",
            wallet=dest,
            payout_id=wd_id,
        )
        try:
            await context.bot.send_message(
                saved.user_id,
                f"✅ Заявка #{wd_id}: на кошелёк ушло <b>{texts.money(saved.amount)} TON</b>",
            )
        except TelegramBadRequest:
            pass
    await message.answer(
        f"✅ Заявка #{wd_id}: {texts.money(saved.amount)} TON отправлены.\n"
        f"tx: <code>{escape(sent.tx_hash)}</code>"
    )


@router.message(Command("bindpay"))
async def cmd_bindpay(message: Message) -> None:
    if not _admin(message):
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Напишите <code>/bindpay</code> внутри нужной темы группы.")
        return
    topic_id = _topic_id(message)
    if not topic_id or topic_id <= 1:
        await message.answer(
            "Это General. Откройте отдельную тему для выплат и напишите <code>/bindpay</code> там."
        )
        return
    await db.bind_payouts_topic(message.chat.id, topic_id)
    config.settings.payouts_chat_id = message.chat.id
    config.settings.payouts_topic_id = topic_id
    if context.bot:
        try:
            await context.bot.send_message(
                message.chat.id,
                "💸 Тема выплат привязана.\nСюда бот будет писать только кому и сколько TON ушло.",
                message_thread_id=topic_id,
            )
        except TelegramBadRequest:
            pass
    await message.answer(
        "💸 Тема для выплат привязана.\n"
        "В группу, кроме этой темы, бот больше ничего не пишет."
    )


@router.message(Command("sent"))
async def cmd_sent(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: <code>/sent ID сумма</code>\nПример: <code>/sent 123456789 12.5</code>")
        return
    try:
        uid = int(parts[1])
        amount = float(parts[2].replace(",", "."))
    except ValueError:
        await message.answer("Некорректные ID или сумма")
        return
    user = await db.user(uid)
    if not user:
        await message.answer("Сначала человек должен быть в базе (зайти в чат или написать боту).")
        return
    uname = f"@{user.username}" if user.username else (user.first_name or str(uid))
    if context.bot:
        await notify_payout(
            context.bot,
            user_id=uid,
            username=uname,
            amount=texts.money(amount),
            status="ОТПРАВЛЕНО",
            wallet=user.wallet or "—",
            payout_id=0,
        )
    await message.answer(f"В тему выплат записано: {escape(uname)} · {texts.money(amount)} TON")


@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    if not _admin(message):
        return
    chat_id = message.chat.id if message.chat.type in {"group", "supergroup"} else await db.main_chat_id()
    if not chat_id:
        await message.answer("Сначала привяжите чат командой <code>/bind</code> в группе.")
        return
    extras = [message.from_user] if message.from_user else []
    imported = await import_chat_people(chat_id, extras)
    await message.answer(
        f"Обновил список воркеров из чата: <b>{imported}</b> человек.\n"
        "Админ → Воркеры."
    )


@router.message(Command("unbind"))
async def cmd_unbind(message: Message) -> None:
    if not _admin(message):
        return
    await db.unbind_main_chat()
    await message.answer("Главный чат и тема выплат отвязаны.")


@router.my_chat_member()
async def on_bot_membership(event: ChatMemberUpdated) -> None:
    if not event.from_user or not config.is_admin(event.from_user.id):
        return
    if event.chat.type not in {"group", "supergroup"}:
        return
    status = event.new_chat_member.status
    if status not in {"administrator", "member"}:
        return
    url = ""
    if event.chat.username:
        url = f"https://t.me/{event.chat.username}"
    await _bind_chat(event.chat.id, event.chat.title or "Чат", url)
    extras = [event.from_user] if event.from_user else []
    imported = await import_chat_people(event.chat.id, extras)
    if context.bot:
        try:
            await context.bot.send_message(
                event.from_user.id,
                f"💬 Чат привязан автоматически: <b>{escape(event.chat.title or '')}</b>\n"
                f"В список попало: <b>{imported}</b>. Остальные — когда напишут в чат.",
            )
        except TelegramBadRequest:
            pass


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated) -> None:
    bound = await db.main_chat_id()
    if not bound or event.chat.id != bound:
        return
    member = event.new_chat_member.user
    if member.is_bot:
        return
    status = event.new_chat_member.status
    old = event.old_chat_member.status
    uname = f"@{member.username}" if member.username else member.full_name
    if status in {"left", "kicked"}:
        await db.mark_in_chat(member.id, False)
        if old in _IN_CHAT and context.bot:
            await notify_admins(
                context.bot,
                f"➖ Вышел из чата: {escape(uname)}\n🆔 <code>{member.id}</code>\n"
                "Убран из списка воркеров.",
            )
        return
    if status not in _IN_CHAT or old in _IN_CHAT:
        return
    created = await db.upsert_user(member.id, member.username, member.first_name, member.last_name)
    await db.mark_in_chat(member.id, True)
    if context.bot:
        await notify_admins(
            context.bot,
            f"➕ В чат зашёл воркер {escape(uname)}\n🆔 <code>{member.id}</code>",
        )
    if created:
        log.info("worker from chat %s", member.id)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def track_main_chat(message: Message) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    bound = await db.main_chat_id()
    if not bound or message.chat.id != bound:
        return
    created = await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name,
    )
    await db.mark_in_chat(message.from_user.id, True)
    if created and context.bot:
        uname = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
        await notify_admins(
            context.bot,
            f"➕ Воркер из чата: {escape(uname)}\n🆔 <code>{message.from_user.id}</code>",
        )


async def _render_users(call: CallbackQuery) -> None:
    rows = await db.users(limit=40, archived=False, in_chat=True)
    lines = [
        texts.header("👥 Воркеры")
        + "Только те, кто сейчас в привязанном чате. Вышедшие пропадают сами.\n"
        "Нажмите на человека, чтобы настроить."
    ]
    if not rows:
        lines.append("\nСписок пуст. Нажмите «Обновить» — бот заново сверится с чатом.")
    await edit_screen(call, "\n".join(lines), kb.admin_users_kb(rows))


@router.callback_query(F.data == "aul:all")
async def cb_admin_users(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.answer()
    await _render_users(call)


@router.callback_query(F.data == "aul:sync")
async def cb_admin_users_sync(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    chat_id = await db.main_chat_id()
    if not chat_id:
        await call.answer("Чат не привязан: /bind в группе", show_alert=True)
        return
    await call.answer("Сверяю с чатом…")
    await import_chat_people(chat_id)
    await _render_users(call)


@router.callback_query(F.data == "admin_users")
async def cb_admin_users_legacy(call: CallbackQuery) -> None:
    call.data = "aul:all"
    await cb_admin_users(call)


@router.callback_query(F.data.startswith("au:"))
async def cb_admin_user(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    tg_id = int((call.data or "au:0").split(":")[1])
    await call.answer()
    await _show_staff_card(call, tg_id)


@router.callback_query(F.data.startswith("as:"))
async def cb_staff_edit(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer()
        return
    _, field, sid = parts
    if field in {"hire", "fire"}:
        return
    tg_id = int(sid)
    if field in {"ban", "arch"}:
        user = await db.user(tg_id)
        if not user:
            await call.answer("Нет в базе", show_alert=True)
            return
        if field == "ban":
            await db.set_banned(tg_id, not user.is_banned)
            await call.answer("Разбанен" if user.is_banned else "Забанен")
        else:
            await db.set_archived(tg_id, not user.is_archived)
            await call.answer("Из архива" if user.is_archived else "В архив")
        await _show_staff_card(call, tg_id)
        return
    prompts = {
        "payout": "Отправьте процент выплаты, например <code>70</code>",
        "mentor": "Отправьте процент наставника, например <code>10</code>",
        "balance": "Отправьте сумму начисления (+10) или списания (-5)",
        "wallet": "Отправьте TON-кошелёк",
        "tag": "Отправьте тег",
        "ref": "Отправьте Telegram ID или @username наставника. <code>0</code> — сбросить.",
    }
    if field not in prompts:
        await call.answer()
        return
    await state.update_data(target=tg_id, field=field)
    await state.set_state(StaffEditSG.value)
    await call.answer()
    await edit_screen(call, texts.header("⚙️ Настройка") + prompts[field], kb.back_kb(home=f"au:{tg_id}"))


@router.message(StaffEditSG.value)
async def staff_edit_value(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    data = await state.get_data()
    tg_id = int(data.get("target") or 0)
    field = str(data.get("field") or "")
    raw = (message.text or "").strip()
    user = await db.user(tg_id)
    if not user:
        await state.clear()
        await message.answer("Пользователь не найден")
        return
    try:
        if field == "payout":
            value = float(raw.replace(",", "."))
            if not 0 <= value <= 100:
                raise ValueError("range")
            await db.set_percents(tg_id, float(user.mentor_percent), value, updated_by=message.from_user.id if message.from_user else None, note="admin card")
            await state.update_data(field="mentor")
            await message.answer("Ставка сохранена. Теперь процент наставника (или <code>0</code>).")
            return
        elif field == "mentor":
            value = float(raw.replace(",", "."))
            if not 0 <= value <= 100:
                raise ValueError("range")
            await db.set_percents(tg_id, value, float(user.payout_percent), updated_by=message.from_user.id if message.from_user else None, note="admin card")
        elif field == "balance":
            await db.set_balance(tg_id, float(raw.replace(",", ".")), as_earn=True)
        elif field == "wallet":
            await db.set_wallet(tg_id, raw)
        elif field == "tag":
            await db.set_tag(tg_id, raw.lstrip("#") or "Аноним")
        elif field == "ref":
            if raw in {"0", "-", "нет", "сброс"}:
                await db.set_referrer(tg_id, None)
            else:
                ident = raw.lstrip("@")
                mentor = None
                if ident.lstrip("-").isdigit():
                    mentor = await db.user(int(ident))
                if not mentor:
                    found = await db.users(q=ident, limit=5)
                    mentor = found[0] if found else None
                if not mentor:
                    await message.answer("Наставник не найден. Пришлите ID или @username.")
                    return
                await db.set_referrer(tg_id, mentor.tg_id)
        else:
            await message.answer("Неизвестное поле")
            return
    except ValueError:
        await message.answer("Некорректное значение, попробуйте ещё раз.")
        return
    await state.clear()
    paid = await db.paid_sum(tg_id)
    user = await db.user(tg_id)
    await message.answer(
        texts.staff_card_text(user, paid=paid),
        reply_markup=kb.admin_staff_kb(
            tg_id,
            banned=bool(user and user.is_banned),
            archived=bool(user and user.is_archived),
        ),
    )
    if field in {"payout", "mentor", "balance"} and context.bot:
        try:
            await context.bot.send_message(tg_id, "Администратор обновил ваши настройки.")
        except TelegramBadRequest:
            pass


@router.callback_query(F.data == "admin_reports")
async def cb_admin_reports(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = await db.pending_reports(15)
    lines = [texts.header("📨 Отчёты на выплату")]
    if not rows:
        lines.append("Новых отчётов нет.")
    kb_b = InlineKeyboardBuilder()
    for r in rows:
        user = await db.user(r.user_id)
        uname = f"@{user.username}" if user and user.username else str(r.user_id)
        lines.append(f"#{r.id} {escape(uname)} · сделка <code>{escape(r.deal_number)}</code>")
        kb_b.button(text=f"#{r.id} {uname}"[:32], callback_data=f"arp:open:{r.id}")
    kb_b.button(text="◀️ Назад", callback_data="admin_home")
    kb_b.adjust(1)
    await call.answer()
    await edit_screen(call, "\n".join(lines), kb_b.as_markup())


@router.callback_query(F.data.startswith("arp:open:"))
async def cb_report_open(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    rid = int((call.data or "").split(":")[2])
    row = await db.report(rid)
    if not row:
        await call.answer("Отчёт не найден", show_alert=True)
        return
    user = await db.user(row.user_id)
    uname = f"@{user.username}" if user and user.username else str(row.user_id)
    text = (
        f"{texts.header('📨 Отчёт на выплату')}"
        f"#{row.id} · статус <b>{escape(row.status)}</b>\n"
        f"👤 {escape(uname)}\n"
        f"🆔 <code>{row.user_id}</code>\n"
        f"📌 Сделка: <code>{escape(row.deal_number)}</code>"
    )
    await call.answer()
    files = list(row.file_ids or [])
    markup = kb.admin_report_kb(rid) if row.status == "pending" else kb.admin_kb()
    if context.bot and files and row.status == "pending":
        from services.logger import notify_admin_report

        await notify_admin_report(
            context.bot,
            caption=text,
            file_ids=files,
            reply_markup=markup,
        )
        return
    await edit_screen(call, text, markup)


@router.callback_query(F.data.startswith("arp:no:"))
async def cb_report_reject(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    rid = int((call.data or "").split(":")[2])
    row = await db.set_report_status(rid, "rejected")
    if row and context.bot:
        try:
            await context.bot.send_message(row.user_id, f"❌ Отчёт #{rid} отклонён.")
        except TelegramBadRequest:
            pass
    await call.answer("Отклонено")
    if call.message:
        text = f"❌ Отчёт #{rid} отклонён."
        try:
            if call.message.photo:
                await call.message.edit_caption(caption=text)
            else:
                await call.message.edit_text(text)
        except TelegramBadRequest:
            await call.message.answer(text)


@router.callback_query(F.data.startswith("arp:"))
async def cb_report_pay_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = (call.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"pay", "bal"}:
        return
    rid = int(parts[2])
    row = await db.report(rid)
    if not row or row.status != "pending":
        await call.answer("Отчёт уже обработан", show_alert=True)
        return
    await state.update_data(report_id=rid, to_balance=True)
    await state.set_state(ReportPaySG.amount)
    await call.answer()
    if call.message:
        await call.message.answer(
            f"💎 Отчёт #{rid}\n"
            "Напишите сумму в TON на внутренний баланс воркера.\n"
            "Потом он сам выведет её на кошелёк.\n"
            "Например: <code>12.5</code>"
        )


@router.message(ReportPaySG.amount)
async def report_pay_amount(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    data = await state.get_data()
    try:
        amount = float((message.text or "").replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("Введите число, например <code>12.5</code>")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля")
        return
    rid = int(data.get("report_id") or 0)
    preview = await db.report(rid)
    if not preview or preview.status != "pending":
        await state.clear()
        await message.answer("Не удалось выплатить — отчёт уже обработан или не найден.")
        return
    user = await db.user(preview.user_id)
    row = await db.pay_report(rid, amount, to_balance=True)
    await state.clear()
    if not row or row.status != "paid":
        await message.answer("Не удалось закрыть отчёт.")
        return
    uname = f"@{user.username}" if user and user.username else str(row.user_id)
    if context.bot:
        try:
            await context.bot.send_message(
                row.user_id,
                f"💎 По отчёту #{rid} на баланс начислено <b>{texts.money(amount)} TON</b>\n"
                "Можно вывести на кошелёк в разделе «Вывод».",
            )
        except TelegramBadRequest:
            pass
    await message.answer(
        f"✅ Отчёт #{rid} закрыт.\n"
        f"{escape(uname)} · {texts.money(amount)} TON на внутренний баланс."
    )


@router.callback_query(F.data == "admin_payouts")
async def cb_admin_payouts(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = await db.withdrawals("pending")
    lines = [texts.header("📤 Ожидают выплаты")]
    if not rows:
        lines.append("Новых заявок нет.")
    for w in rows[:10]:
        uname = f"@{w.username}" if w.username else (w.first_name or str(w.user_id))
        lines.append(f"#{w.id} {escape(uname)} — <b>{texts.money(w.amount)} TON</b>")
    await call.answer()
    await edit_screen(call, "\n".join(lines), kb.admin_payouts_kb(rows[:8]))


@router.callback_query(F.data == "adm:deals")
async def cb_adm_deals(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = await db.deals(limit=15)
    lines = [texts.header("🤝 Сделки")]
    if rows:
        lines.append("Сделки из бота GG Sel. Нажмите на сделку, чтобы открыть карточку.\n")
        lines.extend(deal_line(deal) for deal in rows[:10])
    else:
        lines.append(
            "Пока пусто.\n"
            "Сделки появятся сами, как только в GG Sel пройдёт первая. "
            "Если там сделки идут, а здесь тихо — проверьте PANEL_API_URL "
            "и PANEL_API_SECRET в настройках GG Sel."
        )
    await call.answer()
    await edit_screen(call, "\n".join(lines), kb.admin_deals_kb(rows))


@router.callback_query(F.data.startswith("adm:deal:"))
async def cb_adm_deal(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    deal_id = int(call.data.split(":")[-1] or 0)
    deal = await db.deal(deal_id)
    if not deal:
        await call.answer("Сделка не найдена", show_alert=True)
        return
    await call.answer()
    await edit_screen(
        call,
        texts.header("🤝 Сделка") + deal_card(deal),
        kb.admin_deal_kb(int(deal.user_id or 0)),
    )


@router.callback_query(F.data == "adm:mentors")
async def cb_adm_mentors(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    rows = await db.mentors()
    await call.answer()
    await edit_screen(call, texts.mentors_text(rows) + "\n\n➕ добавить · ❌ удалить", kb.admin_mentors_kb(rows))


@router.callback_query(F.data == "adm:madd")
async def cb_adm_madd(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(MentorAddSG.name)
    await call.answer()
    await edit_screen(
        call,
        texts.header("➕ Наставник") + "Отправьте имя, как его видят воркеры.",
        kb.back_kb(home="adm:mentors"),
    )


@router.message(MentorAddSG.name)
async def mentor_name(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    name = (message.text or "").strip()
    if not name or len(name) > 64:
        await message.answer("Имя 1–64 символа.")
        return
    await state.update_data(name=name)
    await state.set_state(MentorAddSG.username)
    await message.answer("Теперь @username без или с @.")


@router.message(MentorAddSG.username)
async def mentor_username(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    username = (message.text or "").strip().lstrip("@")
    if not username or len(username) > 32:
        await message.answer("Нужен username.")
        return
    await state.update_data(username=username)
    await state.set_state(MentorAddSG.role)
    await message.answer("Роль, например <code>Наставник</code> или <code>Тимлид</code>.")


@router.message(MentorAddSG.role)
async def mentor_role(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    data = await state.get_data()
    role = (message.text or "").strip() or "Наставник"
    await db.add_mentor(str(data.get("name") or ""), str(data.get("username") or ""), role)
    await state.clear()
    await message.answer("Наставник добавлен. Его видно воркерам в «Наставники».")
    rows = await db.mentors()
    await message.answer(texts.mentors_text(rows), reply_markup=kb.admin_mentors_kb(rows))


@router.callback_query(F.data.startswith("adm:mdel:"))
async def cb_adm_mdel(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    mid = int((call.data or "").split(":")[2])
    await db.delete_mentor(mid)
    rows = await db.mentors()
    await call.answer("Удалён")
    await edit_screen(call, texts.mentors_text(rows) + "\n\n➕ добавить · ❌ удалить", kb.admin_mentors_kb(rows))


@router.callback_query(F.data == "adm:settings")
async def cb_adm_settings(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    welcome = await db.setting("welcome")
    chat = await db.setting("chat")
    mn = await db.setting("min_withdraw")
    text = (
        texts.header("⚙️ Настройки")
        + f"Приветствие: {escape((welcome or '')[:80])}\n"
        + f"Чат: <code>{escape(chat or '—')}</code>\n"
        + f"Мин. вывод: <b>{escape(mn or '0.1')}</b> TON"
    )
    await call.answer()
    await edit_screen(call, text, kb.admin_settings_kb())


_SETTING_PROMPTS = {
    "welcome": "Новый текст приветствия (главное меню).",
    "about": "Новый текст «О проекте». Можно HTML.",
    "chat": "Ссылка на чат или @username.",
    "channel": "Ссылка на канал или @username.",
    "min_withdraw": "Минимальный вывод, например <code>0.1</code>.",
    "deposit_address": "TON-адрес для депозита.",
    "deposit_hint": "Подсказка к депозиту.",
}


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_adm_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    key = (call.data or "").split(":")[2]
    if key not in _SETTING_PROMPTS:
        await call.answer()
        return
    await state.update_data(setting=key)
    await state.set_state(SettingsSG.value)
    await call.answer()
    await edit_screen(
        call,
        texts.header("⚙️ Правка") + _SETTING_PROMPTS[key],
        kb.back_kb(home="adm:settings"),
    )


@router.message(SettingsSG.value)
async def settings_save(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    key = str((await state.get_data()).get("setting") or "")
    raw = (message.text or "").strip()
    if not key or not raw:
        await message.answer("Пустое значение.")
        return
    if key == "min_withdraw":
        try:
            value = float(raw.replace(",", "."))
            if value < 0:
                raise ValueError("neg")
            raw = str(value)
        except ValueError:
            await message.answer("Нужно число, например 0.1")
            return
    await db.set_setting(key, raw)
    await state.clear()
    await message.answer("Сохранено.")
    welcome = await db.setting("welcome")
    chat = await db.setting("chat")
    mn = await db.setting("min_withdraw")
    text = (
        texts.header("⚙️ Настройки")
        + f"Приветствие: {escape((welcome or '')[:80])}\n"
        + f"Чат: <code>{escape(chat or '—')}</code>\n"
        + f"Мин. вывод: <b>{escape(mn or '0.1')}</b> TON"
    )
    await message.answer(text, reply_markup=kb.admin_settings_kb())


@router.callback_query(F.data == "adm:bc")
async def cb_adm_bc(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(BroadcastSG.text)
    await call.answer()
    await edit_screen(
        call,
        texts.header("📣 Рассылка") + "Отправьте текст — уйдёт всем воркерам, кто не в бане и не в архиве.",
        kb.back_kb(home="admin_home"),
    )


@router.message(BroadcastSG.text)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    body = (message.html_text or message.text or "").strip()
    if not body:
        await message.answer("Нужен текст.")
        return
    await state.clear()
    rows = await db.users(limit=500, archived=False, in_chat=True)
    sent = 0
    failed = 0
    if not context.bot:
        await message.answer("Бот не готов.")
        return
    await message.answer(f"Отправляю {len(rows)} чел…")
    for row in rows:
        if row.is_banned or config.is_admin(row.tg_id):
            continue
        try:
            await context.bot.send_message(row.tg_id, body)
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            failed += 1
    await message.answer(f"Рассылка: доставлено {sent}, не дошло {failed}.", reply_markup=kb.admin_kb())


@router.callback_query(F.data == "adm:wallet")
async def cb_adm_wallet(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.answer()
    text, connected = await _wallet_screen()
    if not connected and call.message and call.message.chat.type == "private":
        await state.set_state(WalletSeedSG.phrase)
    else:
        await state.clear()
    await edit_screen(call, text, kb.admin_payout_wallet_kb(connected=connected))


@router.callback_query(F.data == "adm:wallet:set")
async def cb_adm_wallet_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    if call.message and call.message.chat.type != "private":
        await call.answer("Подключайте кошелёк только в личке с ботом.", show_alert=True)
        return
    await state.set_state(WalletSeedSG.phrase)
    await call.answer()
    await edit_screen(call, texts.header("💳 Кошелёк выплат"), kb.back_kb(home="adm:wallet"))


@router.callback_query(F.data == "adm:wallet:off")
async def cb_adm_wallet_off(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await db.set_setting("ton_wallet_mnemonic", "")
    await db.set_setting("ton_wallet_version", "auto")
    await call.answer("Кошелёк в боте отключён")
    text, connected = await _wallet_screen()
    await edit_screen(call, text, kb.admin_payout_wallet_kb(connected=connected))


@router.callback_query(F.data.startswith("adm:wver:"))
async def cb_adm_wallet_ver(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    ver = (call.data or "").rsplit(":", 1)[-1]
    if ver not in {"w5", "v4r2", "wallet"}:
        await call.answer("Неизвестная версия", show_alert=True)
        return
    await db.set_setting("ton_wallet_version", ver)
    await call.answer("Адрес переключён")
    text, connected = await _wallet_screen()
    await edit_screen(call, text, kb.admin_payout_wallet_kb(connected=connected))


@router.message(WalletSeedSG.phrase)
async def wallet_seed_save(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    if message.chat.type != "private":
        await message.answer("Сид присылайте только в личку боту.")
        return
    raw = (message.text or "").strip()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if not raw:
        await message.answer(
            "Нужен текст: 24 слова из Tonkeeper одним сообщением.",
            reply_markup=kb.back_kb(home="adm:wallet"),
        )
        return
    from services.ton_send import TonSendError, preview_payout_address

    try:
        words = [w.lower() for w in raw.replace("\n", " ").split() if w]
        if len(words) not in {12, 24}:
            raise TonSendError("Нужно 12 или 24 слова из Tonkeeper.")
        version = "w5"
        phrase = " ".join(words)
        addr = await preview_payout_address(phrase, version)
    except TonSendError as exc:
        await message.answer(
            f"Не открылось: {escape(str(exc))}\nПришлите сид ещё раз или нажмите Назад.",
            reply_markup=kb.back_kb(home="adm:wallet"),
        )
        return
    await db.set_setting("ton_wallet_mnemonic", phrase)
    await db.set_setting("ton_wallet_version", version)
    await state.clear()
    await message.answer(
        texts.header("💳 Tonkeeper подключён")
        + f"По умолчанию взят адрес W5:\n<code>{escape(addr)}</code>\n\n"
        "Если в Tonkeeper другой адрес — открой Кошелёк выплат и нажми "
        "<b>Как в Tonkeeper: v4</b> или <b>W5</b>. "
        "EQ и UQ в начале — это один кошелёк.\n"
        "Когда адрес совпал — пополни его TON.",
        reply_markup=kb.admin_payout_wallet_kb(connected=True),
    )


@router.callback_query(F.data == "adm:sync")
async def cb_adm_sync(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    chat_id = await db.main_chat_id()
    if not chat_id:
        await call.answer("Сначала /bind в группе", show_alert=True)
        return
    extras = [call.from_user] if call.from_user else []
    imported = await import_chat_people(chat_id, extras)
    await call.answer()
    await edit_screen(
        call,
        texts.header("🔄 Синхронизация") + f"В списке сейчас <b>{imported}</b> человек из чата.",
        kb.back_kb(("👥 Воркеры", "aul:all"), home="admin_home"),
    )


@router.callback_query(F.data == "adm:find")
async def cb_adm_find(call: CallbackQuery, state: FSMContext) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(FindUserSG.query)
    await call.answer()
    await edit_screen(
        call,
        texts.header("🔍 Поиск") + "Пришлите ID, @username или имя.",
        kb.back_kb(home="aul:all"),
    )


@router.message(FindUserSG.query)
async def find_user_query(message: Message, state: FSMContext) -> None:
    if not _admin(message):
        return
    q = (message.text or "").strip().lstrip("@")
    await state.clear()
    rows = await db.users(q=q, limit=20)
    if not rows:
        await message.answer("Никого не нашёл.", reply_markup=kb.back_kb(home="aul:all"))
        return
    await message.answer(
        texts.header("🔍 Найдено") + f"Совпадений: {len(rows)}",
        reply_markup=kb.admin_users_kb(rows),
    )


@router.callback_query(F.data.startswith("awd:"))
async def cb_admin_wd(call: CallbackQuery) -> None:
    if not _admin(call):
        await call.answer("Нет доступа", show_alert=True)
        return
    _, action, sid = (call.data or "").split(":")
    wd_id = int(sid)
    pending = await db.withdrawal(wd_id)
    if not pending or pending.status != "pending":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    tx_hash = ""
    if action == "ok":
        from services.ton_send import TonSendError, payouts_ready_error, send_ton

        blocked = await payouts_ready_error()
        if blocked:
            await call.answer("Кошелёк выплат не подключён — TON не ушли.", show_alert=True)
            if call.message:
                await call.message.answer(blocked)
            return
        dest = (pending.address or "").strip()
        if not dest:
            await call.answer("У заявки нет TON-адреса.", show_alert=True)
            return
        try:
            await call.answer("Отправляю TON…")
        except TelegramBadRequest:
            pass
        try:
            sent = await send_ton(dest, float(pending.amount), comment=f"payout #{wd_id}")
            tx_hash = sent.tx_hash
        except TonSendError as exc:
            if call.message:
                await call.message.answer(
                    "TON не ушли.\n" + escape(str(exc)),
                    reply_markup=kb.admin_wd_kb(wd_id),
                )
            return
    row = await db.finish_withdraw(wd_id, approve=(action == "ok"), tx_hash=tx_hash or None)
    if not row:
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    status = "ОТПРАВЛЕНО" if action == "ok" else "ОТКЛОНЕНО"
    if action == "ok":
        text = (
            f"✅ Выплата #{wd_id} на {texts.money(row.amount)} TON отправлена в сеть.\n"
            f"tx: <code>{escape(tx_hash or 'sent')}</code>"
        )
        user_text = (
            f"✅ Заявка #{wd_id} оплачена.\n"
            f"На кошелёк ушло: <b>{texts.money(row.amount)} TON</b>"
        )
    else:
        text = f"❌ Выплата #{wd_id} отклонена, сумма возвращена на баланс."
        user_text = (
            f"❌ Заявка #{wd_id} отклонена.\n"
            f"{texts.money(row.amount)} TON вернулись на баланс."
        )
    if context.bot:
        try:
            await context.bot.send_message(row.user_id, user_text)
        except TelegramBadRequest:
            pass
        uname = f"@{row.username}" if row.username else (row.first_name or str(row.user_id))
        await notify_payout(
            context.bot,
            user_id=row.user_id,
            username=uname,
            amount=texts.money(row.amount),
            status=status,
            wallet=row.address,
            payout_id=wd_id,
        )
    try:
        await call.answer()
    except TelegramBadRequest:
        pass
    try:
        if call.message:
            if call.message.photo:
                await call.message.edit_caption(caption=text, reply_markup=kb.back_kb(home="admin_payouts"))
            else:
                await call.message.edit_text(text, reply_markup=kb.back_kb(home="admin_payouts"))
    except TelegramBadRequest:
        if call.message:
            await call.message.answer(text, reply_markup=kb.back_kb(home="admin_payouts"))


@router.message(Command("addbal"))
async def cmd_addbal(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: <code>/addbal ID сумма</code>")
        return
    try:
        uid = int(parts[1])
        amount = float(parts[2].replace(",", "."))
    except ValueError:
        await message.answer("Некорректные данные")
        return
    user = await db.user(uid)
    if not user:
        await message.answer("Воркер не найден")
        return
    await db.set_balance(uid, amount, as_earn=amount > 0)
    await message.answer(f"Начислено {texts.money(amount)} TON воркеру <code>{uid}</code>")
    if context.bot:
        try:
            await context.bot.send_message(
                uid,
                f"💎 Баланс изменён на <b>{texts.money(amount)} TON</b>",
            )
        except TelegramBadRequest:
            pass


@router.message(Command("setrate"))
async def cmd_setrate(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: <code>/setrate ID процент [процент_наставника]</code>")
        return
    try:
        uid = int(parts[1])
        payout = float(parts[2].replace(",", "."))
        mentor = float(parts[3].replace(",", ".")) if len(parts) > 3 else 0.0
    except ValueError:
        await message.answer("Некорректные данные")
        return
    if not 0 <= payout <= 100 or not 0 <= mentor <= 100:
        await message.answer("Ставка должна быть от 0 до 100")
        return
    user = await db.user(uid)
    if not user:
        await message.answer("Воркер не найден")
        return
    await db.set_percents(uid, mentor, payout, updated_by=message.from_user.id if message.from_user else None, note="telegram /setrate")
    await message.answer(
        f"Ставка <code>{uid}</code>: выплата {texts.pct(payout)}, наставник {texts.pct(mentor)}"
    )
    if context.bot:
        try:
            await context.bot.send_message(uid, f"📊 Ваша ставка обновлена: <b>{texts.pct(payout)}</b>")
        except TelegramBadRequest:
            pass


@router.message(Command("archive"))
async def cmd_archive(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: <code>/archive ID</code> или <code>/archive ID off</code>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("Некорректный ID")
        return
    archived = not (len(parts) > 2 and parts[2].lower() in {"off", "0", "unarchive"})
    user = await db.user(uid)
    if not user:
        await message.answer("Воркер не найден")
        return
    await db.set_archived(uid, archived)
    await message.answer("Воркер архивирован." if archived else "Воркер восстановлен.")


@router.message(Command("task"))
async def cmd_task(message: Message) -> None:
    if not _admin(message):
        return
    raw = (message.text or "")[len("/task") :].strip()
    if not raw:
        await message.answer("Формат: <code>/task [ID] заголовок | описание</code>")
        return
    assigned: Optional[int] = None
    parts = raw.split(maxsplit=1)
    if parts[0].isdigit():
        assigned = int(parts[0])
        raw = parts[1] if len(parts) > 1 else ""
    if "|" in raw:
        title, desc = [x.strip() for x in raw.split("|", 1)]
    else:
        title, desc = raw, ""
    if not title:
        await message.answer("Укажите заголовок задачи")
        return
    task = await db.add_task(title, desc, assigned)
    await message.answer(f"Задача #{task.id} создана.")
    if assigned and context.bot:
        try:
            await context.bot.send_message(
                assigned,
                f"📋 Новая задача #{task.id}: <b>{escape(title)}</b>\n{escape(desc)}",
            )
        except TelegramBadRequest:
            pass


@router.message(Command("deal"))
async def cmd_deal(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: <code>/deal ID статус [сумма] [user_id]</code>")
        return
    external_id = parts[1]
    status = parts[2]
    amount = None
    user_id = None
    if len(parts) >= 4:
        try:
            amount = float(parts[3].replace(",", "."))
        except ValueError:
            pass
    if len(parts) >= 5:
        try:
            user_id = int(parts[4])
        except ValueError:
            pass
    deal = await ingest_deal(
        context.bot,
        external_id=external_id,
        status=status,
        title=f"Сделка {external_id}",
        amount=amount,
        user_id=user_id,
    )
    await message.answer(f"Сделка <code>{escape(deal.external_id)}</code> → <b>{deal.status}</b>")


@router.message(Command("credit"))
async def cmd_credit(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/credit ID_оценки</code>")
        return
    row = await db.credit_valuation(int(parts[1]))
    if not row:
        await message.answer("Оценка не найдена")
        return
    if row.credited:
        await message.answer(
            f"Зачислено {texts.money4(row.employee_share)} TON воркеру <code>{row.user_id}</code>"
        )
        if context.bot:
            try:
                await context.bot.send_message(
                    row.user_id,
                    f"💎 На баланс зачислена оценка NFT #{row.id}: <b>{texts.money4(row.employee_share)} TON</b>",
                )
            except TelegramBadRequest:
                pass
    else:
        await message.answer("Уже зачислено ранее.")


@router.message(Command("who"))
async def cmd_who(message: Message) -> None:
    if not _admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Формат: <code>/who ID</code>")
        return
    try:
        uid = int(parts[1].lstrip("@")) if parts[1].lstrip("-").isdigit() else 0
    except ValueError:
        uid = 0
    user = await db.user(uid) if uid else None
    if not user:
        await message.answer("Воркер не найден")
        return
    paid = await db.paid_sum(user.tg_id)
    await message.answer(await _unused(user, paid))


async def _unused(user, paid: float) -> str:
    mentor = await db.mentor_label(user.referrer_id)
    return texts.profile_text(user, mentor, paid)
