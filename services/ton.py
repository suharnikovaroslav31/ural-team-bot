from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx

from config import settings

log = logging.getLogger("ton_rpc")

NANO = Decimal("1000000000")


class TonRPCError(Exception):
    pass


@dataclass(slots=True)
class AccountInfo:
    address: str
    balance: Decimal
    status: str
    raw: dict[str, Any]


def mask_wallet(address: str) -> str:
    addr = (address or "").strip()
    if len(addr) <= 10:
        return addr or "—"
    return f"{addr[:6]}…{addr[-4:]}"


async def get_address_information(address: str) -> AccountInfo:
    params: dict[str, str] = {"address": address}
    if settings.toncenter_api_key:
        params["api_key"] = settings.toncenter_api_key
    url = settings.toncenter_url.rstrip("/") + "/getAddressInformation"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise TonRPCError(f"TON RPC недоступен: {exc}") from exc

    if not body.get("ok"):
        raise TonRPCError(str(body.get("error") or "TON RPC отклонил запрос"))
    result = body.get("result") or {}
    nano = Decimal(str(result.get("balance") or 0))
    return AccountInfo(
        address=address,
        balance=(nano / NANO).quantize(Decimal("0.000000001")),
        status=str(result.get("state") or "unknown"),
        raw=result,
    )


async def wallet_is_usable(address: str) -> tuple[bool, str]:
    try:
        info = await get_address_information(address)
    except TonRPCError as exc:
        return False, str(exc)
    if info.status in {"active", "uninitialized", "nonexist"}:
        return True, info.status
    return False, info.status
