from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config import settings

log = logging.getLogger("ton_send")
_lock = asyncio.Lock()


class TonSendError(Exception):
    pass


@dataclass(slots=True)
class SendResult:
    tx_hash: str
    from_address: str


async def _mnemonic_source() -> str:
    try:
        from database.crud import db

        saved = (await db.setting("ton_wallet_mnemonic", "")).strip()
        if saved:
            return saved
    except Exception:
        pass
    return settings.ton_wallet_mnemonic.strip()


async def _version_source() -> str:
    try:
        from database.crud import db

        saved = (await db.setting("ton_wallet_version", "")).strip()
        if saved:
            return saved
    except Exception:
        pass
    return (settings.ton_wallet_version or "auto").strip()


async def payouts_enabled() -> bool:
    return bool(await _mnemonic_source()) and settings.ton_auto_withdraw


async def payouts_ready_error() -> str | None:
    if not await _mnemonic_source():
        return (
            "Кошелёк выплат не подключён.\n"
            "Админ → Кошелёк выплат → Подключить Tonkeeper и пришлите 24 слова в личку боту."
        )
    if not settings.ton_auto_withdraw:
        return "Автовыплаты выключены (TON_AUTO_WITHDRAW=false)."
    return None


def _mnemonic_words(raw: str) -> list[str]:
    words = [w.lower() for w in raw.replace("\n", " ").split() if w]
    if len(words) not in {12, 18, 24}:
        raise TonSendError("Нужно 12 или 24 секретных слова")
    return words


async def preview_payout_address(mnemonic: str, version: str = "auto") -> str:
    words = _mnemonic_words(mnemonic)
    try:
        return await _wallet_address(words, version)
    except TonSendError:
        raise
    except Exception as exc:
        raise TonSendError(f"Не удалось открыть кошелёк: {exc}") from exc


_VERSION_LABELS = {
    "w5": "W5 — новый Tonkeeper",
    "v4r2": "v4 — старый Tonkeeper",
    "wallet": "Telegram Wallet",
}


async def list_payout_addresses(mnemonic: str | None = None) -> list[tuple[str, str, str, str]]:
    """Return (version_key, label, UQ address, EQ address)."""
    raw = mnemonic if mnemonic is not None else await _mnemonic_source()
    words = _mnemonic_words(raw)
    api_key = settings.toncenter_api_key or None
    client = None
    pairs = [("w5", "WalletV5R1"), ("v4r2", "WalletV4R2")]
    if len(words) == 12:
        pairs = [("wallet", "WalletTg"), ("w5", "WalletV5R1"), ("v4r2", "WalletV4R2")]
    result: list[tuple[str, str, str, str]] = []
    try:
        from ton_core import NetworkGlobalID
        from tonutils.clients import ToncenterClient

        classes = _load_wallet_classes()
        client = ToncenterClient(network=NetworkGlobalID.MAINNET, api_key=api_key)
        if hasattr(client, "connect"):
            await client.connect()
        for key, cls_name in pairs:
            wallet_cls = classes.get(cls_name)
            if wallet_cls is None:
                continue
            wallet, *_ = wallet_cls.from_mnemonic(client, words)
            result.append(
                (
                    key,
                    _VERSION_LABELS[key],
                    _addr_str(wallet.address, bounceable=False),
                    _addr_str(wallet.address, bounceable=True),
                )
            )
        return result
    except ImportError:
        pass
    finally:
        await _close_client(client)

    try:
        from tonutils.client import ToncenterClient as OldClient
        from tonutils.wallet import WalletV4R2 as OldV4

        try:
            from tonutils.wallet import WalletV5R1 as OldV5
        except ImportError:
            OldV5 = None
        client = OldClient(is_testnet=False, api_key=api_key)
        old_map = [("v4r2", OldV4)]
        if OldV5:
            old_map.insert(0, ("w5", OldV5))
        for key, wallet_cls in old_map:
            if wallet_cls is None:
                continue
            wallet, *_ = wallet_cls.from_mnemonic(client, words)
            result.append(
                (
                    key,
                    _VERSION_LABELS[key],
                    _addr_str(wallet.address, bounceable=False),
                    _addr_str(wallet.address, bounceable=True),
                )
            )
        return result
    except Exception as exc:
        await _close_client(client)
        raise TonSendError(f"Не удалось посчитать адреса: {exc}") from exc
    finally:
        await _close_client(client)


async def payout_address() -> str:
    words = _mnemonic_words(await _mnemonic_source())
    try:
        return await _wallet_address(words)
    except Exception as exc:
        raise TonSendError(f"Не удалось открыть кошелёк: {exc}") from exc


def _human_ton_error(exc: Exception, from_address: str = "") -> str:
    text = str(exc)
    low = text.lower()
    addr = f"\nПополни в Tonkeeper именно этот адрес:\n{from_address}" if from_address else ""
    if "unpack account state" in low or "cannot apply external message" in low:
        return (
            "Кошелёк выплат пустой или ещё не активирован в сети. "
            "Либо в боте выбран не тот адрес (W5 / v4), не тот что в Tonkeeper."
            f"{addr}\n"
            "Кинь туда TON, подожди 15 секунд и повтори вывод."
        )
    if "seqno" in low or "exitcode: 33" in low:
        return "Сеть отклонила перевод (seqno). Подожди несколько секунд и повтори."
    if "not enough" in low or "insufficient" in low or "exitcode: -13" in low:
        return f"На кошельке выплат не хватает TON на сумму и комиссию.{addr}"
    return f"Сеть TON отклонила перевод: {text}"


