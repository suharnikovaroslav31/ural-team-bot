from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from database.models import (
    CommissionRate,
    Deal,
    DealStatus,
    DepositTicket,
    Earning,
    Mentor,
    NFTValuation,
    PayoutFeed,
    PayoutRequest,
    PayoutStatus,
    Setting,
    Task,
    TaskReport,
    TaskStatus,
    TransactionLog,
    User,
    UserRole,
)
from database.session import session_scope

DEFAULTS: dict[str, str] = {
    "about": (
        "ⓘ <b>Ural Team</b>\n\n"
        "<b>Ural Team</b> — команда в сфере <b>NFT-ворка</b>.\n\n"
        "⚙️ Своя панель с выплатами.\n"
        "👥 Подготовленные OTC-боты для ворка.\n"
        "📊 Парсер для работы по маркету, китайцам и RU.\n"
        "💰 Работаем <b>70/30</b>, для топов в лидерборде повышаем ставку!\n\n"
        "🪄 Ссылка в чат действует на <b>1 вход</b>."
    ),
    "channel": "—",
    "chat": "—",
    "min_withdraw": "0.1",
    "currency": "TON",
    "network": "TON",
    "deposit_address": "",
    "deposit_hint": "Переведите средства на указанный адрес и нажмите «Я оплатил». Админ зачислит баланс вручную.",
    "welcome": "🏠 Ural Team",
    "main_chat_id": "0",
    "main_chat_title": "",
    "payouts_chat_id": "0",
    "payouts_topic_id": "0",
    "ton_wallet_mnemonic": "",
    "ton_wallet_version": "auto",
}


def now_utc() -> datetime:
    return datetime.utcnow()


def fmt_dt(value: Optional[datetime | str]) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        if " " in value:
            date, time = value.split(" ", 1)
            parts = date.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]} {time[:5]}"
        return str(value)


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


