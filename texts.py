from __future__ import annotations

from html import escape
from typing import Any, Optional

from database import fmt_dt

HR = "━━━━━━━━━━━━━━━━━━━━"


def money(value: Any) -> str:
    return f"{float(value or 0):.2f}"


def money4(value: Any) -> str:
    return f"{float(value or 0):.4f}"


def pct(value: Any) -> str:
    n = float(value or 0)
    if n == int(n):
        return f"{int(n)}%"
    return f"{n:g}%"


def display_name(user: Any) -> str:
    username = _row(user, "username")
    if username:
        return f"@{escape(str(username))}"
    name = " ".join(p for p in (_row(user, "first_name"), _row(user, "last_name")) if p)
    return escape(name or str(_row(user, "tg_id") or "—"))


def _row(user: Any, key: str, default: Any = None) -> Any:
    if user is None:
        return default
    try:
        val = user[key]
    except (KeyError, IndexError, TypeError):
        val = getattr(user, key, default)
    return default if val is None else val


def header(title: str) -> str:
    return f"🔱 <b>Ural Team</b>\n{HR}\n<b>{title}</b>\n"


def profile_text(user: Any, mentor: str, paid: float, currency: str = "TON") -> str:
    name = escape(str(_row(user, "username") or _row(user, "first_name") or "—"))
    tag = escape(str(_row(user, "tag", "Аноним") or "Аноним").lstrip("#"))
    wallet = (str(_row(user, "wallet", "") or "")).strip()
    wallet_s = f"<code>{escape(wallet)}</code>" if wallet else "-"
    mentor_s = escape(mentor or "-")
    return (
        "👤 Твой профиль\n"
        f"<b>Telegram ID:</b> {_row(user, 'tg_id')}\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Тег:</b> #{tag}\n"
        f"<b>Наставник:</b> {mentor_s}\n"
        f"<b>Процент наставника:</b> {pct(_row(user, 'mentor_percent', 0))}\n"
        f"<b>Процент выплаты:</b> {pct(_row(user, 'payout_percent', 70))}\n"
        f"<b>Баланс:</b> {money(_row(user, 'balance', 0))} {escape(currency)}\n"
        f"<b>Кошелек:</b> {wallet_s}\n"
        f"<b>Общая сумма выплат:</b> {money4(paid)} {escape(currency)}"
    )


def cabinet_text(user: Any, tasks_n: int, reports_n: int, pending: float) -> str:
    return (
        f"{header('🏠 Личный кабинет')}"
        f"💎 Баланс: <b>{money(_row(user, 'balance', 0))} TON</b>\n"
        f"📊 Ставка: <b>{pct(_row(user, 'payout_percent', 70))}</b>\n"
        f"📋 Активных задач: <b>{tasks_n}</b>\n"
        f"📨 Отчётов: <b>{reports_n}</b>\n"
        f"⏳ В обработке: <b>{money(pending)} TON</b>\n"
        f"{HR}\n"
        "Выберите действие ниже."
    )


def tag_setup_text(current: str) -> str:
    tag = escape(str(current or "Аноним").lstrip("#"))
    return (
        "✎ Настройка тега\n\n"
        f"Текущий тег: <b>#{tag}</b>\n\n"
        "Отправьте новый тег одним сообщением."
    )


def about_text(raw: str, channel: str = "", chat: str = "") -> str:
    try:
        return raw.format(channel=escape(channel), chat=escape(chat))
    except (KeyError, ValueError, IndexError):
        return raw


def wallet_text() -> str:
    return "👛 Отправь кошелек одним сообщением."


def deposit_text(address: str, hint: str, network: str) -> str:
    if not address:
        return (
            "💳 <b>Пополнение</b>\n\n"
            "Адрес для пополнения ещё не указан.\n"
            "Напишите администратору."
        )
    return (
        "💳 <b>Пополнение</b>\n\n"
        f"🌐 Сеть: <b>{escape(network)}</b>\n"
        f"📍 Адрес:\n<code>{escape(address)}</code>\n\n"
        f"{escape(hint)}"
    )


def mentors_text(rows: Any) -> str:
    if not rows:
        return "👥 Наставники пока не добавлены."
    lines = ["👥 Наставники\n"]
    for m in rows:
        uname = str(m["username"]).lstrip("@")
        role = escape(str(m["role"] or "Наставник"))
        name = escape(str(m["name"]))
        lines.append(f"{role}: <a href=\"https://t.me/{uname}\">{name}</a> — @{escape(uname)}")
    return "\n".join(lines)


def leaderboard_menu_text() -> str:
    return "📈 Лидерборд\nВыбери период."


def leaderboard_text(rows: Any, period: str = "all") -> str:
    titles = {"day": "День", "week": "Неделя", "month": "Месяц", "all": "Все время"}
    title = titles.get(period, "Все время")
    if not rows:
        return f"📈 Лидерборд\nПериод: {title}\n\nПока нет данных."
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = [f"📈 Лидерборд\nПериод: {title}\n"]
    for i, u in enumerate(rows):
        medal = medals.get(i, f"{i + 1}.")
        tag = escape(str(_row(u, "tag", "Аноним") or "Аноним").lstrip("#"))
        total = _row(u, "total", 0) or 0
        lines.append(f"{medal} #{tag} — <b>{money(total)} TON</b>")
    return "\n".join(lines)


