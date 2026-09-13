from __future__ import annotations

import logging
from html import escape
from typing import Any, Optional

from aiogram import Bot

from database.crud import db, fmt_dt
from database.models import Deal
from services.logger import notify_admins, notify_deal
from texts import money

log = logging.getLogger("marketplace")

# Этапы сделки в gg_sel (см. handlers/deals.py того бота).
STAGES: dict[str, str] = {
    "open": "Создана, ждёт вторую сторону",
    "active": "Стороны в сделке, ждём оплату",
    "paid": "Покупатель оплатил",
    "goods_sent": "Товар передан гаранту",
    "completed": "Успешно завершена",
    "cancelled": "Отменена",
}
FINAL_STAGES = {"completed", "cancelled"}
# Порядок этапов: событие «назад» пришло с опозданием, его игнорируем.
STAGE_ORDER = {"open": 0, "active": 1, "paid": 2, "goods_sent": 3, "cancelled": 4, "completed": 5}

DEAL_TYPES: dict[str, str] = {
    "gift": "Гифт",
    "channel": "Канал",
    "stars": "Stars",
    "nft": "NFT",
}

CURRENCY: dict[str, str] = {
    "ton": "TON",
    "card": "RUB",
    "rub": "RUB",
    "stars": "STARS",
    "usdt": "USDT",
    "usd": "USD",
    "eur": "EUR",
    "byn": "BYN",
    "kzt": "KZT",
}

STATUS_FLAG = {"success": "✅", "error": "❌", "pending": "⏳"}


async def ingest_deal(
    bot: Optional[Bot],
    *,
    external_id: str,
    status: str,
    title: str = "",
    amount: Optional[float] = None,
    user_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
    notify: bool = True,
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
    if bot is not None and notify:
        await notify_deal(
            bot,
            external_id=deal.external_id,
            status=deal.status,
            title=deal.title,
            amount=money(amount) if amount is not None else None,
            user_id=user_id,
        )
    return deal


def _int(value: Any) -> Optional[int]:
    text = str(value if value is not None else "").strip()
    if not text or not text.lstrip("-").isdigit():
        return None
    return int(text)


def _float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _person(raw: Any, flat_id: Any = None, flat_username: Any = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {
        "id": _int(data.get("id") or data.get("tg_id") or flat_id),
        "username": str(data.get("username") or flat_username or "").lstrip("@").strip()[:64],
        "name": str(data.get("name") or data.get("full_name") or "").strip()[:128],
    }


def _status_of(stage: str) -> str:
    if stage in {"completed", "success", "done"}:
        return "success"
    if stage in {"cancelled", "canceled", "failed", "error", "declined", "refunded"}:
        return "error"
    return "pending"


async def _owner_id(seller_id: Optional[int], buyer_id: Optional[int]) -> Optional[int]:
    """Сделку вешаем на того, кто есть в панели — это наш человек."""
    for candidate in (seller_id, buyer_id):
        if candidate and await db.user(candidate):
            return candidate
    return seller_id or buyer_id


async def ingest_external_deal(bot: Optional[Bot], body: dict[str, Any]) -> Deal:
    """Событие сделки из внешнего бота (gg_sel): сохранить и при финале уведомить."""
    code = str(body.get("id") or body.get("code") or body.get("external_id") or "").strip()[:64]
    if not code:
        raise ValueError("id required")

    stage = str(body.get("status") or body.get("stage") or "").strip().lower()
    event = str(body.get("event") or stage).strip().lower()[:32]
    if stage in {"created", "new"}:
        stage = "open"
    deal_type = str(body.get("deal_type") or body.get("type") or "").strip().lower()[:32]
    pay = str(body.get("pay_method") or body.get("pay") or "").strip().lower()[:16]
    amount = _float(body.get("amount"))
    seller = _person(body.get("seller"), body.get("seller_id"), body.get("seller_username"))
    buyer = _person(body.get("buyer"), body.get("buyer_id"), body.get("buyer_username"))

    prev = await db.deal_by_external(code)
    prev_stage = str((prev.payload or {}).get("stage") or "") if prev else ""
    new_rank = STAGE_ORDER.get(stage, -1)
    if prev is not None and STAGE_ORDER.get(prev_stage, -1) > new_rank >= 0:
        # Событие отстало от жизни: сделка уже дальше по этапам.
        log.info("Пропускаю устаревший этап %s по сделке %s", stage, code)
        return prev

    payload = {
        "source": str(body.get("source") or "gg_sel")[:32],
        "stage": stage,
        "event": event,
        "type": deal_type,
        "pay": pay,
        "amount": amount,
        "description": str(body.get("description") or "").strip()[:500],
        "seller": seller,
        "buyer": buyer,
        "actor_id": _int(body.get("actor_id")),
        "created_at": str(body.get("created_at") or "")[:32],
    }
    title = f"{source_label(payload['source'])} · {DEAL_TYPES.get(deal_type, deal_type or 'Сделка')}"
    deal = await ingest_deal(
        bot,
        external_id=code,
        status=_status_of(stage),
        title=title[:255],
        amount=amount,
        user_id=await _owner_id(seller["id"], buyer["id"]),
        payload=payload,
        notify=stage in FINAL_STAGES,
    )
    if bot is not None and stage in FINAL_STAGES and stage != prev_stage:
        await notify_admins(bot, deal_card(deal))
    return deal


def source_label(source: str) -> str:
    return {"gg_sel": "GG Sel", "ggsel": "GG Sel"}.get(source, source or "Внешний бот")


def _person_label(data: Any) -> str:
    person = data if isinstance(data, dict) else {}
    uid = person.get("id")
    username = str(person.get("username") or "").strip()
    name = str(person.get("name") or "").strip()
    head = f"@{escape(username)}" if username else escape(name or "—")
    return f"{head} · <code>{uid}</code>" if uid else head


def deal_amount(deal: Deal) -> str:
    data = deal.payload or {}
    if deal.amount is None:
        return "—"
    cur = CURRENCY.get(str(data.get("pay") or "").lower(), "")
    value = f"{float(deal.amount):g}"
    return f"{value} {cur}".strip()


def deal_line(deal: Deal) -> str:
    data = deal.payload or {}
    flag = STATUS_FLAG.get(deal.status, "⏳")
    stage = STAGES.get(str(data.get("stage") or ""), deal.status)
    return f"{flag} <code>{escape(deal.external_id)}</code> · {escape(stage)} · {deal_amount(deal)}"


def deal_card(deal: Deal) -> str:
    data = deal.payload or {}
    stage_raw = str(data.get("stage") or "")
    flag = STATUS_FLAG.get(deal.status, "⏳")
    description = str(data.get("description") or "").strip()
    lines = [
        f"{flag} <b>Сделка {escape(deal.external_id)}</b>",
        f"📌 {escape(STAGES.get(stage_raw, stage_raw or deal.status))}",
        f"📦 {escape(DEAL_TYPES.get(str(data.get('type') or ''), str(data.get('type') or '—')))}",
        f"💎 Сумма: <b>{deal_amount(deal)}</b>",
        f"👤 Продавец: {_person_label(data.get('seller'))}",
        f"👤 Покупатель: {_person_label(data.get('buyer'))}",
        f"💬 {escape(description)}" if description else "",
        f"🌐 {escape(source_label(str(data.get('source') or '')))}",
        f"🕒 {fmt_dt(deal.updated_at)}",
    ]
    return "\n".join(line for line in lines if line)
