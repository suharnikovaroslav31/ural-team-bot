"""Подстановка премиум-эмодзи во все исходящие сообщения бота."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

import emoji

log = logging.getLogger("premium_emoji")

# В алертах и подсказках премиум-эмодзи не рисуются, а лимит там 200 символов.
SKIP_METHODS = {"AnswerCallbackQuery", "AnswerInlineQuery", "AnswerWebAppQuery"}

LIMITS = {"text": 4096, "caption": 1024}

_FAIL_MARKS = (
    "tg-emoji",
    "custom emoji",
    "custom_emoji",
    "emoji_invalid",
    "entities",
    "too long",
)


class PremiumEmojiMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot: Bot, method):
        original = _enrich(method)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if not original or not _looks_like_emoji_error(exc):
                raise
            log.warning("Премиум-эмодзи отключены: %s", exc)
            emoji.disable()
            for field, value in original.items():
                setattr(method, field, value)
            _drop_button_icons(method)
            return await make_request(bot, method)


def _enrich(method) -> dict[str, str]:
    if not emoji.enabled() or type(method).__name__ in SKIP_METHODS:
        return {}
    if not _is_html(method):
        return {}
    original: dict[str, str] = {}
    for field, limit in LIMITS.items():
        value = getattr(method, field, None)
        if not isinstance(value, str) or not value:
            continue
        rich = emoji.enrich(value)
        if rich == value or len(rich) > limit:
            continue
        original[field] = value
        setattr(method, field, rich)
    return original


def _is_html(method) -> bool:
    parse_mode = getattr(method, "parse_mode", None)
    if isinstance(parse_mode, str):
        return parse_mode.upper() == "HTML"
    return True


def _drop_button_icons(method) -> None:
    markup = getattr(method, "reply_markup", None)
    if not isinstance(markup, InlineKeyboardMarkup):
        return
    for row in markup.inline_keyboard:
        for button in row:
            if getattr(button, "icon_custom_emoji_id", None):
                button.icon_custom_emoji_id = None


def _looks_like_emoji_error(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return any(mark in text for mark in _FAIL_MARKS)
