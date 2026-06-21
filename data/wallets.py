"""
Curated wallet lists for whale and smart money tracking.

How to populate this file:
  - Solana whales:  solscan.io/leaderboard, step.finance whale tracker
  - BNB whales:     bscscan.com/accounts, debank.com whale leaderboard
  - Smart money:    nansen.ai top traders, cielo.finance wallet labels
  - Exchange wallets: publicly documented on each chain's explorer

All addresses here are public on-chain data — no private information.
"""

# ── Solana ────────────────────────────────────────────────────────────────────

SOLANA_WHALE_WALLETS: list[str] = [
    # Add known Solana whale addresses here.
    # Format: base58 string, 32-44 characters.
    # Example sources: solscan.io/leaderboard, step.finance
]

SOLANA_SMART_MONEY: list[str] = [
    # Wallets with documented history of early entries on winning tokens.
    # Sources: nansen.ai, cielo.finance, manually identified from DexScreener
]

SOLANA_EXCHANGE_WALLETS: list[str] = [
    # Known exchange hot wallets (Binance, Coinbase, OKX, Bybit on Solana)
    # Large inflows here can signal sell pressure; outflows can signal accumulation.
]

# ── BNB Chain ─────────────────────────────────────────────────────────────────

BNB_WHALE_WALLETS: list[str] = [
    # Add known BNB Chain whale addresses here.
    # Format: 0x... (42 characters)
]

BNB_SMART_MONEY: list[str] = [
    # Profitable early buyers on BNB Chain.
    # Sources: bscscan.com top accounts, debank whale tracker
]

BNB_EXCHANGE_WALLETS: list[str] = [
    # Known BNB Chain exchange hot wallets.
]

# ── Combined helpers ──────────────────────────────────────────────────────────

def all_solana_tracked() -> list[str]:
    return list(set(SOLANA_WHALE_WALLETS + SOLANA_SMART_MONEY))


def all_bnb_tracked() -> list[str]:
    return list(set(BNB_WHALE_WALLETS + BNB_SMART_MONEY))


def is_exchange_wallet(address: str) -> bool:
    all_exchange = SOLANA_EXCHANGE_WALLETS + BNB_EXCHANGE_WALLETS
    return address in all_exchange