class Database:
    async def connect(self) -> None:
        from database.session import init_db

        await init_db()
        await self._seed_settings()

    async def close(self) -> None:
        from database.session import close_db

        await close_db()

    async def _seed_settings(self) -> None:
        async with session_scope() as session:
            for key, value in DEFAULTS.items():
                exists = await session.get(Setting, key)
                if exists is None:
                    session.add(Setting(key=key, value=value))
                elif key in {"welcome", "about"} and (
                    "Poltavsk" in (exists.value or "") or "полтавск" in (exists.value or "").lower()
                ):
                    exists.value = value

    async def setting(self, key: str, default: str = "") -> str:
        async with session_scope() as session:
            row = await session.get(Setting, key)
            if row:
                return row.value
        return DEFAULTS.get(key, default)

    async def settings_all(self) -> dict[str, str]:
        async with session_scope() as session:
            rows = (await session.execute(select(Setting))).scalars().all()
        data = dict(DEFAULTS)
        data.update({r.key: r.value for r in rows})
        return data

    async def set_setting(self, key: str, value: str) -> None:
        async with session_scope() as session:
            row = await session.get(Setting, key)
            if row:
                row.value = value
            else:
                session.add(Setting(key=key, value=value))

    async def upsert_user(
        self,
        tg_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        referrer_id: Optional[int] = None,
        *,
        as_admin: bool = False,
    ) -> bool:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                if as_admin:
                    user.role = UserRole.admin.value
                    user.status = "Админ"
                    user.is_employee = True
                else:
                    user.is_employee = True
                    if user.status in {"Новый", "Кандидат", "Сотрудник"}:
                        user.status = "Воркер"
                return False

            ref: Optional[int] = None
            if referrer_id and referrer_id != tg_id:
                if await session.get(User, referrer_id):
                    ref = referrer_id
            from config import settings

            user = User(
                tg_id=tg_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                referrer_id=ref,
                payout_percent=_dec(settings.default_payout_rate),
                status="Админ" if as_admin else "Воркер",
                role=UserRole.admin.value if as_admin else UserRole.employee.value,
                is_employee=True,
                registered_at=now_utc(),
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                user = await session.get(User, tg_id)
                if not user:
                    raise
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                user.is_employee = True
                if as_admin:
                    user.role = UserRole.admin.value
                    user.status = "Админ"
                elif user.status in {"Новый", "Кандидат", "Сотрудник"}:
                    user.status = "Воркер"
                return False
            return True

    async def user(self, tg_id: int) -> Optional[User]:
        async with session_scope() as session:
            return await session.get(User, tg_id)

    async def users(
        self,
        q: str = "",
        limit: int = 200,
        *,
        archived: Optional[bool] = None,
        employees: Optional[bool] = None,
        in_chat: Optional[bool] = None,
    ) -> list[User]:
        async with session_scope() as session:
            stmt: Select[tuple[User]] = select(User).order_by(User.registered_at.desc()).limit(limit)
            if archived is not None:
                stmt = stmt.where(User.is_archived.is_(archived))
            if employees is not None:
                stmt = stmt.where(User.is_employee.is_(employees))
            if in_chat is not None:
                stmt = stmt.where(User.in_main_chat.is_(in_chat))
            if q:
                like = f"%{q.strip().lstrip('@')}%"
                stmt = stmt.where(
                    or_(
                        cast(User.tg_id, String).like(like),
                        User.username.ilike(like),
                        User.first_name.ilike(like),
                    )
                )
            return list((await session.execute(stmt)).scalars().all())

    async def refs_count(self, tg_id: int) -> int:
        async with session_scope() as session:
            value = await session.scalar(
                select(func.count()).select_from(User).where(User.referrer_id == tg_id)
            )
            return int(value or 0)

    async def set_balance(self, tg_id: int, amount: float, as_earn: bool = True) -> None:
        delta = _dec(amount)
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if not user:
                raise ValueError("user_not_found")
            user.balance = _dec(user.balance) + delta
            if as_earn and delta > 0:
                user.earned = _dec(user.earned) + delta
                session.add(Earning(user_id=tg_id, amount=delta, created_at=now_utc()))
            session.add(
                TransactionLog(
                    user_id=tg_id,
                    kind="credit" if delta >= 0 else "debit",
                    amount=delta,
                    title="Ручная корректировка баланса",
                    payload={"as_earn": as_earn},
                    created_at=now_utc(),
                )
            )

    async def set_status(self, tg_id: int, status: str) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.status = status

    async def set_banned(self, tg_id: int, banned: bool) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.is_banned = banned

    async def set_archived(self, tg_id: int, archived: bool) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.is_archived = archived
                if archived:
                    user.status = "Архив"

    async def set_employee(self, tg_id: int, hired: bool) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if not user:
                return
            user.is_employee = True
            if hired:
                user.is_archived = False
                if user.status in {"Новый", "Кандидат", "Сотрудник", "Архив"}:
                    user.status = "Воркер"

    async def mark_in_chat(self, tg_id: int, value: bool = True) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.in_main_chat = value

    async def mark_left_except(self, present_ids: set[int]) -> int:
        async with session_scope() as session:
            rows = list(
                (await session.execute(select(User).where(User.in_main_chat.is_(True)))).scalars().all()
            )
            left = 0
            for user in rows:
                if user.tg_id not in present_ids:
                    user.in_main_chat = False
                    left += 1
            return left

    async def main_chat_id(self) -> int:
        raw = await self.setting("main_chat_id", "0")
        try:
            return int(raw)
        except ValueError:
            return 0

    async def bind_main_chat(self, chat_id: int, title: str, url: str = "", topic_id: int = 0) -> None:
        await self.set_setting("main_chat_id", str(chat_id))
        await self.set_setting("main_chat_title", title)
        if url:
            await self.set_setting("chat", url)
        if topic_id:
            await self.bind_payouts_topic(chat_id, topic_id)

    async def bind_payouts_topic(self, chat_id: int, topic_id: int) -> None:
        await self.set_setting("payouts_chat_id", str(chat_id))
        await self.set_setting("payouts_topic_id", str(topic_id))

    async def payouts_destination(self) -> tuple[int, int]:
        try:
            chat_id = int(await self.setting("payouts_chat_id", "0") or 0)
            topic_id = int(await self.setting("payouts_topic_id", "0") or 0)
        except ValueError:
            return 0, 0
        if not chat_id:
            chat_id = await self.main_chat_id()
        return chat_id, topic_id

    async def unbind_main_chat(self) -> None:
        await self.set_setting("main_chat_id", "0")
        await self.set_setting("main_chat_title", "")
        await self.set_setting("payouts_chat_id", "0")
        await self.set_setting("payouts_topic_id", "0")

    async def set_tag(self, tg_id: int, tag: str) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.tag = tag

    async def set_wallet(self, tg_id: int, wallet: str) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if user:
                user.wallet = wallet

    async def set_referrer(self, tg_id: int, referrer_id: Optional[int]) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if not user:
                return
            if referrer_id and referrer_id != tg_id and await session.get(User, referrer_id):
                user.referrer_id = referrer_id
            else:
                user.referrer_id = None

    async def set_percents(
        self,
        tg_id: int,
        mentor_percent: float,
        payout_percent: float,
        *,
        updated_by: Optional[int] = None,
        note: str = "",
    ) -> None:
        async with session_scope() as session:
            user = await session.get(User, tg_id)
            if not user:
                return
            user.mentor_percent = _dec(mentor_percent)
            user.payout_percent = _dec(payout_percent)
            session.add(
                CommissionRate(
                    user_id=tg_id,
                    payout_rate=_dec(payout_percent),
                    mentor_rate=_dec(mentor_percent),
                    note=note or None,
                    updated_by=updated_by,
                    created_at=now_utc(),
                )
            )

    async def mentor_label(self, referrer_id: Optional[int]) -> str:
        if not referrer_id:
            return "-"
        row = await self.user(referrer_id)
        if not row:
            return "-"
        return row.username or row.first_name or "-"

    async def pending_withdraw_sum(self, tg_id: int) -> float:
        async with session_scope() as session:
            value = await session.scalar(
                select(func.coalesce(func.sum(PayoutRequest.amount), 0)).where(
                    and_(
                        PayoutRequest.user_id == tg_id,
                        PayoutRequest.status == PayoutStatus.pending.value,
                    )
                )
            )
            return float(value or 0)

    async def paid_sum(self, tg_id: int) -> float:
        async with session_scope() as session:
            value = await session.scalar(
                select(func.coalesce(func.sum(PayoutRequest.amount), 0)).where(
                    and_(
                        PayoutRequest.user_id == tg_id,
                        PayoutRequest.status.in_(
                            [PayoutStatus.approved.value, PayoutStatus.paid.value]
                        ),
                    )
                )
            )
            return float(value or 0)

    async def create_withdraw(self, user_id: int, amount: float, address: str) -> int:
        value = _dec(amount)
        if value <= 0:
            raise ValueError("invalid_amount")
        async with session_scope() as session:
            user = await session.get(User, user_id)
            if not user:
                raise ValueError("user_not_found")
            if _dec(user.balance) < value:
                raise ValueError("insufficient")
            user.balance = _dec(user.balance) - value
            user.wallet = address
            req = PayoutRequest(
                user_id=user_id,
                amount=value,
                address=address,
                status=PayoutStatus.pending.value,
                created_at=now_utc(),
            )
            session.add(req)
            await session.flush()
            session.add(
                TransactionLog(
                    user_id=user_id,
                    kind="payout_request",
                    amount=value,
                    title=f"Заявка на выплату #{req.id}",
                    payload={"address": address},
                    created_at=now_utc(),
                )
            )
            return int(req.id)

    async def withdrawals(self, status: Optional[str] = None) -> list[PayoutRequest]:
        async with session_scope() as session:
            stmt = (
                select(PayoutRequest)
                .options(selectinload(PayoutRequest.user))
                .order_by(PayoutRequest.id.desc())
            )
            if status:
                stmt = stmt.where(PayoutRequest.status == status)
            else:
                stmt = stmt.limit(200)
            return list((await session.execute(stmt)).scalars().all())

    async def withdrawal(self, wd_id: int) -> Optional[PayoutRequest]:
        async with session_scope() as session:
            stmt = (
                select(PayoutRequest)
                .options(selectinload(PayoutRequest.user))
                .where(PayoutRequest.id == wd_id)
            )
            return (await session.execute(stmt)).scalars().first()

    async def finish_withdraw(
        self,
        wd_id: int,
        approve: bool,
        *,
        tx_hash: Optional[str] = None,
    ) -> Optional[PayoutRequest]:
        async with session_scope() as session:
            stmt = (
                select(PayoutRequest)
                .options(selectinload(PayoutRequest.user))
                .where(PayoutRequest.id == wd_id)
            )
            row = (await session.execute(stmt)).scalars().first()
            if not row or row.status != PayoutStatus.pending.value:
                return None
            row.status = PayoutStatus.paid.value if approve else PayoutStatus.rejected.value
            row.processed_at = now_utc()
            if tx_hash:
                row.tx_hash = tx_hash
            user = await session.get(User, row.user_id)
            if not approve and user:
                user.balance = _dec(user.balance) + _dec(row.amount)
            elif approve and user:
                uname = f"@{user.username}" if user.username else (user.first_name or str(user.tg_id))
                session.add(PayoutFeed(username=uname, amount=row.amount, created_at=now_utc()))
            session.add(
                TransactionLog(
                    user_id=row.user_id,
                    kind="payout_ok" if approve else "payout_reject",
                    amount=row.amount,
                    title=f"Выплата #{wd_id}",
                    payload={"tx_hash": tx_hash, "address": row.address},
                    created_at=now_utc(),
                )
            )
            await session.flush()
            return row

    async def set_withdraw_tx(self, wd_id: int, tx_hash: str) -> Optional[PayoutRequest]:
        async with session_scope() as session:
            stmt = (
                select(PayoutRequest)
                .options(selectinload(PayoutRequest.user))
                .where(PayoutRequest.id == wd_id)
            )
            row = (await session.execute(stmt)).scalars().first()
            if not row:
                return None
            row.tx_hash = tx_hash
            if row.status == PayoutStatus.pending.value:
                row.status = PayoutStatus.paid.value
                row.processed_at = now_utc()
            return row

    async def payouts(self, limit: int = 20) -> list[PayoutFeed]:
        async with session_scope() as session:
            stmt = select(PayoutFeed).order_by(PayoutFeed.id.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def add_payout(self, username: str, amount: float) -> None:
        async with session_scope() as session:
            session.add(PayoutFeed(username=username, amount=_dec(amount), created_at=now_utc()))

    async def delete_payout(self, pid: int) -> None:
        async with session_scope() as session:
            row = await session.get(PayoutFeed, pid)
            if row:
                await session.delete(row)

    async def add_deal_payout(self, user_id: int, deal_number: str, file_ids: list[str]) -> int:
        async with session_scope() as session:
            report = TaskReport(
                user_id=user_id,
                deal_number=deal_number,
                file_ids=file_ids,
                status="pending",
                created_at=now_utc(),
            )
            session.add(report)
            await session.flush()
            session.add(
                TransactionLog(
                    user_id=user_id,
                    kind="report",
                    title=f"Отчёт по задаче {deal_number}",
                    payload={"files": len(file_ids), "report_id": report.id},
                    created_at=now_utc(),
                )
            )
            return int(report.id)

    async def reports_for(self, user_id: int, limit: int = 20) -> list[TaskReport]:
        async with session_scope() as session:
            stmt = (
                select(TaskReport)
                .where(TaskReport.user_id == user_id)
                .order_by(TaskReport.id.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def leaderboard(self, period: str = "all", limit: int = 10) -> list[User]:
        async with session_scope() as session:
            if period == "all":
                stmt = (
                    select(User)
                    .where(and_(User.is_banned.is_(False), User.earned > 0, User.is_archived.is_(False)))
                    .order_by(User.earned.desc())
                    .limit(limit)
                )
                rows = list((await session.execute(stmt)).scalars().all())
                for row in rows:
                    setattr(row, "total", row.earned)
                return rows

            now = datetime.now()
            if period == "day":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif period == "week":
                start = (now - timedelta(days=now.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            stmt = (
                select(
                    User.tg_id,
                    User.tag,
                    User.username,
                    User.first_name,
                    func.sum(Earning.amount).label("total"),
                )
                .join(Earning, Earning.user_id == User.tg_id)
                .where(and_(User.is_banned.is_(False), Earning.created_at >= start))
                .group_by(User.tg_id)
                .having(func.sum(Earning.amount) > 0)
                .order_by(func.sum(Earning.amount).desc())
                .limit(limit)
            )
            result = []
            for tg_id, tag, username, first_name, total in (await session.execute(stmt)).all():
                user = User(
                    tg_id=tg_id,
                    tag=tag,
                    username=username,
                    first_name=first_name,
                )
                setattr(user, "total", total)
                result.append(user)
            return result

    async def mentors(self) -> list[Mentor]:
        async with session_scope() as session:
            stmt = select(Mentor).order_by(Mentor.sort_order.asc(), Mentor.id.asc())
            return list((await session.execute(stmt)).scalars().all())

    async def add_mentor(self, name: str, username: str, role: str, sort_order: int = 0) -> None:
        async with session_scope() as session:
            session.add(
                Mentor(name=name, username=username.lstrip("@"), role=role, sort_order=sort_order)
            )

    async def delete_mentor(self, mid: int) -> None:
        async with session_scope() as session:
            row = await session.get(Mentor, mid)
            if row:
                await session.delete(row)

    async def add_deposit_ticket(self, user_id: int) -> int:
        async with session_scope() as session:
            ticket = DepositTicket(user_id=user_id, created_at=now_utc())
            session.add(ticket)
            await session.flush()
            return int(ticket.id)

    async def stats(self) -> dict[str, Any]:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with session_scope() as session:
            users = int(
                await session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(and_(User.in_main_chat.is_(True), User.is_archived.is_(False)))
                )
                or 0
            )
            today_n = int(
                await session.scalar(
                    select(func.count()).select_from(User).where(User.registered_at >= today)
                )
                or 0
            )
            balances = await session.scalar(select(func.coalesce(func.sum(User.balance), 0)))
            pending = int(
                await session.scalar(
                    select(func.count())
                    .select_from(PayoutRequest)
                    .where(PayoutRequest.status == PayoutStatus.pending.value)
                )
                or 0
            )
            paid = await session.scalar(
                select(func.coalesce(func.sum(PayoutRequest.amount), 0)).where(
                    PayoutRequest.status.in_([PayoutStatus.approved.value, PayoutStatus.paid.value])
                )
            )
            pending_sum = await session.scalar(
                select(func.coalesce(func.sum(PayoutRequest.amount), 0)).where(
                    PayoutRequest.status == PayoutStatus.pending.value
                )
            )
            open_tasks = int(
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.status != TaskStatus.done.value)
                )
                or 0
            )
        return {
            "users": users,
            "today": today_n,
            "balances": float(balances or 0),
            "pending": pending,
            "paid": float(paid or 0),
            "pending_sum": float(pending_sum or 0),
            "open_tasks": open_tasks,
        }

    async def all_user_ids(self) -> list[int]:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(User.tg_id).where(and_(User.is_banned.is_(False), User.is_archived.is_(False)))
                )
            ).scalars().all()
            return [int(x) for x in rows]

    async def log_tx(
        self,
        *,
        user_id: Optional[int],
        kind: str,
        title: str,
        amount: Optional[float] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> TransactionLog:
        async with session_scope() as session:
            row = TransactionLog(
                user_id=user_id,
                kind=kind,
                amount=_dec(amount) if amount is not None else None,
                title=title,
                payload=payload,
                created_at=now_utc(),
            )
            session.add(row)
            await session.flush()
            return row

    async def logs(self, limit: int = 100, user_id: Optional[int] = None) -> list[TransactionLog]:
        async with session_scope() as session:
            stmt = select(TransactionLog).order_by(TransactionLog.id.desc()).limit(limit)
            if user_id:
                stmt = stmt.where(TransactionLog.user_id == user_id)
            return list((await session.execute(stmt)).scalars().all())

    async def save_valuation(
        self,
        *,
        user_id: int,
        url: str,
        collection: Optional[str],
        item_address: Optional[str],
        floor_price: Decimal,
        payout_rate: Decimal,
        employee_share: Decimal,
        total_value: Decimal,
    ) -> NFTValuation:
        async with session_scope() as session:
            row = NFTValuation(
                user_id=user_id,
                url=url,
                collection=collection,
                item_address=item_address,
                floor_price=floor_price,
                payout_rate=payout_rate,
                employee_share=employee_share,
                total_value=total_value,
                created_at=now_utc(),
            )
            session.add(row)
            await session.flush()
            return row

    async def valuations(self, limit: int = 100, user_id: Optional[int] = None) -> list[NFTValuation]:
        async with session_scope() as session:
            stmt = (
                select(NFTValuation)
                .options(selectinload(NFTValuation.user))
                .order_by(NFTValuation.id.desc())
                .limit(limit)
            )
            if user_id:
                stmt = stmt.where(NFTValuation.user_id == user_id)
            return list((await session.execute(stmt)).scalars().all())

    async def credit_valuation(self, vid: int) -> Optional[NFTValuation]:
        async with session_scope() as session:
            row = await session.get(NFTValuation, vid)
            if not row or row.credited:
                return row
            user = await session.get(User, row.user_id)
            if not user:
                return None
            user.balance = _dec(user.balance) + _dec(row.employee_share)
            user.earned = _dec(user.earned) + _dec(row.employee_share)
            row.credited = True
            session.add(Earning(user_id=user.tg_id, amount=row.employee_share, created_at=now_utc()))
            session.add(
                TransactionLog(
                    user_id=user.tg_id,
                    kind="valuation_credit",
                    amount=row.employee_share,
                    title=f"Зачисление оценки NFT #{vid}",
                    payload={"valuation_id": vid},
                    created_at=now_utc(),
                )
            )
            return row

    async def tasks(self, assigned_to: Optional[int] = None, limit: int = 50) -> list[Task]:
        async with session_scope() as session:
            stmt = select(Task).order_by(Task.id.desc()).limit(limit)
            if assigned_to is not None:
                stmt = stmt.where(
                    or_(Task.assigned_to == assigned_to, Task.assigned_to.is_(None))
                )
            return list((await session.execute(stmt)).scalars().all())

    async def add_task(self, title: str, description: str, assigned_to: Optional[int]) -> Task:
        async with session_scope() as session:
            task = Task(
                title=title,
                description=description,
                assigned_to=assigned_to,
                status=TaskStatus.open.value,
                created_at=now_utc(),
            )
            session.add(task)
            await session.flush()
            return task

    async def set_task_status(self, task_id: int, status: str) -> Optional[Task]:
        async with session_scope() as session:
            task = await session.get(Task, task_id)
            if task:
                task.status = status
            return task

    async def upsert_deal(
        self,
        *,
        external_id: str,
        user_id: Optional[int],
        title: str,
        status: str,
        amount: Optional[float] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Deal:
        async with session_scope() as session:
            existing = (
                await session.execute(select(Deal).where(Deal.external_id == external_id))
            ).scalars().first()
            if existing:
                existing.status = status
                existing.title = title or existing.title
                if user_id:
                    existing.user_id = user_id
                if amount is not None:
                    existing.amount = _dec(amount)
                if payload:
                    existing.payload = payload
                existing.updated_at = now_utc()
                return existing
            deal = Deal(
                external_id=external_id,
                user_id=user_id,
                title=title,
                status=status if status in {s.value for s in DealStatus} else DealStatus.pending.value,
                amount=_dec(amount) if amount is not None else None,
                payload=payload,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            session.add(deal)
            await session.flush()
            return deal

    async def deals(self, limit: int = 100) -> list[Deal]:
        async with session_scope() as session:
            stmt = select(Deal).order_by(Deal.id.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def deal(self, deal_id: int) -> Optional[Deal]:
        async with session_scope() as session:
            return await session.get(Deal, deal_id)

    async def deal_by_external(self, external_id: str) -> Optional[Deal]:
        async with session_scope() as session:
            stmt = select(Deal).where(Deal.external_id == external_id.strip())
            return (await session.execute(stmt)).scalars().first()

    async def report(self, report_id: int) -> Optional[TaskReport]:
        async with session_scope() as session:
            return await session.get(TaskReport, report_id)

    async def pending_reports(self, limit: int = 20) -> list[TaskReport]:
        async with session_scope() as session:
            stmt = (
                select(TaskReport)
                .where(TaskReport.status == "pending")
                .order_by(TaskReport.id.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def set_report_status(self, report_id: int, status: str) -> Optional[TaskReport]:
        async with session_scope() as session:
            row = await session.get(TaskReport, report_id)
            if row:
                row.status = status
            return row

    async def pay_report(self, report_id: int, amount: float, *, to_balance: bool = False) -> Optional[TaskReport]:
        value = _dec(amount)
        async with session_scope() as session:
            row = await session.get(TaskReport, report_id)
            if not row or row.status != "pending":
                return row
            row.status = "paid"
            row.amount = value
            user = await session.get(User, row.user_id)
            if user:
                user.earned = _dec(user.earned) + value
                if to_balance:
                    user.balance = _dec(user.balance) + value
                session.add(Earning(user_id=user.tg_id, amount=value, created_at=now_utc()))
            session.add(
                TransactionLog(
                    user_id=row.user_id,
                    kind="report_paid",
                    amount=value,
                    title=f"Выплата по отчёту #{report_id}",
                    payload={"deal": row.deal_number, "to_balance": to_balance},
                    created_at=now_utc(),
                )
            )
            return row


db = Database()
