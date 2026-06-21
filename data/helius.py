import logging
import os
import requests

logger = logging.getLogger("jarvis.data.helius")

_RPC = "https://mainnet.helius-rpc.com/"
_API = "https://api.helius.xyz/v0"


def _key() -> str:
    k = os.getenv("HELIUS_API_KEY", "")
    if not k:
        logger.warning("HELIUS_API_KEY not set — Solana on-chain data unavailable")
    return k


def get_transactions(address: str, limit: int = 50) -> list:
    """Fetch recent parsed transactions for a Solana address."""
    k = _key()
    if not k:
        return []
    try:
        r = requests.get(
            f"{_API}/addresses/{address}/transactions",
            params={"api-key": k, "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Helius transactions failed [{address}]: {e}")
        return []


def get_token_largest_accounts(mint: str) -> list:
    """Return the top token accounts (holder list) for a Solana mint."""
    k = _key()
    if not k:
        return []
    try:
        r = requests.post(
            _RPC,
            params={"api-key": k},
            json={"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("value", [])
    except Exception as e:
        logger.error(f"Helius holder fetch failed [{mint}]: {e}")
        return []


def detect_large_token_buys(address: str, watchlist_mints: set) -> list:
    """
    Scan a wallet's recent transactions for token purchases matching watchlist mints.

    Uses tokenTransfers (SPL token movements), NOT nativeTransfers (SOL payments).
    A "buy" is identified when the watched wallet is the recipient of a token transfer.
    Returns one entry per matching transfer — caller applies USD threshold using price data.
    """
    txns = get_transactions(address, limit=100)
    buys = []
    for tx in txns:
        for transfer in tx.get("tokenTransfers", []):
            mint = transfer.get("mint", "")
            if mint not in watchlist_mints:
                continue
            to_account = transfer.get("toUserAccount", "")
            amount = float(transfer.get("tokenAmount", 0) or 0)
            if amount <= 0:
                continue
            buys.append({
                "signature": tx.get("signature"),
                "mint": mint,
                "amount_tokens": amount,
                "from_account": transfer.get("fromUserAccount"),
                "to_account": to_account,
                "timestamp": tx.get("timestamp"),
            })
    return buys
