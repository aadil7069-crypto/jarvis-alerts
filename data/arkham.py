"""
Arkham Intelligence Client
==========================
Arkham labels blockchain addresses with entity names — "Jump Trading",
"Wintermute", "Alameda", institutional funds, exchanges, and known individuals.

This enriches our WalletScore.label field: instead of "smart_degen" or "kol",
a wallet gets labeled "Jump Trading — Prop Desk" when Arkham knows it.

API key required. Get one at: https://intel.arkm.com/api
Set ARKHAM_API_KEY in your .env file.

If no key is set, all calls return {} silently (no errors logged).
"""
import logging
import os
import requests

logger = logging.getLogger("jarvis.data.arkham")

_BASE = "https://api.arkhamintelligence.com"
_TIMEOUT = 10


def _key() -> str:
    return os.getenv("ARKHAM_API_KEY", "")


def get_entity(address: str) -> dict:
    """
    Look up entity information for a blockchain address.

    Returns a dict with known fields, or {} if:
      - ARKHAM_API_KEY is not configured
      - Address is unknown to Arkham (404)
      - Any network or API error

    Successful response includes:
      name:     "Jump Trading" / "Wintermute" / etc.
      type:     "exchange" | "fund" | "individual" | "dao" | "team" | ...
      website:  URL or None
      twitter:  handle or None
      label:    more specific label ("Jump Trading - Market Maker")
    """
    key = _key()
    if not key:
        return {}

    try:
        r = requests.get(
            f"{_BASE}/intelligence/address/{address}",
            headers={"API-Key": key, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 404:
            return {}   # unknown address — not an error
        r.raise_for_status()

        data = r.json()
        entity = data.get("arkhamEntity") or {}
        label_obj = data.get("arkhamLabel") or {}

        if not entity and not label_obj:
            return {}

        name = entity.get("name") or label_obj.get("name")
        return {
            "name": name,
            "type": entity.get("type"),
            "website": entity.get("website"),
            "twitter": entity.get("twitter"),
            "label": label_obj.get("name") or name,
        }

    except Exception as e:
        logger.debug(f"Arkham lookup failed [{address[:12]}]: {e}")
        return {}


def get_entity_label(address: str) -> str | None:
    """
    Convenience wrapper — returns the best available label string or None.
    Priority: specific label > entity name > None.
    """
    info = get_entity(address)
    return info.get("label") or info.get("name")
