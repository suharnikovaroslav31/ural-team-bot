import asyncio
import logging

import config
from database import db


def token_ready() -> bool:
    token = config.BOT_TOKEN
    return bool(token) and not token.startswith("123456789") and ":" in token


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not token_ready():
        logging.error("Нет валидного BOT_TOKEN в .env")
        return

    await db.connect()
    from bot import start_polling
    from services.api import start_api

    api = await start_api()
    logging.info("Бот запущен")
    try:
        await start_polling()
    finally:
        if api is not None:
            await api.cleanup()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
