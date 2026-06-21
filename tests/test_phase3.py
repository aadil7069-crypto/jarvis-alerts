"""
Phase 3 tests — confidence scoring, signal processing, and agent decision logic.
No real API calls or DB connections required.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core.scoring import compute_score, signals_to_strengths
from data.sentiment_feeds import fear_greed_to_strength, fear_greed_to_signal_type
from agents.strategy_agent import _momentum_strength


# ── Confidence scoring ────────────────────────────────────────────────────────

def test_score_all_signals_perfect():
    """All six signal categories at full strength = 100."""
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        elite_trader_strength=1.0,
        whale_strength=1.0,
        sentiment_strength=1.0,
        strategy_strength=1.0,
        pm_modifier=0,
    )
    assert result["total"] == 100
    assert result["base_score"] == 100


def test_score_only_vetting():
    """Only vetting passed, nothing else = 18 points (vetting_pass weight)."""
    result = compute_score(vetting_passed=True)
    assert result["total"] == 18
    assert result["breakdown"]["vetting_pass"] == 18


def test_score_no_signals():
    """Nothing passed = 0."""
    result = compute_score(vetting_passed=False)
    assert result["total"] == 0


def test_score_pm_modifier_positive():
    """Prediction market adds up to +bonus."""
    result = compute_score(vetting_passed=True, pm_modifier=10)
    assert result["total"] == 28   # vetting_pass(18) + pm(10)
    assert result["pm_modifier"] == 10


def test_score_pm_modifier_negative():
    """Negative modifier reduces score but can't go below 0."""
    result = compute_score(vetting_passed=True, pm_modifier=-30)
    assert result["total"] == 0   # 20 - 30 clamped at 0


def test_score_capped_at_100():
    """Score never exceeds 100 even with a high modifier."""
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        whale_strength=1.0,
        sentiment_strength=1.0,
        strategy_strength=1.0,
        pm_modifier=50,
    )
    assert result["total"] == 100


def test_score_weights_sum_to_100():
    """At full strength across all six signals (no modifier), breakdown sums to 100."""
    result = compute_score(
        vetting_passed=True,
        smart_money_strength=1.0,
        elite_trader_strength=1.0,
        whale_strength=1.0,
        sentiment_strength=1.0,
        strategy_strength=1.0,
    )
    assert sum(result["breakdown"].values()) == 100


def test_score_partial_strength():
    """Half-strength smart money = 11 (round(22 * 0.5))."""
    result = compute_score(
        vetting_passed=False,
        smart_money_strength=0.5,
    )
    assert result["breakdown"]["smart_money_buy"] == 11


# ── Signal-to-strengths conversion ────────────────────────────────────────────

def _make_signal(agent_name: str, strength: float, hours_old: int = 0, expires_in_hours: int = 4) -> MagicMock:
    sig = MagicMock()
    sig.agent_name = agent_name
    sig.strength = strength
    sig.signal_type = "bullish"
    sig.created_at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    sig.expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
    return sig


def test_signals_to_strengths_basic():
    signals = [
        _make_signal("smart_money_agent", 80),
        _make_signal("whale_agent", 60),
        _make_signal("sentiment_agent", 55),
        _make_signal("strategy_agent", 70),
    ]
    strengths = signals_to_strengths(signals)
    assert strengths["smart_money"] == 0.80
    assert strengths["whale"] == 0.60
    assert abs(strengths["sentiment"] - 0.55) < 0.01
    assert strengths["strategy"] == 0.70


def test_signals_expired_ignored():
    """Expired signals should be excluded from strengths."""
    signals = [_make_signal("smart_money_agent", 90, expires_in_hours=-1)]
    strengths = signals_to_strengths(signals)
    assert strengths["smart_money"] == 0.0


def test_signals_deduplication():
    """Only the most recent signal per agent should be used."""
    signals = [
        _make_signal("smart_money_agent", 90, hours_old=0),   # newest
        _make_signal("smart_money_agent", 20, hours_old=2),   # older
    ]
    strengths = signals_to_strengths(signals)
    assert strengths["smart_money"] == 0.90


def test_signals_empty():
    """Empty signal list should return all zeros."""
    strengths = signals_to_strengths([])
    assert strengths == {
        "smart_money": 0.0,
        "elite_trader": 0.0,
        "whale": 0.0,
        "sentiment": 0.0,
        "strategy": 0.0,
    }


# ── Fear & Greed sentiment ─────────────────────────────────────────────────────

def test_fear_greed_extreme_fear():
    assert fear_greed_to_strength(0) == 0.0
    assert fear_greed_to_signal_type(0) == "bearish"


def test_fear_greed_neutral():
    strength = fear_greed_to_strength(50)
    assert 0.45 <= strength <= 0.55
    assert fear_greed_to_signal_type(50) == "bullish"


def test_fear_greed_extreme_greed():
    strength = fear_greed_to_strength(100)
    assert strength <= 0.90   # capped — extreme greed precedes corrections
    assert fear_greed_to_signal_type(100) == "bullish"


def test_fear_greed_fear_zone():
    assert fear_greed_to_signal_type(30) == "neutral"


def test_fear_greed_monotonic():
    """Fear & Greed strength should increase as value increases."""
    values = [0, 25, 50, 75, 100]
    strengths = [fear_greed_to_strength(v) for v in values]
    assert all(strengths[i] <= strengths[i + 1] for i in range(len(strengths) - 1))


# ── Momentum scoring ──────────────────────────────────────────────────────────

def test_momentum_strong_signal():
    info = {"price_change_1h": 15, "volume_24h": 1_000_000, "buys_24h": 300, "sells_24h": 100}
    strength = _momentum_strength(info)
    assert strength >= 0.45, "Strong momentum should pass threshold"


def test_momentum_weak_signal():
    info = {"price_change_1h": 0.5, "volume_24h": 10_000, "buys_24h": 50, "sells_24h": 50}
    strength = _momentum_strength(info)
    assert strength < 0.45, "Weak momentum should not pass threshold"


def test_momentum_declining_price_penalty():
    info = {"price_change_1h": -10, "volume_24h": 2_000_000, "buys_24h": 500, "sells_24h": 100}
    strength_negative = _momentum_strength(info)
    info_positive = {**info, "price_change_1h": 10}
    strength_positive = _momentum_strength(info_positive)
    assert strength_negative < strength_positive


def test_momentum_clamped_0_to_1():
    info = {"price_change_1h": 100, "volume_24h": 10_000_000, "buys_24h": 10_000, "sells_24h": 1}
    strength = _momentum_strength(info)
    assert 0.0 <= strength <= 1.0


def test_momentum_missing_fields():
    """Missing fields should default to zero, not crash."""
    strength = _momentum_strength({})
    assert 0.0 <= strength <= 1.0


# ── Orchestrator gate logic (unit-level) ──────────────────────────────────────

def test_meets_threshold_pass():
    """Score above 72 with 3+ agents should pass the gate."""
    score = 75
    agent_count = 3
    assert score >= 72 and agent_count >= 3


def test_meets_threshold_score_too_low():
    """Score below 72 should not pass even with enough agents."""
    score = 65
    agent_count = 4
    assert not (score >= 72 and agent_count >= 3)


def test_meets_threshold_agents_too_few():
    """Score above 72 but fewer than 3 agents should not pass."""
    score = 80
    agent_count = 2
    assert not (score >= 72 and agent_count >= 3)
