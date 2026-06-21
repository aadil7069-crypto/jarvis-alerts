import logging
import requests
from typing import Optional

logger = logging.getLogger("jarvis.data.dexscreener")

_BASE = "https://api.dexscreener.com"


def get_token(address: str) -> Optional[dict]:
    """Return the highest-liquidity pair for a token address."""
    try:
        r = requests.get(f"{_BASE}/latest/dex/tokens/{address}", timeout=10)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        return max(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0)
    except Exception as e:
        logger.error(f"DexScreener token fetch failed [{address}]: {e}")
        return None


def get_new_tokens(chain: str = "solana") -> list:
    """Return recently launched token profiles on a given chain."""
    try:
        r = requests.get(f"{_BASE}/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        return [t for t in r.json() if t.get("chainId", "").lower() == chain.lower()]
    except Exception as e:
        logger.error(f"DexScreener new tokens fetch failed [{chain}]: {e}")
        return []


def get_trending_tokens(chain: str = "solana") -> list:
    """Return currently boosted/trending tokens on a given chain."""
    try:
        r = requests.get(f"{_BASE}/token-boosts/latest/v1", timeout=10)
        r.raise_for_status()
        return [t for t in r.json() if t.get("chainId", "").lower() == chain.lower()]
    except Exception as e:
        logger.error(f"DexScreener trending fetch failed [{chain}]: {e}")
        return []


def extract_token_info(pair: dict) -> dict:
    """Pull the most useful fields out of a DexScreener pair object."""
    base = pair.get("baseToken", {})
    return {
        "address": base.get("address", ""),
        "symbol": base.get("symbol", ""),
        "name": base.get("name", ""),
        "price_usd": float(pair.get("priceUsd") or 0),
        "liquidity_usd": (pair.get("liquidity") or {}).get("usd") or 0,
        "volume_24h": (pair.get("volume") or {}).get("h24") or 0,
        "price_change_5m": (pair.get("priceChange") or {}).get("m5") or 0,
        "price_change_1h": (pair.get("priceChange") or {}).get("h1") or 0,
        "price_change_24h": (pair.get("priceChange") or {}).get("h24") or 0,
        "buys_24h": (pair.get("txns") or {}).get("h24", {}).get("buys") or 0,
        "sells_24h": (pair.get("txns") or {}).get("h24", {}).get("sells") or 0,
        "pair_created_at_ms": pair.get("pairCreatedAt"),
        "chain": pair.get("chainId", ""),
        "dex": pair.get("dexId", ""),
    }
