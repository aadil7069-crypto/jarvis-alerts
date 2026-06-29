"""
GMGN Intelligence Client
========================
GMGN (gmgn.ai) is the leading smart money intelligence platform for Solana memecoins.
It automatically identifies profitable wallets using on-chain P&L data and tags them
(smart_degen, kol, sniper, etc.).

This client uses GMGN's public API endpoints. No API key required.

Key capabilities:
  - Auto-discover smart money wallets (solves the empty wallets.py problem)
  - See which smart money wallets are buying a specific token right now
  - Fetch wallet P&L stats to compute quality scores
  - Get trending tokens with smart money activity annotations
"""
import logging
import requests

logger = logging.getLogger("jarvis.data.gmgn")

_BASE = "https://gmgn.ai/defi/quotation/v1"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}
_TIMEOUT = 15

# GMGN uses short chain names
_CHAIN_MAP = {"solana": "sol", "bnb": "bsc", "sol": "sol", "bsc": "bsc"}


def _chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), "sol")


def get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list:
    """
    Fetch the top smart money wallets ranked by 7-day realized profit.
    Tries multiple endpoint patterns since GMGN changes their API frequently.
    """
    c = _chain(chain)
    candidates = [
        (f"{_BASE}/rank/{c}/wallets/7d", {"orderby": "pnl", "direction": "desc", "limit": limit}),
        (f"{_BASE}/smartmoney/{c}/wallets", {"orderby": "realized_profit", "direction": "desc", "period": "7d", "limit": limit}),
        (f"{_BASE}/rank/{c}/wallets/30d", {"orderby": "pnl", "direction": "desc", "limit": limit}),
    ]
    for url, params in candidates:
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            wallets = (
                data.get("data", {}).get("wallets")
                or data.get("data", {}).get("rank")
                or data.get("data")
                or []
            )
            if wallets:
                logger.debug(f"GMGN wallet list loaded from {url} ({len(wallets)} wallets)")
                return _parse_wallets(wallets)
        except Exception as e:
            logger.debug(f"GMGN wallet endpoint {url} failed: {e}")
    logger.warning(f"GMGN smart money wallets unavailable [{chain}] — all endpoints failed")
    return []


