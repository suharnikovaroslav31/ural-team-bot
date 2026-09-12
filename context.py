from __future__ import annotations

from typing import Optional

from aiogram import Bot

bot: Optional[Bot] = None
banner_file_id: Optional[str] = None
menu_ids: dict[int, int] = {}
album_buf: dict[str, list[str]] = {}
