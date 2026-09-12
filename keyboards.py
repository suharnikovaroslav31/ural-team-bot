from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from emoji import icon


def about_kb(chat_url: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    link = _chat_url(chat_url)
    if link:
        kb.button(text="Войти в чат", url=link, icon_custom_emoji_id=icon("sparkle"))
    kb.button(text="Назад", callback_data="home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def _chat_url(raw: str) -> str | None:
    url = (raw or "").strip()
    if not url or url in {"—", "-"}:
        return None
    if url.startswith("@"):
        return f"https://t.me/{url[1:]}"
    if url.startswith("t.me/"):
        return f"https://{url}"
    if url.startswith(("https://", "http://", "tg://")):
        return url
    if url.replace("_", "").isalnum() and 5 <= len(url) <= 32:
        return f"https://t.me/{url}"
    return None


def main_kb(*, admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Профиль", callback_data="profile", icon_custom_emoji_id=icon("person"))
    kb.button(text="О проекте", callback_data="about", icon_custom_emoji_id=icon("question"))
    kb.button(text="Кошелек", callback_data="wallet", icon_custom_emoji_id=icon("wallet"))
    kb.button(text="Наставники", callback_data="mentors", icon_custom_emoji_id=icon("mentor"))
    kb.button(text="Лидерборд", callback_data="leaderboard", icon_custom_emoji_id=icon("chart"))
    kb.button(text="Выплаты", callback_data="payouts", icon_custom_emoji_id=icon("money"))
    kb.button(text="Вывод", callback_data="withdraw", icon_custom_emoji_id=icon("ton"))
    if admin:
        kb.button(text="Админ", callback_data="admin_home", icon_custom_emoji_id=icon("shield"))
        kb.adjust(2, 2, 2, 1, 1)
    else:
        kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def employee_kb() -> InlineKeyboardMarkup:
    return main_kb()


def admin_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Воркеры", callback_data="aul:all", icon_custom_emoji_id=icon("people"))
    kb.button(text="Отчёты", callback_data="admin_reports", icon_custom_emoji_id=icon("report"))
    kb.button(text="Выводы", callback_data="admin_payouts", icon_custom_emoji_id=icon("money"))
    kb.button(text="Наставники", callback_data="adm:mentors", icon_custom_emoji_id=icon("mentor"))
    kb.button(text="Настройки", callback_data="adm:settings", icon_custom_emoji_id=icon("pen"))
    kb.button(text="Рассылка", callback_data="adm:bc", icon_custom_emoji_id=icon("megaphone"))
    kb.button(text="Кошелёк выплат", callback_data="adm:wallet", icon_custom_emoji_id=icon("card"))
    kb.button(text="Синхр. чат", callback_data="adm:sync", icon_custom_emoji_id=icon("lightning"))
    kb.button(text="В бот", callback_data="home", icon_custom_emoji_id=icon("back"))
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def admin_users_kb(rows, kind: str = "all") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Найти", callback_data="adm:find", icon_custom_emoji_id=icon("question"))
    kb.button(text="Обновить", callback_data="aul:sync", icon_custom_emoji_id=icon("lightning"))
    shown = rows[:30]
    for u in shown:
        name = u.username or u.first_name or str(u.tg_id)
        kb.button(
            text=str(name)[:64],
            callback_data=f"au:{u.tg_id}",
            icon_custom_emoji_id=icon("person"),
        )
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    kb.adjust(2, *([1] * len(shown)), 1)
    return kb.as_markup()


def admin_staff_kb(tg_id: int, *, hired: bool = True, banned: bool = False, archived: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Ставка", callback_data=f"as:payout:{tg_id}", icon_custom_emoji_id=icon("chart"))
    kb.button(text="Наставник %", callback_data=f"as:mentor:{tg_id}", icon_custom_emoji_id=icon("people"))
    kb.button(text="Баланс", callback_data=f"as:balance:{tg_id}", icon_custom_emoji_id=icon("ton"))
    kb.button(text="Кошелёк", callback_data=f"as:wallet:{tg_id}", icon_custom_emoji_id=icon("wallet"))
    kb.button(text="Тег", callback_data=f"as:tag:{tg_id}", icon_custom_emoji_id=icon("pen"))
    kb.button(text="Наставник", callback_data=f"as:ref:{tg_id}", icon_custom_emoji_id=icon("link"))
    kb.button(
        text="Разбан" if banned else "Бан",
        callback_data=f"as:ban:{tg_id}",
        icon_custom_emoji_id=icon("ok" if banned else "warn"),
    )
    kb.button(
        text="Из архива" if archived else "Архив",
        callback_data=f"as:arch:{tg_id}",
        icon_custom_emoji_id=icon("list"),
    )
    kb.button(text="К списку", callback_data="aul:all", icon_custom_emoji_id=icon("back"))
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Настроить тег", callback_data="set_tag", icon_custom_emoji_id=icon("pen"))
    kb.button(text="Назад", callback_data="home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def tag_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data="profile", icon_custom_emoji_id=icon("back"))
    return kb.as_markup()


def leaderboard_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="День", callback_data="lb:day", icon_custom_emoji_id=icon("one"))
    kb.button(text="Неделя", callback_data="lb:week", icon_custom_emoji_id=icon("two"))
    kb.button(text="Месяц", callback_data="lb:month", icon_custom_emoji_id=icon("three"))
    kb.button(text="Всё время", callback_data="lb:all", icon_custom_emoji_id=icon("four"))
    kb.button(text="Назад", callback_data="home", icon_custom_emoji_id=icon("back"))
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def leaderboard_result_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data="leaderboard", icon_custom_emoji_id=icon("back"))
    return kb.as_markup()


def back_kb(*extra: tuple[str, str], home: str = "home") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for text, data in extra:
        kb.button(text=text, callback_data=data)
    kb.button(text="Назад", callback_data=home, icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def home_back_kb() -> InlineKeyboardMarkup:
    return back_kb()


def cabinet_back_kb() -> InlineKeyboardMarkup:
    return back_kb(home="admin_home")


def admin_payout_wallet_kb(*, connected: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if connected:
        kb.button(text="Как в Tonkeeper: W5", callback_data="adm:wver:w5", icon_custom_emoji_id=icon("ok"))
        kb.button(text="Как в Tonkeeper: v4", callback_data="adm:wver:v4r2", icon_custom_emoji_id=icon("ok"))
        kb.button(text="Сменить Tonkeeper", callback_data="adm:wallet:set", icon_custom_emoji_id=icon("lightning"))
        kb.button(text="Отключить", callback_data="adm:wallet:off", icon_custom_emoji_id=icon("no"))
    else:
        kb.button(text="Подключить Tonkeeper", callback_data="adm:wallet:set", icon_custom_emoji_id=icon("link"))
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def wallet_kb() -> InlineKeyboardMarkup:
    return home_back_kb()


def withdraw_kb(can_create: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if can_create:
        kb.button(text="Создать заявку", callback_data="wd_create", icon_custom_emoji_id=icon("ok"))
    kb.button(text="Назад", callback_data="home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def deposit_kb(has_address: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_address:
        kb.button(text="Я оплатил", callback_data="deposit_done", icon_custom_emoji_id=icon("ok"))
    kb.button(text="Назад", callback_data="wallet", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="wd_cancel", icon_custom_emoji_id=icon("no"))
    return kb.as_markup()


def admin_wd_kb(wd_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отправить TON", callback_data=f"awd:ok:{wd_id}", icon_custom_emoji_id=icon("lightning"))
    kb.button(text="Отклонить", callback_data=f"awd:no:{wd_id}", icon_custom_emoji_id=icon("no"))
    kb.adjust(2)
    return kb.as_markup()


def admin_report_kb(report_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Начислить на баланс", callback_data=f"arp:bal:{report_id}", icon_custom_emoji_id=icon("ton"))
    kb.button(text="Отклонить", callback_data=f"arp:no:{report_id}", icon_custom_emoji_id=icon("no"))
    kb.adjust(1)
    return kb.as_markup()


def reports_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Новый отчёт", callback_data="payouts", icon_custom_emoji_id=icon("report"))
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1)
    return kb.as_markup()


def admin_payouts_kb(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for w in rows[:8]:
        kb.button(
            text=f"#{w.id} отправить",
            callback_data=f"awd:ok:{w.id}",
            icon_custom_emoji_id=icon("lightning"),
        )
        kb.button(text=f"#{w.id}", callback_data=f"awd:no:{w.id}", icon_custom_emoji_id=icon("no"))
    kb.button(text="Обновить", callback_data="admin_payouts", icon_custom_emoji_id=icon("lightning"))
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    n = min(len(rows), 8)
    kb.adjust(*([2] * n), 2)
    return kb.as_markup()


def admin_mentors_kb(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Добавить", callback_data="adm:madd", icon_custom_emoji_id=icon("plus"))
    for m in rows[:20]:
        mid = int(getattr(m, "id", 0) or 0)
        name = str(getattr(m, "name", "") or mid)
        kb.button(text=name[:64], callback_data=f"adm:mdel:{mid}", icon_custom_emoji_id=icon("no"))
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    kb.adjust(1, *([1] * min(len(rows), 20)), 1)
    return kb.as_markup()


def admin_settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Приветствие", callback_data="adm:set:welcome", icon_custom_emoji_id=icon("sparkle"))
    kb.button(text="О проекте", callback_data="adm:set:about", icon_custom_emoji_id=icon("question"))
    kb.button(text="Ссылка на чат", callback_data="adm:set:chat", icon_custom_emoji_id=icon("chat"))
    kb.button(text="Канал", callback_data="adm:set:channel", icon_custom_emoji_id=icon("channel"))
    kb.button(text="Мин. вывод", callback_data="adm:set:min_withdraw", icon_custom_emoji_id=icon("ton"))
    kb.button(text="Адрес депозита", callback_data="adm:set:deposit_address", icon_custom_emoji_id=icon("card"))
    kb.button(text="Подсказка депозита", callback_data="adm:set:deposit_hint", icon_custom_emoji_id=icon("report"))
    kb.button(text="Назад", callback_data="admin_home", icon_custom_emoji_id=icon("back"))
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()
