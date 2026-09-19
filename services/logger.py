from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from typing import Any, Optional, Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto

from config import settings
from services.ton import mask_wallet

log = logging.getLogger("logger")


async def _bound_chat() -> int:
    if settings.monitor_chat_id:
        return settings.monitor_chat_id
    try:
        from database.crud import db

        return await db.main_chat_id()
    except Exception:
        return 0


async def _payouts_dest() -> tuple[int, int]:
    if settings.payouts_chat_id:
        return settings.payouts_chat_id, settings.payouts_topic_id
    try:
        from database.crud import db

        return await db.payouts_destination()
    except Exception:
        return 0, 0

HR = "━━━━━━━━━━━━━━━━━━━━"


def _thread(chat_id: int, topic_id: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if chat_id:
        if topic_id:
            kwargs["message_thread_id"] = topic_id
    return kwargs


def card(title: str, lines: Sequence[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return f"<b>{escape(title)}</b>\n{HR}\n{body}"


async def send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    topic_id: int = 0,
    reply_markup=None,
) -> bool:
    if not chat_id:
        return False
    if chat_id < 0 and topic_id <= 1:
        log.warning("skip group General post to %s/%s", chat_id, topic_id)
        return False
    kwargs = _thread(chat_id, topic_id)
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup, **kwargs)
        return True
    except TelegramBadRequest as exc:
        log.warning("Cannot send to %s/%s: %s", chat_id, topic_id, exc)
        return False


async def notify_admin_report(
    bot: Bot,
    *,
    caption: str,
    file_ids: Sequence[str],
    reply_markup=None,
) -> None:
    """Send report to admin PM only: first photo + buttons, extra shots after."""
    photos = [fid for fid in file_ids if fid][:10]
    text = caption[:1024]
    for admin_id in settings.admin_id_set:
        if admin_id <= 0:
            continue
        try:
            if photos:
                await bot.send_photo(
                    admin_id,
                    photo=photos[0],
                    caption=text,
                    reply_markup=reply_markup,
                )
                extra = photos[1:]
                if extra:
                    media = [InputMediaPhoto(media=fid) for fid in extra]
                    await bot.send_media_group(admin_id, media)
            else:
                await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            log.warning("report notify to %s failed: %s", admin_id, exc)
            await send_text(bot, admin_id, text, reply_markup=reply_markup)


async def notify_payout(
    bot: Bot,
    *,
    user_id: int,
    username: str,
    amount: str,
    status: str,
    wallet: str,
    payout_id: int,
) -> None:
    stamp = datetime.utcnow().strftime("%d.%m.%Y %H:%M")
    text = card(
        "💸 Выплачено",
        [
            f"👤 Кому: {escape(username)}",
            f"🆔 <code>{user_id}</code>",
            f"💎 Сумма: <b>{escape(amount)} TON</b>",
            f"👛 <code>{escape(wallet or '—')}</code>",
            f"🕒 {stamp}",
            f"🧾 #{payout_id}" if payout_id else "",
        ],
    )
    kind = (status or "").strip().upper()
    if kind in {"ОТКЛОНЕНО", "НА БАЛАНС", "ТЕМА ПРИВЯЗАНА"}:
        return
    chat_id, topic_id = await _payouts_dest()
    if not chat_id or topic_id <= 1:
        log.warning("Payout topic is not bound (or it is General), skip group post")
        return
    await send_text(bot, chat_id, text, topic_id=topic_id)


async def notify_deal(
    bot: Bot,
    *,
    external_id: str,
    status: str,
    title: str,
    amount: Optional[str],
    user_id: Optional[int],
) -> None:
    stamp = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    flag = "✅" if status == "success" else ("⚠️" if status == "error" else "⏳")
    lines = [
        f"{flag} Сделка <code>{escape(external_id)}</code>",
        f"📌 {escape(title or '—')}",
        f"Статус: <b>{escape(status.upper())}</b>",
        f"🕒 {stamp}",
    ]
    if amount:
        lines.insert(2, f"💎 {escape(amount)} TON")
    if user_id:
        lines.append(f"👤 воркер <code>{user_id}</code>")
    if not settings.deals_chat_id or settings.deals_topic_id <= 1:
        return
    text = card("Shah Team · DEAL", lines)
    await send_text(bot, settings.deals_chat_id, text, topic_id=settings.deals_topic_id)


async def notify_admins(bot: Bot, text: str, reply_markup=None) -> None:
    for admin_id in settings.admin_id_set:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except TelegramBadRequest:
            log.warning("Cannot notify admin %s", admin_id)
