import logging
import os
import requests
from typing import Optional

logger = logging.getLogger("jarvis.data.goplus")

_BASE = "https://api.gopluslabs.io/api/v1"


def _headers() -> dict:
    key = os.getenv("GOPLUS_API_KEY", "")
    return {"Authorization": key} if key else {}


def _parse(result: dict) -> dict:
    """Normalise a GoPlus token result into a clean safety report."""
    return {
        "is_honeypot": result.get("is_honeypot") == "1",
        "buy_tax": float(result.get("buy_tax") or 0),
        "sell_tax": float(result.get("sell_tax") or 0),
        "is_mintable": result.get("is_mintable") == "1",
        "is_proxy": result.get("is_proxy") == "1",
        "owner_can_change_balance": result.get("owner_change_balance") == "1",
        "lp_locked": result.get("lp_locked") == "1",
        "is_open_source": result.get("is_open_source") == "1",
        "creator_address": result.get("creator_address", ""),
        "raw": result,
    }


def check_solana(address: str) -> Optional[dict]:
    try:
        r = requests.get(
            f"{_BASE}/solana/token_security",
            params={"contract_addresses": address},
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        result = r.json().get("result", {}).get(address.lower(), {})
        return _parse(result)
    except Exception as e:
        logger.error(f"GoPlus Solana check failed [{address}]: {e}")
        return None


def check_bsc(address: str) -> Optional[dict]:
    try:
        r = requests.get(
            f"{_BASE}/token_security/56",
            params={"contract_addresses": address},
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        result = r.json().get("result", {}).get(address.lower(), {})
        return _parse(result)
    except Exception as e:
        logger.error(f"GoPlus BSC check failed [{address}]: {e}")
        return None


def check_token(address: str, chain: str) -> Optional[dict]:
    """Unified entry point — routes to the correct chain checker."""
    chain = chain.lower()
    if chain == "solana":
        return check_solana(address)
    if chain in ("bnb", "bsc"):
        return check_bsc(address)
    logger.warning(f"GoPlus: unsupported chain '{chain}'")
    return None
