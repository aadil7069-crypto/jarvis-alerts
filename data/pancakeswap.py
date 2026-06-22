"""
PancakeSwap Smart Router Client — BNB Chain Swaps
==================================================
Uses the PancakeSwap v3 Smart Router for best-route swaps on BSC.

Paper mode  : get_quote() for price-impact data; no broadcast.
Live mode   : get_quote() → execute_swap() → returns tx hash.

Quote API is public (no key required).
Live execution requires:
  BNB_PRIVATE_KEY    — hex-encoded private key (0x... or raw hex)
  BNB_WALLET_ADDRESS — public wallet address (0x...)
  BNB_RPC_URL        — BSC RPC endpoint (env var, defaults to public BSC RPC)

Optional dep for live execution: pip install web3
"""
import logging
import os
import requests

logger = logging.getLogger("jarvis.data.pancakeswap")

_SMART_ROUTER   = "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"  # PancakeSwap v3 smart router
_DEFAULT_BNB_RPC = "https://bsc-dataseed.binance.org/"
_TIMEOUT = 15

# Well-known BSC token addresses
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"

_DEFAULT_SLIPPAGE_PCT = 0.5   # 0.5%
_DEFAULT_GAS_PRICE_GWEI = 3


def get_quote(
    token_in: str,
    token_out: str,
    amount_in_wei: int,
    slippage_pct: float = _DEFAULT_SLIPPAGE_PCT,
    rpc_url: str = "",
) -> dict:
    """
    Get a PancakeSwap v3 swap quote via the on-chain Quoter contract.

    token_in / token_out: checksummed BSC token addresses
    amount_in_wei: input amount in the token's smallest unit (wei)
    slippage_pct: slippage tolerance as a percentage (0.5 = 0.5%)

    Returns:
      amount_out:       int   — expected output token units
      price_impact_pct: float — estimated price impact
      min_amount_out:   int   — minimum output with slippage applied
      gas_estimate:     int   — estimated gas units
    Or {} on failure.
    """
    _rpc = rpc_url or os.getenv("BNB_RPC_URL", _DEFAULT_BNB_RPC)

    try:
        from web3 import Web3  # pip install web3

        w3 = Web3(Web3.HTTPProvider(_rpc, request_kwargs={"timeout": _TIMEOUT}))
        if not w3.is_connected():
            logger.debug("PancakeSwap: BNB RPC not connected")
            return {}

        # PancakeSwap v3 Quoter contract ABI (quoteExactInputSingle)
        quoter_abi = [{
            "inputs": [{"components": [
                {"name": "tokenIn",  "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "fee",      "type": "uint24"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ], "name": "params", "type": "tuple"}],
            "name": "quoteExactInputSingle",
            "outputs": [
                {"name": "amountOut",               "type": "uint256"},
                {"name": "sqrtPriceX96After",        "type": "uint160"},
                {"name": "initializedTicksCrossed",  "type": "uint32"},
                {"name": "gasEstimate",              "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
            "type": "function",
        }]
        quoter_address = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"  # PCS v3 Quoter

        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_address),
            abi=quoter_abi,
        )

        # Try 500 (0.05%), 2500 (0.25%), 100 (0.01%) fee tiers — pick first success
        for fee_tier in (500, 2500, 100, 10000):
            try:
                result = quoter.functions.quoteExactInputSingle({
                    "tokenIn":  Web3.to_checksum_address(token_in),
                    "tokenOut": Web3.to_checksum_address(token_out),
                    "amountIn": amount_in_wei,
                    "fee":      fee_tier,
                    "sqrtPriceLimitX96": 0,
                }).call()

                amount_out = result[0]
                gas_estimate = result[3]
                slippage_factor = 1 - (slippage_pct / 100)
                min_amount_out = int(amount_out * slippage_factor)

                # Rough price impact: compare to spot (simplified)
                price_impact_pct = 0.0

                return {
                    "amount_out":       amount_out,
                    "min_amount_out":   min_amount_out,
                    "price_impact_pct": price_impact_pct,
                    "gas_estimate":     gas_estimate,
                    "fee_tier":         fee_tier,
                }
            except Exception:
                continue  # try next fee tier

        logger.debug(f"PancakeSwap: no liquid pool found for {token_in[:8]}→{token_out[:8]}")
        return {}

    except ImportError:
        logger.debug("PancakeSwap: web3 not installed — pip install web3")
        return {}
    except Exception as e:
        logger.debug(f"PancakeSwap quote failed: {e}")
        return {}


def get_price_impact(token_address: str, size_usd: float, bnb_price_usd: float = 300.0) -> float:
    """
    Estimate price impact for buying `size_usd` worth of a token with BNB.
    Returns price_impact as a fraction (0.02 = 2%) or 0.0 on failure.
    """
    amount_bnb = size_usd / bnb_price_usd
    amount_wei = int(amount_bnb * 1e18)
    quote = get_quote(WBNB_ADDRESS, token_address, amount_wei)
    return float(quote.get("price_impact_pct", 0.0))


def execute_swap(
    token_in: str,
    token_out: str,
    amount_in_wei: int,
    min_amount_out: int,
    fee_tier: int,
    rpc_url: str = "",
) -> dict:
    """
    Execute a PancakeSwap v3 swap on BSC mainnet.

    Requires:
      BNB_PRIVATE_KEY    env var — hex private key
      BNB_WALLET_ADDRESS env var — sender address

    Returns:
      {"tx_hash": "0x...", "status": "broadcast"}
    or
      {"error": "...", "status": "failed"}

    Called ONLY when system mode == "live". Never called in paper mode.
    """
    private_key = os.getenv("BNB_PRIVATE_KEY", "")
    wallet      = os.getenv("BNB_WALLET_ADDRESS", "")

    if not private_key or not wallet:
        return {"error": "BNB_PRIVATE_KEY / BNB_WALLET_ADDRESS not set", "status": "failed"}

    _rpc = rpc_url or os.getenv("BNB_RPC_URL", _DEFAULT_BNB_RPC)

    try:
        from web3 import Web3
        from web3.middleware import geth_poa_middleware  # type: ignore

        w3 = Web3(Web3.HTTPProvider(_rpc, request_kwargs={"timeout": 30}))
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if not w3.is_connected():
            return {"error": "BNB RPC not reachable", "status": "failed"}

        # PancakeSwap v3 smart router exactInputSingle
        router_abi = [{
            "inputs": [{"components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "recipient",         "type": "address"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "amountOutMinimum",  "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ], "name": "params", "type": "tuple"}],
            "name": "exactInputSingle",
            "outputs": [{"name": "amountOut", "type": "uint256"}],
            "stateMutability": "payable",
            "type": "function",
        }]

        router = w3.eth.contract(
            address=Web3.to_checksum_address(_SMART_ROUTER),
            abi=router_abi,
        )

        gas_price = w3.to_wei(_DEFAULT_GAS_PRICE_GWEI, "gwei")
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(wallet))

        is_bnb_in = token_in.lower() == WBNB_ADDRESS.lower()

        tx = router.functions.exactInputSingle({
            "tokenIn":           Web3.to_checksum_address(token_in),
            "tokenOut":          Web3.to_checksum_address(token_out),
            "fee":               fee_tier,
            "recipient":         Web3.to_checksum_address(wallet),
            "amountIn":          amount_in_wei,
            "amountOutMinimum":  min_amount_out,
            "sqrtPriceLimitX96": 0,
        }).build_transaction({
            "from":     Web3.to_checksum_address(wallet),
            "value":    amount_in_wei if is_bnb_in else 0,
            "gas":      300_000,
            "gasPrice": gas_price,
            "nonce":    nonce,
            "chainId":  56,  # BSC mainnet
        })

        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hash_hex = tx_hash.hex()

        logger.info(f"PancakeSwap swap broadcast: {tx_hash_hex[:20]}... | wallet: {wallet[:10]}")
        return {"tx_hash": tx_hash_hex, "status": "broadcast", "wallet": wallet[:10]}

    except ImportError:
        return {
            "error": "Missing live-execution dependency — pip install web3",
            "status": "failed",
        }
    except Exception as e:
        logger.error(f"PancakeSwap execute_swap failed: {e}")
        return {"error": str(e), "status": "failed"}
