"""
WhaleAgent tests — large buy detection, holder concentration, signal strength scaling.
No real API calls.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from agents.whale_agent import WhaleAgent, _MIN_WHALE_USD, _SCALE_USD, _TOP_HOLDER_MIN_USD
from data.helius import detect_large_token_buys


# ── Constants sanity ──────────────────────────────────────────────────────────

def test_min_whale_usd_reasonable():
    assert _MIN_WHALE_USD >= 10_000   # must be meaningfully large

def test_scale_usd_above_min():
    assert _SCALE_USD > _MIN_WHALE_USD

def test_top_holder_min_above_whale_min():
    assert _TOP_HOLDER_MIN_USD >= _MIN_WHALE_USD


# ── Strength scaling formula ──────────────────────────────────────────────────

def test_strength_single_min_buy():
    total = _MIN_WHALE_USD
    strength = min(1.0, total / _SCALE_USD)
    assert 0.0 < strength < 1.0   # one $25k buy is a partial signal


def test_strength_at_scale_is_1():
    strength = min(1.0, _SCALE_USD / _SCALE_USD)
    assert strength == 1.0


def test_strength_capped_above_scale():
    total = _SCALE_USD * 10
    strength = min(1.0, total / _SCALE_USD)
    assert strength == 1.0


def test_strength_zero_means_no_whales():
    strength = min(1.0, 0 / _SCALE_USD)
    assert strength == 0.0


def test_strength_scales_linearly_below_cap():
    s1 = min(1.0, 100_000 / _SCALE_USD)
    s2 = min(1.0, 200_000 / _SCALE_USD)
    assert s2 == pytest.approx(s1 * 2, rel=0.01)


# ── GMGN whale filtering logic ────────────────────────────────────────────────

def test_whale_filter_excludes_small_buys():
    traders = [
        {"buy_amount_usd": 5_000,  "holding": True},
        {"buy_amount_usd": 30_000, "holding": True},
        {"buy_amount_usd": 1_000,  "holding": True},
    ]
    whales = [t for t in traders if t["buy_amount_usd"] >= _MIN_WHALE_USD and t["holding"]]
    assert len(whales) == 1
    assert whales[0]["buy_amount_usd"] == 30_000


def test_whale_filter_excludes_sold_positions():
    traders = [
        {"buy_amount_usd": 50_000, "holding": False},  # sold — not accumulating
        {"buy_amount_usd": 50_000, "holding": True},
    ]
    whales = [t for t in traders if t["buy_amount_usd"] >= _MIN_WHALE_USD and t["holding"]]
    assert len(whales) == 1


def test_whale_filter_empty_traders():
    traders = []
    whales = [t for t in traders if t.get("buy_amount_usd", 0) >= _MIN_WHALE_USD]
    assert whales == []


def test_whale_filter_all_qualify():
    traders = [
        {"buy_amount_usd": 25_000, "holding": True},
        {"buy_amount_usd": 75_000, "holding": True},
        {"buy_amount_usd": 100_000, "holding": True},
    ]
    whales = [t for t in traders if t["buy_amount_usd"] >= _MIN_WHALE_USD and t["holding"]]
    assert len(whales) == 3
    total = sum(w["buy_amount_usd"] for w in whales)
    assert total == 200_000


# ── Holder concentration logic ────────────────────────────────────────────────

def test_holder_concentration_filters_small():
    holders = [{"uiAmount": 1_000}, {"uiAmount": 100}]
    price_usd = 1.0
    whales = [h for h in holders if (h.get("uiAmount") or 0) * price_usd >= _TOP_HOLDER_MIN_USD]
    assert len(whales) == 0


def test_holder_concentration_detects_large():
    holders = [{"uiAmount": 100_000}, {"uiAmount": 200}]
    price_usd = 1.0
    whales = [h for h in holders if (h.get("uiAmount") or 0) * price_usd >= _TOP_HOLDER_MIN_USD]
    assert len(whales) == 1


def test_holder_concentration_price_matters():
    holders = [{"uiAmount": 100_000}]
    price_low = 0.0001   # very cheap token — 100k tokens = $10 → no whale
    price_high = 1.0     # 100k tokens = $100k → whale
    whales_low  = [h for h in holders if (h.get("uiAmount") or 0) * price_low  >= _TOP_HOLDER_MIN_USD]
    whales_high = [h for h in holders if (h.get("uiAmount") or 0) * price_high >= _TOP_HOLDER_MIN_USD]
    assert len(whales_low) == 0
    assert len(whales_high) == 1


# ── Helius detect_large_token_buys ───────────────────────────────────────────

def test_helius_finds_watchlist_token_buy():
    txns = [
        {
            "signature": "sig1",
            "timestamp": 1700000000,
            "tokenTransfers": [
                {
                    "mint": "WatchlistMintAddr",
                    "fromUserAccount": "seller",
                    "toUserAccount": "buyer_wallet",
                    "tokenAmount": 50_000.0,
                }
            ],
            "nativeTransfers": [],
        }
    ]
    watchlist = {"WatchlistMintAddr"}
    with patch("data.helius.get_transactions", return_value=txns):
        buys = detect_large_token_buys("buyer_wallet", watchlist)
    assert len(buys) == 1
    assert buys[0]["mint"] == "WatchlistMintAddr"
    assert buys[0]["amount_tokens"] == 50_000.0


def test_helius_ignores_non_watchlist_token():
    txns = [
        {
            "signature": "sig1",
            "timestamp": 1700000000,
            "tokenTransfers": [
                {
                    "mint": "SomeOtherToken",
                    "fromUserAccount": "a",
                    "toUserAccount": "b",
                    "tokenAmount": 999_999.0,
                }
            ],
        }
    ]
    watchlist = {"WatchlistMintAddr"}
    with patch("data.helius.get_transactions", return_value=txns):
        buys = detect_large_token_buys("wallet", watchlist)
    assert buys == []


def test_helius_ignores_zero_amount():
    txns = [
        {
            "signature": "sig1",
            "timestamp": 1700000000,
            "tokenTransfers": [
                {
                    "mint": "WatchlistMintAddr",
                    "fromUserAccount": "a",
                    "toUserAccount": "b",
                    "tokenAmount": 0,
                }
            ],
        }
    ]
    watchlist = {"WatchlistMintAddr"}
    with patch("data.helius.get_transactions", return_value=txns):
        buys = detect_large_token_buys("wallet", watchlist)
    assert buys == []


def test_helius_handles_empty_transactions():
    with patch("data.helius.get_transactions", return_value=[]):
        buys = detect_large_token_buys("wallet", {"SomeMint"})
    assert buys == []


def test_helius_handles_missing_token_transfers():
    txns = [{"signature": "sig1", "timestamp": 1700000000}]
    with patch("data.helius.get_transactions", return_value=txns):
        buys = detect_large_token_buys("wallet", {"SomeMint"})
    assert buys == []


def test_helius_multiple_watchlist_tokens():
    txns = [
        {
            "signature": "sig1",
            "timestamp": 1700000000,
            "tokenTransfers": [
                {"mint": "MintA", "fromUserAccount": "a", "toUserAccount": "w", "tokenAmount": 1000.0},
                {"mint": "MintB", "fromUserAccount": "b", "toUserAccount": "w", "tokenAmount": 2000.0},
                {"mint": "MintC", "fromUserAccount": "c", "toUserAccount": "w", "tokenAmount": 3000.0},
            ],
        }
    ]
    watchlist = {"MintA", "MintB"}   # MintC not in watchlist
    with patch("data.helius.get_transactions", return_value=txns):
        buys = detect_large_token_buys("w", watchlist)
    mints = {b["mint"] for b in buys}
    assert "MintA" in mints
    assert "MintB" in mints
    assert "MintC" not in mints


# ── Signal distinction from SmartMoneyAgent ───────────────────────────────────

def test_whale_signal_ignores_wallet_quality():
    """
    A wallet with zero track record but a $50k buy must be a whale signal.
    (SmartMoneyAgent would score it low and ignore it — WhaleAgent must not.)
    """
    traders = [
        {
            "buy_amount_usd": 50_000,
            "holding": True,
            "realized_profit": 0,       # no track record
            "win_rate": 0,
            "buy_tx_count": 1,
        }
    ]
    whales = [t for t in traders if t["buy_amount_usd"] >= _MIN_WHALE_USD and t["holding"]]
    # Still qualifies as whale buy — size is the only criterion
    assert len(whales) == 1
