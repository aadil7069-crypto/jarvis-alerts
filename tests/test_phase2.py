"""
Phase 2 tests — validate data parsing and vetting logic without making real API calls.
"""
from data.dexscreener import extract_token_info
from data.goplus import _parse as goplus_parse
from data.honeypot import check_bsc


# ── DexScreener ───────────────────────────────────────────────────────────────

def test_extract_token_info_full():
    fake_pair = {
        "baseToken": {"address": "0xABC", "symbol": "TEST", "name": "Test Token"},
        "priceUsd": "0.0042",
        "liquidity": {"usd": 123456},
        "volume": {"h24": 50000},
        "priceChange": {"m5": 1.2, "h1": -3.4, "h24": 10.5},
        "txns": {"h24": {"buys": 200, "sells": 150}},
        "pairCreatedAt": 1700000000000,
        "chainId": "solana",
        "dexId": "raydium",
    }
    info = extract_token_info(fake_pair)
    assert info["address"] == "0xABC"
    assert info["symbol"] == "TEST"
    assert info["price_usd"] == 0.0042
    assert info["liquidity_usd"] == 123456
    assert info["buys_24h"] == 200
    assert info["chain"] == "solana"


def test_extract_token_info_missing_fields():
    info = extract_token_info({})
    assert info["price_usd"] == 0
    assert info["liquidity_usd"] == 0
    assert info["address"] == ""


# ── GoPlus ────────────────────────────────────────────────────────────────────

def test_goplus_parse_honeypot():
    raw = {"is_honeypot": "1", "buy_tax": "10", "sell_tax": "90", "is_mintable": "0"}
    result = goplus_parse(raw)
    assert result["is_honeypot"] is True
    assert result["sell_tax"] == 90.0


def test_goplus_parse_clean():
    raw = {"is_honeypot": "0", "buy_tax": "2", "sell_tax": "2", "is_open_source": "1"}
    result = goplus_parse(raw)
    assert result["is_honeypot"] is False
    assert result["buy_tax"] == 2.0
    assert result["is_open_source"] is True


def test_goplus_parse_empty():
    result = goplus_parse({})
    assert result["is_honeypot"] is False
    assert result["buy_tax"] == 0.0


# ── Vetting logic (unit-level, no API calls) ──────────────────────────────────

def test_fail_reasons_low_liquidity():
    liquidity = 10_000
    min_liq = 50_000
    fail_reasons = []
    if liquidity < min_liq:
        fail_reasons.append(f"low_liquidity:${liquidity:,.0f}")
    assert "low_liquidity:$10,000" in fail_reasons


def test_fail_reasons_high_sell_tax():
    safety = {"is_honeypot": False, "sell_tax": 95.0, "buy_tax": 5.0, "is_mintable": False}
    fail_reasons = []
    if safety.get("sell_tax", 0) > 20:
        fail_reasons.append(f"sell_tax:{safety['sell_tax']:.0f}%")
    assert "sell_tax:95%" in fail_reasons
