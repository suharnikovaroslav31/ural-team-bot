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

`requirements.txt` — только то, что нужно боту. Веб-панель `panel.py` и PostgreSQL:
`pip install -r requirements-panel.txt`.

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

## Приём сделок из бота GG Sel

Бот панели поднимает HTTP-приёмник, если задана переменная `PORT` (на Bothost её
подставляет хостинг, когда у бота включён веб-интерфейс/домен). Сделки видны в
`/admin` → «Сделки», финал сделки прилетает админам в личку.

Настройка:

1. В Bothost у бота панели включить веб-интерфейс (домен), порт оставить как в
   настройках — приложение слушает `0.0.0.0:$PORT` само.
2. В панели задать `MARKETPLACE_SECRET` — любой длинный пароль.
3. В боте GG Sel задать `PANEL_API_URL` (домен панели) и `PANEL_API_SECRET`
   (тот же пароль).

Проверка домена: `GET https://<домен>/health` → `{"ok": true}`.

```
POST /api/deals
X-Api-Secret: <MARKETPLACE_SECRET>
{
  "source": "gg_sel",
  "event": "completed",
  "id": "abc123",
  "status": "completed",
  "deal_type": "gift",
  "pay_method": "ton",
  "amount": 12.5,
  "description": "https://t.me/nft/PlushPepe-111",
  "seller": {"id": 111, "username": "seller", "name": "Seller"},
  "buyer": {"id": 222, "username": "buyer", "name": "Buyer"}
}
```

Этапы: `open` → `active` → `paid` → `goods_sent` → `completed`, отдельно
`cancelled`. Опоздавшие события с прошлым этапом панель игнорирует.
Старый путь `/api/marketplace/deals` с `X-Marketplace-Secret` тоже работает.
