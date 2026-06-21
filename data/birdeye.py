"""
Birdeye API Client
==================
Official documented API for Solana token analytics, trader rankings, and OHLCV data.
API key required (free tier available). Set BIRDEYE_API_KEY in .env.

Key capabilities:
  - Top trader rankings by P&L (powers EliteTraderAgent)
  - New token listings
  - Trending tokens
  - OHLCV data for strategy (better than DexScreener for Solana)
  - Token security scores

Docs: https://docs.birdeye.so
"""
import logging
import os
import requests

logger = logging.getLogger("jarvis.data.birdeye")

_BASE = "https://public-api.birdeye.so"
_TIMEOUT = 15


def _headers(chain: str = "solana") -> dict:
    key = os.getenv("BIRDEYE_API_KEY", "")
    if not key:
        logger.warning("BIRDEYE_API_KEY not set — Birdeye data unavailable")
    return {
        "X-API-KEY": key,
        "x-chain": chain,
        "Accept": "application/json",
    }


def _get(path: str, params: dict = None, chain: str = "solana") -> dict | None:
    key = os.getenv("BIRDEYE_API_KEY", "")
    if not key:
        return None
    try:
        r = requests.get(
            f"{_BASE}{path}",
            params=params or {},
            headers=_headers(chain),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Birdeye request failed [{path}]: {e}")
        return None


def get_top_traders(chain: str = "solana", limit: int = 20, period: str = "today") -> list:
    """
    Fetch top traders ranked by realized P&L.
    These are the EliteTraderAgent's signal sources — wallets consistently
    in the top percentile of daily/weekly returns.

    Returns list of trader dicts: address, pnl, win_rate, trade_count
    """
    data = _get(
        "/trader/gainers-losers",
        params={
            "type": period,
            "sort_by": "PnL",
            "sort_type": "desc",
            "offset": 0,
            "limit": limit,
        },
        chain=chain,
    )
    if not data:
        return []
    traders = (data.get("data") or {}).get("items") or []
    result = []
    for t in traders:
        addr = t.get("address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "pnl": _safe_float(t.get("pnl")),
            "trade_count": int(t.get("trade_count") or 0),
            "buy_count": int(t.get("buy_count") or 0),
            "sell_count": int(t.get("sell_count") or 0),
            "win_rate": _safe_float(t.get("win_rate")),
            "network": chain,
        })
    return result


def get_trending_tokens(chain: str = "solana", limit: int = 20) -> list:
    """
    Fetch trending tokens from Birdeye.
    Birdeye's trending often captures tokens slightly earlier than DexScreener
    because it weights smart money volume more heavily.
    """
    data = _get(
        "/defi/token_trending",
        params={
            "sort_by": "rank",
            "sort_type": "asc",
            "offset": 0,
            "limit": limit,
        },
        chain=chain,
    )
    if not data:
        return []
    tokens = (data.get("data") or {}).get("items") or data.get("data") or []
    result = []
    for t in tokens:
        addr = t.get("address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "symbol": t.get("symbol") or "",
            "name": t.get("name") or "",
            "price_usd": _safe_float(t.get("price")),
            "volume_24h": _safe_float(t.get("volume24h") or t.get("volume_24h")),
            "price_change_24h": _safe_float(t.get("priceChange24h") or t.get("price_change_24h")),
            "liquidity": _safe_float(t.get("liquidity")),
            "rank": t.get("rank"),
        })
    return result


def get_new_listings(chain: str = "solana", limit: int = 30) -> list:
    """
    Fetch newly listed tokens. Birdeye's new listing API is faster than
    DexScreener for Solana because Birdeye indexes directly from the DEX
    program events.
    """
    data = _get(
        "/defi/new_listing",
        params={"limit": limit},
        chain=chain,
    )
    if not data:
        return []
    tokens = data.get("data") or []
    result = []
    for t in tokens:
        addr = t.get("address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "symbol": t.get("symbol") or "",
            "name": t.get("name") or "",
            "listed_at": t.get("listTime") or t.get("list_time"),
            "liquidity": _safe_float(t.get("liquidity")),
            "price_usd": _safe_float(t.get("price")),
        })
    return result


def get_token_security(address: str, chain: str = "solana") -> dict:
    """
    Birdeye's token security check.  Provides a second opinion alongside GoPlus
    for contract risk assessment.  Includes top holder concentration which GoPlus
    does not always surface for Solana.
    """
    data = _get(
        "/defi/token_security",
        params={"address": address},
        chain=chain,
    )
    if not data:
        return {}
    d = data.get("data") or {}
    return {
        "top10_holder_pct": _safe_float(d.get("top10HolderPercent") or d.get("top10_holder_percent")),
        "creator_pct": _safe_float(d.get("creatorPercentage") or d.get("creator_percentage")),
        "owner_pct": _safe_float(d.get("ownerPercentage") or d.get("owner_percentage")),
        "is_mutable": d.get("mutableMetadata", False),
        "freeze_authority": d.get("freezeAuthority"),
        "mint_authority": d.get("mintAuthority"),
    }


def get_token_price(address: str, chain: str = "solana") -> float:
    """Current price of a token from Birdeye. Faster than DexScreener for Solana."""
    data = _get(
        "/defi/price",
        params={"address": address, "check_liquidity": 10},
        chain=chain,
    )
    if not data:
        return 0.0
    return _safe_float((data.get("data") or {}).get("value"))


def get_ohlcv(
    address: str,
    interval: str = "15m",
    chain: str = "solana",
    limit: int = 50,
) -> list:
    """
    OHLCV price history for a token.
    Used by StrategyAgent for momentum calculation (replaces DexScreener priceChange
    which only gives 1h/24h snapshots — OHLCV gives granular candle data).

    interval: 1m, 5m, 15m, 1H, 4H, 1D
    """
    import time
    now = int(time.time())
    intervals_seconds = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
    window = intervals_seconds.get(interval, 900)
    time_from = now - window * limit

    data = _get(
        "/defi/ohlcv",
        params={
            "address": address,
            "type": interval,
            "time_from": time_from,
            "time_to": now,
        },
        chain=chain,
    )
    if not data:
        return []
    items = (data.get("data") or {}).get("items") or []
    return [
        {
            "time": i.get("unixTime"),
            "open": _safe_float(i.get("o")),
            "high": _safe_float(i.get("h")),
            "low": _safe_float(i.get("l")),
            "close": _safe_float(i.get("c")),
            "volume": _safe_float(i.get("v")),
        }
        for i in items
    ]


def _safe_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0
