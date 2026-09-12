from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from config import settings

log = logging.getLogger("nft_calc")

ADDR_RE = re.compile(r"(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}|0:[a-fA-F0-9]{64}")
NANO = Decimal("1000000000")


class NFTCalcError(Exception):
    """Raised when a floor price cannot be resolved."""


@dataclass(slots=True)
class ValuationResult:
    url: str
    item_address: Optional[str]
    collection: Optional[str]
    collection_address: Optional[str]
    floor_price: Decimal
    payout_rate: Decimal
    employee_share: Decimal
    total_value: Decimal
    source: str
    name: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "item_address": self.item_address,
            "collection": self.collection,
            "collection_address": self.collection_address,
            "floor_price": str(self.floor_price),
            "payout_rate": str(self.payout_rate),
            "employee_share": str(self.employee_share),
            "total_value": str(self.total_value),
            "source": self.source,
            "name": self.name,
        }


def parse_ton_address(raw: str) -> Optional[str]:
    match = ADDR_RE.search(raw or "")
    return match.group(0) if match else None


def parse_nft_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Return (item_address, collection_address) extracted from a marketplace URL."""
    text = (url or "").strip()
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.netloc or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    item: Optional[str] = None
    collection: Optional[str] = None

    if "getgems.io" in host or "tonkeeper.com" in host or "tonviewer.com" in host:
        if parts:
            if parts[0] in {"nft", "item"} and len(parts) >= 2:
                item = parse_ton_address(parts[1]) or parts[1]
            elif parts[0] == "collection" and len(parts) >= 2:
                collection = parse_ton_address(parts[1]) or parts[1]
                if len(parts) >= 3:
                    item = parse_ton_address(parts[2]) or parts[2]
            else:
                found = parse_ton_address(parsed.path)
                item = found
    else:
        found = parse_ton_address(text)
        if found:
            item = found
    return item, collection


def _nano_to_ton(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        nano = Decimal(str(value))
    except Exception:
        return None
    if nano > NANO:
        return (nano / NANO).quantize(Decimal("0.000000001"))
    return nano


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "UralTeamPanel/1.0"}
    if settings.ton_api_key:
        headers["Authorization"] = f"Bearer {settings.ton_api_key}"
    return headers


async def _tonapi_get(path: str) -> dict[str, Any]:
    url = settings.ton_api_base.rstrip("/") + path
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=_headers())
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise NFTCalcError("Некорректный ответ TON API")
        return data


async def _floor_from_sales(collection_address: str) -> Optional[Decimal]:
    try:
        data = await _tonapi_get(
            f"/v2/nfts/collections/{collection_address}/items?limit=100&offset=0"
        )
    except Exception as exc:
        log.warning("tonapi collection items failed: %s", exc)
        return None
    floors: list[Decimal] = []
    for item in data.get("nft_items") or []:
        sale = item.get("sale") or {}
        price = (sale.get("price") or {}) if isinstance(sale, dict) else {}
        value = price.get("value")
        parsed = _nano_to_ton(value)
        if parsed and parsed > 0:
            floors.append(parsed)
    return min(floors) if floors else None


async def _getgems_floor(address: str) -> Optional[tuple[Decimal, str]]:
    query = """
    query Floor($addr: String!) {
      nftCollectionByAddress(address: $addr) {
        name
        floorPrice
        floorPriceNanoton
      }
    }
    """
    payload = {"query": query, "variables": {"addr": address}}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                settings.getgems_api,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if response.status_code >= 400:
                return None
            body = response.json()
    except Exception as exc:
        log.warning("getgems graphql failed: %s", exc)
        return None
    collection = ((body or {}).get("data") or {}).get("nftCollectionByAddress") or {}
    name = str(collection.get("name") or "")
    for key in ("floorPrice", "floorPriceNanoton"):
        parsed = _nano_to_ton(collection.get(key))
        if parsed and parsed > 0:
            if key == "floorPrice" and parsed < Decimal("100000"):
                return parsed, name
            if key == "floorPriceNanoton":
                return parsed, name
            if parsed >= NANO:
                return parsed / NANO, name
            return parsed, name
    return None


async def evaluate(url: str, payout_rate: Decimal) -> ValuationResult:
    raw = (url or "").strip()
    if not raw:
        raise NFTCalcError("Пришлите ссылку на NFT или коллекцию Getgems / Tonkeeper")

    item_address, collection_address = parse_nft_url(raw)
    if not item_address and not collection_address:
        raise NFTCalcError("Не удалось извлечь TON-адрес из ссылки")

    name = ""
    collection_name: Optional[str] = None
    floor: Optional[Decimal] = None
    source = "tonapi"

    if item_address:
        try:
            nft = await _tonapi_get(f"/v2/nfts/{item_address}")
            meta = nft.get("metadata") or {}
            name = str(meta.get("name") or "")
            col = nft.get("collection") or {}
            collection_name = col.get("name")
            collection_address = collection_address or col.get("address")
            sale = nft.get("sale") or {}
            price = (sale.get("price") or {}) if isinstance(sale, dict) else {}
            listed = _nano_to_ton(price.get("value"))
            if listed:
                floor = listed
                source = "tonapi:listing"
        except httpx.HTTPStatusError as exc:
            log.warning("tonapi nft lookup %s: %s", item_address, exc)
        except Exception as exc:
            log.warning("tonapi nft error: %s", exc)

    if floor is None and collection_address:
        floor = await _floor_from_sales(collection_address)
        if floor:
            source = "tonapi:collection_floor"
        gem = await _getgems_floor(collection_address)
        if gem:
            gem_floor, gem_name = gem
            collection_name = collection_name or gem_name
            if floor is None or gem_floor < floor:
                floor = gem_floor
                source = "getgems"
            if not collection_name:
                try:
                    col = await _tonapi_get(f"/v2/nfts/collections/{collection_address}")
                    collection_name = (col.get("metadata") or {}).get("name") or collection_name
                except Exception:
                    pass

    if floor is None:
        raise NFTCalcError(
            "Floor price не найден. Проверьте ссылку или укажите TON API ключ в .env"
        )

    rate = payout_rate if payout_rate > 1 else payout_rate
    share = (floor * rate / Decimal("100")).quantize(Decimal("0.000000001"))
    return ValuationResult(
        url=raw,
        item_address=item_address,
        collection=collection_name,
        collection_address=collection_address,
        floor_price=floor,
        payout_rate=rate,
        employee_share=share,
        total_value=floor,
        source=source,
        name=name,
    )