async def send_ton(destination: str, amount: float, comment: str = "") -> SendResult:
    if amount <= 0:
        raise TonSendError("Сумма должна быть больше нуля")
    dest = (destination or "").strip()
    if not dest:
        raise TonSendError("Нет адреса получателя")
    words = _mnemonic_words(await _mnemonic_source())
    async with _lock:
        from_s = ""
        try:
            return await _transfer(words, dest, amount, comment)
        except TonSendError:
            raise
        except Exception as exc:
            log.exception("TON transfer failed")
            try:
                from_s = await payout_address()
            except Exception:
                from_s = ""
            raise TonSendError(_human_ton_error(exc, from_s)) from exc


async def _wallet_address(words: list[str], version: str | None = None) -> str:
    wallet, client = await _open_wallet(words, version)
    try:
        return _addr_str(wallet.address)
    finally:
        await _close_client(client)


async def _transfer(words: list[str], dest: str, amount: float, comment: str) -> SendResult:
    wallet, client = await _open_wallet(words)
    try:
        from_s = _addr_str(wallet.address)
        nano = _to_nano(amount)
        try:
            await wallet.refresh()
        except Exception:
            pass
        balance = int(getattr(wallet, "balance", 0) or 0)
        fee = 50_000_000
        if balance <= 0:
            raise TonSendError(
                "На кошельке выплат 0 TON — сеть его ещё не видит.\n"
                f"Пополни в Tonkeeper этот адрес:\n{from_s}\n"
                "Адрес должен совпасть с Tonkeeper (кнопки W5 / v4). "
                "Потом подожди 15 секунд и повтори."
            )
        if balance < nano + fee:
            have = balance / 1_000_000_000
            raise TonSendError(
                f"На кошельке выплат только {have:.4f} TON, нужно больше "
                f"(сумма вывода + ~0.05 TON комиссия).\n"
                f"Адрес:\n{from_s}"
            )
        msg = await wallet.transfer(destination=dest, amount=nano, body=comment or "Ural Team payout")
        tx_hash = ""
        if msg is not None:
            tx_hash = str(getattr(msg, "normalized_hash", None) or getattr(msg, "hash", "") or "")
        return SendResult(tx_hash=tx_hash or "sent", from_address=from_s)
    finally:
        await _close_client(client)


def _addr_str(addr, *, bounceable: bool = False) -> str:
    if hasattr(addr, "to_str"):
        try:
            return addr.to_str(is_bounceable=bounceable, is_url_safe=True)
        except TypeError:
            return addr.to_str(is_bounceable=bounceable)
    return str(addr)


def _to_nano(amount: float) -> int:
    try:
        from ton_core import to_nano

        return int(to_nano(amount))
    except Exception:
        return int(round(float(amount) * 1_000_000_000))


def _wallet_class_names(word_count: int, version: str) -> list[str]:
    version = (version or "auto").strip().lower()
    aliases = {
        "tg": "WalletTg",
        "wallet": "WalletTg",
        "telegram": "WalletTg",
        "wallettg": "WalletTg",
        "v5": "WalletV5R1",
        "v5r1": "WalletV5R1",
        "w5": "WalletV5R1",
        "v4": "WalletV4R2",
        "v4r2": "WalletV4R2",
    }
    if version in aliases:
        return [aliases[version]]
    if word_count == 12:
        return ["WalletTg", "WalletV5R1", "WalletV4R2"]
    return ["WalletV5R1", "WalletV4R2", "WalletTg"]


def _load_wallet_classes():
    from tonutils.contracts import WalletTg, WalletV4R2, WalletV5R1

    return {
        "WalletTg": WalletTg,
        "WalletV5R1": WalletV5R1,
        "WalletV4R2": WalletV4R2,
    }


async def _score_wallet(wallet) -> int:
    try:
        await wallet.refresh()
        score = 0
        if getattr(wallet, "is_active", False):
            score += 2
        if int(getattr(wallet, "balance", 0) or 0) > 0:
            score += 1
        return score
    except Exception:
        return 0


async def _open_wallet(words: list[str], version: str | None = None):
    api_key = settings.toncenter_api_key or None
    client = None
    ver = (version if version is not None else await _version_source()).strip().lower()

    try:
        from ton_core import NetworkGlobalID
        from tonutils.clients import ToncenterClient

        classes = _load_wallet_classes()
        client = ToncenterClient(network=NetworkGlobalID.MAINNET, api_key=api_key)
        if hasattr(client, "connect"):
            await client.connect()

        names = _wallet_class_names(len(words), ver)
        best = None
        best_score = -1
        for name in names:
            wallet_cls = classes[name]
            wallet, *_ = wallet_cls.from_mnemonic(client, words)
            if len(names) == 1:
                return wallet, client
            score = await _score_wallet(wallet)
            if score > best_score:
                best, best_score = wallet, score
            if score >= 2:
                break
        return best, client
    except ImportError:
        pass
    except Exception:
        await _close_client(client)
        raise

    try:
        from tonutils.client import ToncenterClient as OldClient
        from tonutils.wallet import WalletV4R2 as OldV4

        try:
            from tonutils.wallet import WalletV5R1 as OldV5
        except ImportError:
            OldV5 = None
        wallet_cls = OldV5 if ver in {"v5", "v5r1", "w5"} and OldV5 else OldV4
        client = OldClient(is_testnet=False, api_key=api_key)
        wallet, *_ = wallet_cls.from_mnemonic(client, words)
        return wallet, client
    except Exception:
        await _close_client(client)
        raise


async def _close_client(client) -> None:
    if client is None:
        return
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result
