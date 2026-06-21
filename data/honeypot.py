import logging
import requests
from typing import Optional

logger = logging.getLogger("jarvis.data.honeypot")


def check_bsc(address: str) -> Optional[dict]:
    """Check whether a BSC token is a honeypot via Honeypot.is (free, no key needed)."""
    try:
        r = requests.get(
            f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID=56",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        sim = data.get("simulationResult") or {}
        return {
            "is_honeypot": data.get("isHoneypot", False),
            "reason": data.get("honeypotReason", ""),
            "buy_tax": sim.get("buyTax", 0),
            "sell_tax": sim.get("sellTax", 0),
            "transfer_tax": sim.get("transferTax", 0),
        }
    except Exception as e:
        logger.error(f"Honeypot.is check failed [{address}]: {e}")
        return None
