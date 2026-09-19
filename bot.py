from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault, MenuButtonCommands
from aiogram.types.bot_command import BotCommand

import config
import context
from handlers.admin import router as admin_router
from handlers.employee import router as employee_router
from services.premium_emoji import PremiumEmojiMiddleware

log = logging.getLogger("bot")
dp = Dispatcher()
dp.include_router(admin_router)
dp.include_router(employee_router)

bot = Bot(
    token=config.BOT_TOKEN or "0:init",
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
context.bot = bot


async def setup_bot() -> Bot:
    if not config.BOT_TOKEN or config.BOT_TOKEN.startswith("123456789"):
        raise RuntimeError("Укажите BOT_TOKEN в файле .env")
    global bot
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.session.middleware(PremiumEmojiMiddleware())
    context.bot = bot
    public = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="menu", description="Открыть меню"),
    ]
    await bot.set_my_commands(public, scope=BotCommandScopeDefault())
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    *public,
                    BotCommand(command="admin", description="Панель: статистика и команды"),
                    BotCommand(command="bind", description="Привязать группу к боту"),
                    BotCommand(command="sync", description="Подтянуть людей из привязанного чата"),
                    BotCommand(command="bindpay", description="Тема, куда писать выплаты TON"),
                    BotCommand(command="payoutwallet", description="Адрес кошелька автовыплат"),
                    BotCommand(command="paysend", description="Отправить TON по заявке на вывод"),
                    BotCommand(command="sent", description="Вручную: отправил TON человеку"),
                    BotCommand(command="addbal", description="Начислить/списать баланс: ID сумма"),
                    BotCommand(command="setrate", description="Ставка воркера: ID процент"),
                    BotCommand(command="emoji_ids", description="ID премиум-эмодзи из сообщения"),
                    BotCommand(command="unbind", description="Отвязать группу и тему"),
                    BotCommand(command="wipe", description="Снести старых воркеров и кошельки"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            log.warning("Cannot set admin commands for %s", admin_id)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    return bot


async def start_polling() -> None:
    await setup_bot()
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        )
    finally:
        await bot.session.close()