def payouts_deal_text() -> str:
    return "📄 Укажи номер сделки для выплаты."


def payouts_screens_text() -> str:
    return "📋 Загрузи от 2 до 10 скриншотов."


def fmt_min(value: Any) -> str:
    n = float(value or 0)
    if n == 0:
        return "0.00"
    text = f"{n:.4f}".rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.00"


def withdraw_text(balance: float, min_amount: float) -> str:
    return (
        "🏧 Вывод средств\n"
        f"На балансе: <b>{money(balance)} TON</b>\n"
        f"Минимум: {fmt_min(min_amount)} TON\n"
        "Напиши, сколько снять на свой кошелёк."
    )


def tasks_text(rows: Any) -> str:
    if not rows:
        return f"{header('📋 Задачи')}Активных задач нет."
    lines = [header("📋 Активные задачи")]
    for t in rows:
        status = escape(str(t["status"]))
        lines.append(f"#{t['id']} · <b>{escape(str(t['title']))}</b>\n   {status}")
        desc = (t["description"] or "").strip()
        if desc:
            lines.append(f"   <i>{escape(desc[:180])}</i>")
    return "\n".join(lines)


def reports_text(rows: Any) -> str:
    if not rows:
        return f"{header('📨 Отчёты')}Вы ещё не отправляли отчёты."
    lines = [header("📨 Статус отчётов")]
    for r in rows:
        stamp = fmt_dt(r["created_at"])
        lines.append(
            f"#{r['id']} · <code>{escape(str(r['deal_number']))}</code>\n"
            f"   {escape(str(r['status']))} · {stamp}"
        )
    return "\n".join(lines)


def nft_prompt_text() -> str:
    return (
        f"{header('🖼 Оценка NFT')}"
        "Пришлите URL предмета или коллекции:\n"
        "• Getgems\n"
        "• Tonkeeper\n"
        "• TON-адрес EQ/UQ\n\n"
        "Система снимет floor price и посчитает вашу долю по персональной ставке."
    )


def nft_result_text(result: Any) -> str:
    col = escape(result.collection or result.name or "коллекция")
    return (
        f"{header('🖼 Оценка NFT')}"
        f"🏷 {col}\n"
        f"📉 Floor: <b>{money4(result.floor_price)} TON</b>\n"
        f"📊 Ставка: <b>{pct(result.payout_rate)}</b>\n"
        f"💎 Доля воркера: <b>{money4(result.employee_share)} TON</b>\n"
        f"📦 Источник: {escape(result.source)}\n"
        f"{HR}\n"
        "Зачисление на баланс выполняет администратор."
    )


def staff_card_text(user: Any, *, paid: float = 0) -> str:
    uname = display_name(user)
    chat = "да" if _row(user, "in_main_chat") else "нет"
    wallet = (str(_row(user, "wallet", "") or "")).strip()
    wallet_s = f"<code>{escape(wallet)}</code>" if wallet else "не указан"
    status = str(_row(user, "status", "Воркер") or "Воркер")
    if status in {"Новый", "Кандидат", "Сотрудник"}:
        status = "Воркер"
    return (
        f"{header('👤 Воркер')}"
        f"{uname}\n"
        f"🆔 <code>{_row(user, 'tg_id')}</code>\n"
        f"📌 Статус: <b>{escape(status)}</b>\n"
        f"💬 В главном чате: <b>{chat}</b>\n"
        f"{HR}\n"
        f"📊 Ставка: <b>{pct(_row(user, 'payout_percent', 70))}</b>\n"
        f"👥 Наставник: <b>{pct(_row(user, 'mentor_percent', 0))}</b>\n"
        f"💎 Баланс: <b>{money(_row(user, 'balance', 0))} TON</b>\n"
        f"📤 Выплачено: <b>{money4(paid)} TON</b>\n"
        f"🏷 Тег: #{escape(str(_row(user, 'tag', 'Аноним') or 'Аноним').lstrip('#'))}\n"
        f"👛 {wallet_s}"
    )


def admin_home_text(stats: dict[str, Any], *, chat_title: str = "") -> str:
    return (
        f"{header('🛠 Панель администратора')}"
        f"👥 Воркеры: <b>{stats.get('users', 0)}</b>\n"
        f"🆕 Сегодня: {stats.get('today', 0)}\n"
        f"💎 На балансах: <b>{money(stats.get('balances', 0))} TON</b>\n"
        f"⏳ Выводов: {stats.get('pending', 0)}\n"
        f"✅ Выплачено: <b>{money(stats.get('paid', 0))} TON</b>\n"
        f"💬 Чат: <b>{escape(chat_title or 'не привязан')}</b>\n"
        f"{HR}\n"
        "Все кнопки рабочие. Наставников добавляйте в «Наставники» — "
        "они появятся воркерам в меню."
    )
