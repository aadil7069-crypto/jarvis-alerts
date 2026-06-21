import logging
import os
import requests

logger = logging.getLogger("jarvis.data.bscscan")

_API = "https://api.bscscan.com/api"


def _key() -> str:
    k = os.getenv("BSCSCAN_API_KEY", "")
    if not k:
        logger.warning("BSCSCAN_API_KEY not set — BNB on-chain data limited")
    return k or "YourApiKeyToken"


def get_token_transfers(address: str, limit: int = 100) -> list:
    """Get recent BEP-20 token transfers for a BSC address."""
    try:
        r = requests.get(
            _API,
            params={
                "module": "account",
                "action": "tokentx",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": limit,
                "sort": "desc",
                "apikey": _key(),
            },
            timeout=15,
        )
        r.raise_for_status()
        result = r.json()
        if result.get("status") == "1":
            return result.get("result", [])
        return []
    except Exception as e:
        logger.error(f"BSCScan transfer fetch failed [{address}]: {e}")
        return []


def get_bnb_balance(address: str) -> float:
    """Return the BNB balance of an address in BNB (not wei)."""
    try:
        r = requests.get(
            _API,
            params={"module": "account", "action": "balance", "address": address, "apikey": _key()},
            timeout=10,
        )
        r.raise_for_status()
        wei = int(r.json().get("result", 0))
        return wei / 1e18
    except Exception as e:
        logger.error(f"BSCScan balance fetch failed [{address}]: {e}")
        return 0.0
