"""
Jupiter Aggregator Client — Solana Best-Route Swaps
====================================================
Jupiter finds the best swap route across all Solana DEXes (Raydium, Orca,
Meteora, etc.) and aggregates liquidity for minimum slippage.

Paper mode  : get_quote() for realistic price-impact data; no broadcast.
Live mode   : get_quote() → execute_swap() → returns tx signature.

Quote API requires no key (public endpoint).
Live execution requires:
  SOLANA_PRIVATE_KEY  — Base58-encoded keypair (64 bytes)
  HELIUS_API_KEY      — used to build the RPC URL

Optional deps for live execution (pip install solders base58):
  solders  — Solana transaction types
  base58   — keypair decoding
"""
import logging
import os
import requests

logger = logging.getLogger("jarvis.data.jupiter")

_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"
_PRICE_URL = "https://price.jup.ag/v6/price"
_TIMEOUT   = 15

# Well-known Solana token mints
SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_DEFAULT_SLIPPAGE_BPS = 50   # 0.5% — tight enough for small caps with decent liquidity


def get_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = _DEFAULT_SLIPPAGE_BPS,
) -> dict:
    """
    Fetch best-route swap quote from Jupiter.

    amount: token units in the input mint's smallest denomination
            (lamports for SOL, micro-USDC for USDC, etc.)

    Returns:
      in_amount:        int   — input token units
      out_amount:       int   — output token units (pre-slippage min)
      price_impact_pct: float — estimated price impact as a fraction (0.01 = 1%)
      route_plan:       list  — DEX hops used
      raw:              dict  — full Jupiter response (needed for execute_swap)
    Or {} on any failure.
    """
    try:
        r = requests.get(
            _QUOTE_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": False,
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        return {
            "in_amount":        int(data.get("inAmount", 0)),
            "out_amount":       int(data.get("outAmount", 0)),
            "price_impact_pct": float(data.get("priceImpactPct", 0)),
            "route_plan":       data.get("routePlan", []),
            "raw":              data,
        }
    except Exception as e:
        logger.debug(f"Jupiter quote failed ({input_mint[:8]}→{output_mint[:8]}): {e}")
        return {}


def get_price_impact(token_mint: str, size_usd: float) -> float:
    """
    Estimate price impact for buying `size_usd` worth of a token with USDC.
    Returns price_impact as a fraction (0.02 = 2%) or 0.0 on failure.
    """
    usdc_units = int(size_usd * 1_000_000)  # USDC has 6 decimals
    quote = get_quote(USDC_MINT, token_mint, usdc_units)
    return float(quote.get("price_impact_pct", 0.0))


def get_sol_price() -> float | None:
    """Fetch current SOL/USD price from Jupiter price API."""
    try:
        r = requests.get(_PRICE_URL, params={"ids": SOL_MINT}, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return float(data["data"][SOL_MINT]["price"])
    except Exception as e:
        logger.debug(f"SOL price fetch failed: {e}")
        return None


def execute_swap(quote: dict, rpc_url: str) -> dict:
    """
    Sign and broadcast a Jupiter swap transaction on Solana mainnet.

    Requires:
      SOLANA_PRIVATE_KEY env var — Base58 encoded 64-byte keypair
      quote["raw"]              — the raw Jupiter quote response

    Returns:
      {"tx_signature": "...", "status": "broadcast", "wallet": "..."}
    or
      {"error": "...", "status": "failed"}

    Called ONLY when system mode == "live". Never called in paper mode.
    """
    private_key_b58 = os.getenv("SOLANA_PRIVATE_KEY", "")
    if not private_key_b58:
        return {"error": "SOLANA_PRIVATE_KEY not set", "status": "failed"}

    raw_quote = quote.get("raw")
    if not raw_quote:
        return {"error": "Missing raw quote data", "status": "failed"}

    try:
        import base58                                      # pip install base58
        from solders.keypair import Keypair                # pip install solders
        from solders.transaction import VersionedTransaction
        import base64

        keypair = Keypair.from_bytes(base58.b58decode(private_key_b58))
        wallet_pubkey = str(keypair.pubkey())

        # Step 1 — Get swap transaction from Jupiter
        swap_r = requests.post(
            _SWAP_URL,
            json={
                "quoteResponse": raw_quote,
                "userPublicKey": wallet_pubkey,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            },
            timeout=_TIMEOUT,
        )
        swap_r.raise_for_status()
        swap_data = swap_r.json()
        tx_b64 = swap_data.get("swapTransaction", "")
        if not tx_b64:
            return {"error": "Jupiter returned no swap transaction", "status": "failed"}

        # Step 2 — Sign
        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signed = keypair.sign_message(bytes(tx.message))
        signed_tx_b64 = base64.b64encode(bytes(tx)).decode()

        # Step 3 — Broadcast via Helius RPC
        rpc_r = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed_tx_b64,
                    {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
                ],
            },
            timeout=30,
        )
        rpc_r.raise_for_status()
        result = rpc_r.json()

        if "error" in result:
            return {"error": result["error"].get("message", "RPC error"), "status": "failed"}

        sig = result.get("result", "")
        logger.info(f"Jupiter swap broadcast: {sig[:20]}... | wallet: {wallet_pubkey[:8]}")
        return {"tx_signature": sig, "status": "broadcast", "wallet": wallet_pubkey[:8]}

    except ImportError as e:
        return {
            "error": f"Missing live-execution dependency: {e} — pip install solders base58",
            "status": "failed",
        }
    except Exception as e:
        logger.error(f"Jupiter execute_swap failed: {e}")
        return {"error": str(e), "status": "failed"}
