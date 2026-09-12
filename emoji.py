"""Премиум-эмодзи для текстов и кнопок.

Обычные эмодзи в коде остаются как есть, а premium-версия подставляется
на выходе (см. services/premium_emoji.py). Работает, пока у владельца бота
активен Telegram Premium; иначе premium автоматически выключается и остаются
обычные эмодзи. Новые ID: команда /emoji_ids в личке боту.
"""

from __future__ import annotations

import re

IDS: dict[str, str] = {
    "brand": "6039802097916974085",
    "ton": "5235630047959727475",
    "money": "5893473283696759404",
    "card": "5902056028513505203",
    "wallet": "6039641775377748623",
    "person": "6032949275732742941",
    "people": "6032609071373226027",
    "mentor": "5778672437122045013",
    "chart": "5244837092042750681",
    "report": "5778299625370817409",
    "list": "6034969813032374911",
    "pen": "5395444784611480792",
    "link": "5271604874419647061",
    "megaphone": "5458603043203327669",
    "chat": "5443038326535759644",
    "warn": "5274099962655816924",
    "ok": "5206607081334906820",
    "no": "5210952531676504517",
    "plus": "5361847815255372871",
    "back": "5895507195524550741",
    "lightning": "5456140674028019486",
    "shield": "5902016123972358349",
    "star": "5463289097336405244",
    "sparkle": "5325547803936572038",
    "gift": "5203996991054432397",
    "channel": "5424818078833715060",
    "question": "5467538555158943525",
    "one": "5794164805065514131",
    "two": "5794085322400733645",
    "three": "5794280000383358988",
    "four": "5794241397217304511",
}

# Какой обычный эмодзи в текстах заменяется на какой премиум.
UNICODE_MAP: dict[str, str] = {
    "🔱": "brand",
    "👨‍🏫": "mentor",
    "🤝": "mentor",
    "👥": "people",
    "👤": "person",
    "💎": "ton",
    "💰": "money",
    "💸": "money",
    "🏧": "money",
    "📤": "money",
    "💳": "card",
    "👛": "wallet",
    "📊": "chart",
    "📈": "chart",
    "📉": "chart",
    "📨": "report",
    "📝": "report",
    "📋": "list",
    "📄": "list",
    "📦": "list",
    "📌": "list",
    "✏️": "pen",
    "✎": "pen",
    "🏷": "pen",
    "🔗": "link",
    "🌐": "link",
    "📣": "megaphone",
    "🔔": "megaphone",
    "📢": "channel",
    "💬": "chat",
    "⚠️": "warn",
    "❗": "warn",
    "🚫": "warn",
    "➖": "no",
    "✅": "ok",
    "❌": "no",
    "➕": "plus",
    "◀️": "back",
    "⬅️": "back",
    "⚡": "lightning",
    "🔄": "lightning",
    "🛠": "shield",
    "🛡": "shield",
    "⭐": "star",
    "✨": "sparkle",
    "🪄": "sparkle",
    "🆕": "sparkle",
    "🎁": "gift",
    "ⓘ": "question",
    "🤔": "question",
}

_TAG = re.compile(r"</?tg-emoji(?: emoji-id=\"\d+\")?>")

# Сначала длинные последовательности: ⚠️ и ◀️ содержат вариационный селектор.
_ORDER = sorted(UNICODE_MAP, key=len, reverse=True)

_enabled = True


def enabled() -> bool:
    return _enabled


def disable() -> None:
    """Отключить премиум до перезапуска (например, Premium у владельца кончился)."""
    global _enabled
    _enabled = False


def icon(key: str) -> str | None:
    """icon_custom_emoji_id для InlineKeyboardButton."""
    if not _enabled:
        return None
    return IDS.get(key) or None


def enrich(text: str) -> str:
    """Заменить обычные эмодзи на премиум-версии."""
    if not _enabled or not text:
        return text
    out = text
    for char in _ORDER:
        if char not in out:
            continue
        emoji_id = IDS.get(UNICODE_MAP[char], "")
        if not emoji_id:
            continue
        out = out.replace(char, f'<tg-emoji emoji-id="{emoji_id}">{char}</tg-emoji>')
    return out


def strip(text: str) -> str:
    """Убрать теги премиум-эмодзи, оставив обычные."""
    return _TAG.sub("", text or "")