def get_token_smart_money_activity(token_address: str, chain: str = "sol", limit: int = 30) -> list:
    """
    For a given token, return recent buys from smart money wallets.

    This is the core signal: smart money actively buying a specific token on our watchlist.

    Returns list of buy dicts:
      wallet_address, wallet_label, amount_usd, timestamp, tx_signature, profit_1d
    """
    c = _chain(chain)
    try:
        r = requests.get(
            f"{_BASE}/tokens/{c}/{token_address}/top_traders",
            params={
                "orderby": "profit",
                "direction": "desc",
                "limit": limit,
                "tag[]": ["smart_degen", "kol"],
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        traders = data.get("data") or []
        return _parse_token_traders(traders)
    except Exception as e:
        logger.error(f"GMGN token smart money activity failed [{token_address[:12]}]: {e}")
        return []


def get_wallet_stats(address: str, chain: str = "sol", period: str = "7d") -> dict:
    """
    Fetch detailed P&L stats for a single wallet.

    Used to compute a quality score for WalletScore records.
    Returns: realized_pnl, unrealized_pnl, win_rate, trade_count, avg_trade_size_usd
    """
    c = _chain(chain)
    try:
        r = requests.get(
            f"{_BASE}/wallet_stat/{c}/{address}",
            params={"period": period},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        return {
            "realized_pnl": _safe_float(data.get("realized_profit")),
            "unrealized_pnl": _safe_float(data.get("unrealized_profit")),
            "win_rate": _safe_float(data.get("winrate")),
            "trade_count": int(data.get("buy_30d") or data.get("txs_30d") or 0),
            "avg_trade_size_usd": _safe_float(data.get("avg_cost")),
            "wallet_label": data.get("tag") or data.get("wallet_tag"),
            "last_active": data.get("last_active_timestamp"),
        }
    except Exception as e:
        logger.error(f"GMGN wallet stats failed [{address[:12]}]: {e}")
        return {}


def get_trending_tokens(chain: str = "sol", limit: int = 20) -> list:
    """
    Fetch trending tokens from GMGN — typically surfaces memecoins earlier
    than DexScreener because GMGN weighs smart money activity heavily.

    Returns list of token dicts: address, symbol, price_usd, volume_1h, smart_money_buys_1h
    """
    c = _chain(chain)
    try:
        r = requests.get(
            f"{_BASE}/tokens/{c}/trending",
            params={"limit": limit},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        tokens = r.json().get("data") or []
        return _parse_trending(tokens)
    except Exception as e:
        logger.error(f"GMGN trending tokens failed [{chain}]: {e}")
        return []


def get_new_pairs(chain: str = "sol", limit: int = 50) -> list:
    """
    Fetch newly launched token pairs. GMGN shows these with smart money
    participation data attached, making it superior to DexScreener for
    identifying early smart money entries.
    """
    c = _chain(chain)
    try:
        r = requests.get(
            f"{_BASE}/tokens/{c}/new_pair",
            params={
                "limit": limit,
                "orderby": "open_timestamp",
                "direction": "desc",
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        tokens = r.json().get("data") or []
        return _parse_new_pairs(tokens)
    except Exception as e:
        logger.error(f"GMGN new pairs failed [{chain}]: {e}")
        return []


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_wallets(raw: list) -> list:
    result = []
    for w in raw:
        addr = w.get("wallet_address") or w.get("address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "realized_pnl_7d": _safe_float(w.get("realized_profit") or w.get("realized_profit_7d")),
            "unrealized_pnl": _safe_float(w.get("unrealized_profit")),
            "win_rate": _safe_float(w.get("winrate") or w.get("win_rate")),
            "trade_count": int(w.get("buy_30d") or w.get("trade_count") or 0),
            "avg_trade_size_usd": _safe_float(w.get("avg_cost") or w.get("avg_trade_size")),
            "wallet_label": w.get("tag") or w.get("wallet_tag") or "smart_degen",
            "last_active": w.get("last_active_timestamp"),
        })
    return result


def _parse_token_traders(raw: list) -> list:
    result = []
    for t in raw:
        addr = t.get("wallet_address") or t.get("address")
        if not addr:
            continue
        result.append({
            "wallet_address": addr,
            "wallet_label": t.get("tag") or t.get("wallet_tag") or "unknown",
            "realized_profit": _safe_float(t.get("realized_profit")),
            "buy_amount_usd": _safe_float(t.get("buy_amount_cur") or t.get("buy_volume_cur")),
            "sell_amount_usd": _safe_float(t.get("sell_amount_cur") or t.get("sell_volume_cur")),
            "holding": t.get("is_holding", False),
            "buy_tx_count": int(t.get("buy_tx_count") or 0),
        })
    return result


def _parse_trending(raw: list) -> list:
    result = []
    for t in raw:
        addr = t.get("address") or t.get("token_address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "symbol": t.get("symbol") or "",
            "price_usd": _safe_float(t.get("price") or t.get("price_usd")),
            "volume_1h": _safe_float(t.get("volume_1h")),
            "smart_money_buys_1h": int(t.get("smart_buy_1h") or t.get("smart_buys_1h") or 0),
            "price_change_1h": _safe_float(t.get("price_change_1h") or t.get("change_1h")),
        })
    return result


def _parse_new_pairs(raw: list) -> list:
    result = []
    for t in raw:
        addr = t.get("address") or t.get("token_address")
        if not addr:
            continue
        result.append({
            "address": addr,
            "symbol": t.get("symbol") or "",
            "open_timestamp": t.get("open_timestamp"),
            "liquidity_usd": _safe_float(t.get("liquidity")),
            "smart_money_buys": int(t.get("smart_buy_count") or 0),
        })
    return result


def _safe_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0
