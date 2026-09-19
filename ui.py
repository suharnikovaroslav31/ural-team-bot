from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, Message

import config
import context
import keyboards as kb
from database.crud import db

log = logging.getLogger("ui")


def banner_file() -> Optional[Path]:
    for name in ("banner.jpg", "banner.jpeg", "banner.png", "banner.webp"):
        path = config.ASSETS / name
        if path.exists():
            return path
    if config.BANNER_PATH.exists():
        return config.BANNER_PATH
    return None


def banner_input():
    if context.banner_file_id:
        return context.banner_file_id
    path = banner_file()
    if path is not None:
        return FSInputFile(path)
    return None


async def remember_banner(message: Message) -> None:
    if message.photo:
        context.banner_file_id = message.photo[-1].file_id


def home_markup(user_id: int):
    return kb.main_kb(admin=config.is_admin(user_id))


async def _send_home(chat_id: int, user_id: int, caption: str):
    if context.bot is None:
        return
    photo = banner_input()
    markup = home_markup(user_id)
    if photo is not None:
        sent = await context.bot.send_photo(chat_id, photo=photo, caption=caption, reply_markup=markup)
        await remember_banner(sent)
    else:
        sent = await context.bot.send_message(chat_id, caption, reply_markup=markup)
    context.menu_ids[chat_id] = sent.message_id


async def show_home(target: Message | CallbackQuery, *, new: bool = False) -> None:
    caption = await db.setting("welcome", "🏠 Ural Team")
    user_id = target.from_user.id if target.from_user else 0
    markup = home_markup(user_id)
    if isinstance(target, CallbackQuery):
        msg = target.message
        if not msg:
            return
        try:
            if msg.photo:
                await msg.edit_caption(caption=caption, reply_markup=markup)
            else:
                await msg.edit_text(caption, reply_markup=markup)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                await send_menu(msg.chat.id, user_id)
        else:
            context.menu_ids[msg.chat.id] = msg.message_id
        return

    if new:
        await _send_home(target.chat.id, user_id, caption)
        return

    await send_menu(target.chat.id, user_id)


async def send_menu(chat_id: int, user_id: int = 0) -> None:
    caption = await db.setting("welcome", "🏠 Ural Team")
    await _send_home(chat_id, user_id, caption)


async def edit_screen(call: CallbackQuery, caption: str, markup) -> None:
    if not call.message:
        return
    limit = 1024 if call.message.photo else 4096
    if len(caption) > limit:
        caption = caption[: limit - 1] + "…"
    try:
        if call.message.photo:
            await call.message.edit_caption(caption=caption, reply_markup=markup)
        else:
            await call.message.edit_text(caption, reply_markup=markup)
        context.menu_ids[call.message.chat.id] = call.message.message_id
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        uid = call.from_user.id if call.from_user else 0
        await send_menu(call.message.chat.id, uid)


async def restore_caption(
    chat_id: int,
    caption: str,
    markup,
    *,
    fallback_user: Optional[int] = None,
) -> None:
    if context.bot is None:
        return
    mid = context.menu_ids.get(chat_id)
    if mid:
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=mid,
                caption=caption,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            try:
                await context.bot.edit_message_text(
                    caption,
                    chat_id=chat_id,
                    message_id=mid,
                    reply_markup=markup,
                )
                return
            except TelegramBadRequest:
                pass
    photo = banner_input()
    if photo is not None:
        sent = await context.bot.send_photo(chat_id, photo=photo, caption=caption, reply_markup=markup)
        await remember_banner(sent)
    else:
        sent = await context.bot.send_message(chat_id, caption, reply_markup=markup)
    context.menu_ids[chat_id] = sent.message_id
