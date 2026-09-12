from __future__ import annotations

from typing import Any, Optional

from aiogram import Bot

from database.crud import db
from database.models import Deal
from services.logger import notify_deal
from texts import money


async def ingest_deal(
    bot: Optional[Bot],
    *,
    external_id: str,
    status: str,
    title: str = "",
    amount: Optional[float] = None,
    user_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
) -> Deal:
    normalized = status.strip().lower()
    if normalized in {"ok", "done", "paid", "success", "успешно"}:
        normalized = "success"
    elif normalized in {"fail", "failed", "error", "ошибка"}:
        normalized = "error"
    elif normalized not in {"pending", "success", "error"}:
        normalized = "pending"

    deal = await db.upsert_deal(
        external_id=external_id.strip(),
        user_id=user_id,
        title=title.strip(),
        status=normalized,
        amount=amount,
        payload=payload,
    )
    await db.log_tx(
        user_id=user_id,
        kind="deal",
        title=f"Сделка {deal.external_id}: {deal.status}",
        amount=amount,
        payload={"external_id": deal.external_id, "status": deal.status},
    )
    if bot is not None:
        await notify_deal(
            bot,
            external_id=deal.external_id,
            status=deal.status,
            title=deal.title,
            amount=money(amount) if amount is not None else None,
            user_id=user_id,
        )
    return deal
