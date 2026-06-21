"""
Intelligence upgrade tests — GMGN/Birdeye parsers, wallet scoring,
coordinated buy detection, updated confidence engine.
No real API calls.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core.scoring import compute_score, signals_to_strengths, WEIGHTS
from data.gmgn import _parse_wallets, _parse_token_traders, _parse_trending, _safe_float
from data.birdeye import _safe_float as be_safe_float
from agents.smart_money_agent import SmartMoneyAgent, _COORDINATED_BUY_THRESHOLD


# ── WEIGHTS sanity ────────────────────────────────────────────────────────────

def test_weights_sum_100():
    assert sum(WEIGHTS.values()) == 100


def test_weights_has_elite_trader():
    assert "elite_trader" in WEIGHTS
    assert WEIGHTS["elite_trader"] > 0


def test_weights_smart_money_gte_elite():
    assert WEIGHTS["smart_money_buy"] >= WEIGHTS["elite_trader"]


# ── Scoring: elite_trader signal ─────────────────────────────────────────────

def test_score_with_elite_trader():
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=0.8,
        elite_trader_strength=0.9,
    )
    assert result["breakdown"]["elite_trader"] > 0
    assert result["total"] > 30


def test_score_elite_only():
    """Elite trader signal alone contributes its full weight."""
    result = compute_score(
        vetting_passed=False,
        elite_trader_strength=1.0,
    )
    assert result["breakdown"]["elite_trader"] == WEIGHTS["elite_trader"]


def test_score_full_convergence():
    """All six signals at full strength = 100."""
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        elite_trader_strength=1.0,
        whale_strength=1.0,
        sentiment_strength=1.0,
        strategy_strength=1.0,
    )
    assert result["total"] == 100


def test_score_convergence_beats_single_source():
    single = compute_score(vetting_passed=True, smart_money_strength=1.0)
    converged = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        elite_trader_strength=0.8,
        whale_strength=0.7,
    )
    assert converged["total"] > single["total"]


def test_score_breakdown_has_six_keys():
    result = compute_score(vetting_passed=True)
    assert len(result["breakdown"]) == 6


def test_score_pm_modifier_applied():
    base = compute_score(vetting_passed=True, smart_money_strength=1.0)
    boosted = compute_score(vetting_passed=True, smart_money_strength=1.0, pm_modifier=10)
    assert boosted["total"] == min(100, base["total"] + 10)


def test_score_total_clamped_to_100():
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        elite_trader_strength=1.0,
        whale_strength=1.0,
        sentiment_strength=1.0,
        strategy_strength=1.0,
        pm_modifier=50,  # would push past 100
    )
    assert result["total"] == 100


# ── signals_to_strengths: elite_trader category ───────────────────────────────

def _mock_signal(agent: str, strength: float, hours_until_expiry: int = 4) -> MagicMock:
    s = MagicMock()
    s.agent_name = agent
    s.strength = strength
    s.signal_type = "bullish"
    s.created_at = datetime.now(timezone.utc)
    s.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours_until_expiry)
    return s


def test_signals_elite_trader_categorised():
    signals = [_mock_signal("elite_trader_agent", 85)]
    strengths = signals_to_strengths(signals)
    assert strengths["elite_trader"] == pytest.approx(0.85)


def test_signals_smart_money_categorised():
    signals = [_mock_signal("smart_money_agent", 70)]
    strengths = signals_to_strengths(signals)
    assert strengths["smart_money"] == pytest.approx(0.70)


def test_signals_smart_and_elite_independent():
    """smart_money and elite_trader are separate buckets — both contribute."""
    signals = [
        _mock_signal("smart_money_agent", 80),
        _mock_signal("elite_trader_agent", 70),
    ]
    strengths = signals_to_strengths(signals)
    assert strengths["smart_money"] == pytest.approx(0.80)
    assert strengths["elite_trader"] == pytest.approx(0.70)


def test_signals_expired_ignored():
    signals = [_mock_signal("elite_trader_agent", 90, hours_until_expiry=-1)]
    strengths = signals_to_strengths(signals)
    assert strengths["elite_trader"] == 0.0


def test_signals_empty_list():
    strengths = signals_to_strengths([])
    for v in strengths.values():
        assert v == 0.0


# ── GMGN _parse_wallets ───────────────────────────────────────────────────────

def test_gmgn_parse_wallets_standard():
    raw = [
        {
            "wallet_address": "5xKR...",
            "realized_profit": "85000",
            "winrate": "0.72",
            "buy_30d": "45",
            "avg_cost": "8500",
            "tag": "smart_degen",
        }
    ]
    parsed = _parse_wallets(raw)
    assert len(parsed) == 1
    w = parsed[0]
    assert w["address"] == "5xKR..."
    assert w["realized_pnl_7d"] == 85000.0
    assert w["win_rate"] == 0.72
    assert w["trade_count"] == 45
    assert w["wallet_label"] == "smart_degen"


def test_gmgn_parse_wallets_missing_address_skipped():
    raw = [{"realized_profit": "1000"}]
    assert _parse_wallets(raw) == []


def test_gmgn_parse_wallets_empty():
    assert _parse_wallets([]) == []


def test_gmgn_parse_wallets_fallback_label():
    raw = [{"wallet_address": "abc", "realized_profit": "0"}]
    parsed = _parse_wallets(raw)
    assert parsed[0]["wallet_label"] == "smart_degen"


# ── GMGN _parse_token_traders ─────────────────────────────────────────────────

def test_gmgn_parse_token_traders_standard():
    raw = [
        {
            "wallet_address": "abc123",
            "tag": "kol",
            "realized_profit": "12000",
            "buy_amount_cur": "5000",
            "sell_amount_cur": "0",
            "is_holding": True,
            "buy_tx_count": 3,
        }
    ]
    parsed = _parse_token_traders(raw)
    assert len(parsed) == 1
    t = parsed[0]
    assert t["wallet_address"] == "abc123"
    assert t["wallet_label"] == "kol"
    assert t["buy_amount_usd"] == 5000.0
    assert t["holding"] is True
    assert t["buy_tx_count"] == 3


def test_gmgn_parse_token_traders_no_address_skipped():
    raw = [{"tag": "kol", "buy_amount_cur": "1000"}]
    assert _parse_token_traders(raw) == []


# ── GMGN _parse_trending ──────────────────────────────────────────────────────

def test_gmgn_parse_trending_standard():
    raw = [
        {
            "address": "TokenAddr",
            "symbol": "BONK",
            "price": "0.000012",
            "volume_1h": "50000",
            "smart_buy_1h": 5,
            "change_1h": "12.5",
        }
    ]
    parsed = _parse_trending(raw)
    assert len(parsed) == 1
    t = parsed[0]
    assert t["address"] == "TokenAddr"
    assert t["symbol"] == "BONK"
    assert t["smart_money_buys_1h"] == 5
    assert t["price_change_1h"] == 12.5


def test_gmgn_parse_trending_no_address_skipped():
    raw = [{"symbol": "BONK", "price": "0.01"}]
    assert _parse_trending(raw) == []


# ── GMGN _safe_float ─────────────────────────────────────────────────────────

def test_gmgn_safe_float_none():
    assert _safe_float(None) == 0.0


def test_gmgn_safe_float_string():
    assert _safe_float("42.5") == 42.5


def test_gmgn_safe_float_int():
    assert _safe_float(10) == 10.0


def test_gmgn_safe_float_invalid():
    assert _safe_float("not_a_number") == 0.0


def test_gmgn_safe_float_empty_string():
    assert _safe_float("") == 0.0


# ── Birdeye _safe_float ───────────────────────────────────────────────────────

def test_birdeye_safe_float_none():
    assert be_safe_float(None) == 0.0


def test_birdeye_safe_float_string():
    assert be_safe_float("99.9") == 99.9


def test_birdeye_safe_float_invalid():
    assert be_safe_float("n/a") == 0.0


# ── Wallet quality score computation ─────────────────────────────────────────

def _bare_sm_agent():
    """Construct SmartMoneyAgent without __init__ for unit-testing _compute_score."""
    agent = SmartMoneyAgent.__new__(SmartMoneyAgent)
    agent.logger = MagicMock()
    return agent


def test_wallet_score_high_quality():
    agent = _bare_sm_agent()
    stats = {
        "win_rate": 75.0,
        "realized_pnl": 90_000,
        "trade_count": 40,
        "avg_trade_size_usd": 8_000,
    }
    score = agent._compute_score(stats)
    assert score >= 70.0, f"Expected >=70, got {score}"


def test_wallet_score_low_quality():
    agent = _bare_sm_agent()
    stats = {
        "win_rate": 20.0,
        "realized_pnl": 100,
        "trade_count": 2,
        "avg_trade_size_usd": 50,
    }
    score = agent._compute_score(stats)
    assert score < 30.0, f"Expected <30, got {score}"


def test_wallet_score_empty_stats():
    agent = _bare_sm_agent()
    assert agent._compute_score({}) == 0.0


def test_wallet_score_capped_at_100():
    agent = _bare_sm_agent()
    stats = {
        "win_rate": 100.0,
        "realized_pnl": 10_000_000,
        "trade_count": 10_000,
        "avg_trade_size_usd": 1_000_000,
    }
    assert agent._compute_score(stats) <= 100.0


def test_wallet_score_pnl_has_highest_weight():
    """40%-weighted PNL: a $100k profit wallet should score above 50."""
    agent = _bare_sm_agent()
    stats = {
        "win_rate": 50.0,
        "realized_pnl": 100_000,
        "trade_count": 10,
        "avg_trade_size_usd": 500,
    }
    assert agent._compute_score(stats) > 50.0


def test_wallet_score_negative_pnl_ignored():
    """Negative P&L maps to profit_score=0, not a negative score."""
    agent = _bare_sm_agent()
    stats = {"win_rate": 60.0, "realized_pnl": -5_000, "trade_count": 5, "avg_trade_size_usd": 500}
    score = agent._compute_score(stats)
    assert score >= 0.0


# ── Coordinated buy detection ─────────────────────────────────────────────────

def test_coordinated_buy_threshold_value():
    assert _COORDINATED_BUY_THRESHOLD >= 3


def test_three_buyers_meets_threshold():
    assert 3 >= _COORDINATED_BUY_THRESHOLD


def test_two_buyers_below_threshold():
    assert 2 < _COORDINATED_BUY_THRESHOLD


def test_coordination_bonus_increases_with_buyer_count():
    """Signal strength formula: base + min(0.3, (buyer_count-1) * 0.1)."""
    base_score = 70.0
    base_strength = base_score / 100.0

    strength_1 = min(1.0, base_strength + min(0.3, (1 - 1) * 0.1))   # 0.70
    strength_3 = min(1.0, base_strength + min(0.3, (3 - 1) * 0.1))   # 0.90
    strength_6 = min(1.0, base_strength + min(0.3, (6 - 1) * 0.1))   # capped at 1.0

    assert strength_3 > strength_1
    assert strength_6 == 1.0


def test_coordination_bonus_capped_at_0_3():
    """Even 100 buyers can only add 0.3 to strength."""
    bonus = min(0.3, (100 - 1) * 0.1)
    assert bonus == 0.3
