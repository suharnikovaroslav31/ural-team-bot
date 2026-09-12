# Ural Team

Telegram-бот учёта задач, агентских комиссий и выплат в TON (Aiogram 3 + SQLite/PostgreSQL).

## Состав

- Кабинет сотрудника: задачи, баланс, отчёты, оценка NFT, заявка на выплату
- Админ в боте: `/admin`, ставки, выплаты, сделки
- Калькулятор NFT: floor Getgems / TONAPI × персональная ставка
- Журнал медиа и уведомления о выплатах в Telegram-топики

## Запуск

```bat
python main.py
```

Напишите боту `/start`. Порт 8080 больше не используется.

Для PostgreSQL:

```bat
docker compose up -d
```

и в `.env`:

```
DATABASE_URL=postgresql+asyncpg://ural:ural@127.0.0.1:5432/ural_team
```

## Деплой на хостинг

- Python 3.11+
- Установка: `pip install -r requirements.txt`
- Команда запуска: `python main.py`

Переменные окружения на хостинге (файл `.env` в репозиторий не попадает):

| Переменная | Зачем |
| --- | --- |
| `BOT_TOKEN` | токен бота от BotFather |
| `ADMIN_IDS` | Telegram ID админов через запятую |
| `DATABASE_URL` | по умолчанию SQLite в `data/bot.db` |
| `TONCENTER_API_KEY` | необязательно, лимиты TON API |

Кошелёк выплат на хостинге подключается заново: Админ → Кошелёк выплат → Подключить Tonkeeper.
Секретные слова в репозитории не хранятся.

Бот должен работать **в одном экземпляре**: после запуска на хостинге локальный `python main.py` нужно остановить, иначе Telegram выдаёт конфликт.

## Команды

Сотрудник: `/start` `/menu` `/web`

Админ:

- `/admin` — сводка
- `/addbal ID сумма`
- `/setrate ID процент [наставник]`
- `/archive ID` / `/archive ID off`
- `/task [ID] заголовок | описание`
- `/deal ID статус [сумма] [user_id]`
- `/credit ID_оценки`

## Marketplace webhook

```
POST /api/marketplace/deals
X-Marketplace-Secret: ...
{"id":"deal-1","status":"success","title":"...","amount":12.5,"user_id":123}
```
