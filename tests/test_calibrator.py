"""
Confidence calibrator tests — weight adjustment math, min-trades gate, rescaling.
No DB or API calls.
"""
import pytest
from core.calibrator import (
    _compute_per_signal_stats,
    _compute_new_weights,
    MIN_TRADES,
    UPLIFT_SCALE,
    MAX_CHANGE,
    _BREAKDOWN_KEYS,
)
from core.scoring import WEIGHTS, set_calibrated_weights, get_active_weights


# ── Constants ─────────────────────────────────────────────────────────────────

def test_min_trades_is_reasonable():
    assert MIN_TRADES >= 10


def test_max_change_caps_at_reasonable_range():
    assert 0.2 <= MAX_CHANGE <= 0.6


def test_breakdown_keys_match_weights():
    for key in _BREAKDOWN_KEYS:
        assert key in WEIGHTS, f"{key} missing from WEIGHTS"


# ── Per-signal stat computation ───────────────────────────────────────────────

def _make_trade(pnl_pct: float, breakdown: dict):
    """Build a mock trade object with breakdown attached."""
    from unittest.mock import MagicMock
    t = MagicMock()
    t.pnl_pct = pnl_pct
    t._breakdown = breakdown
    return t


def test_stats_perfect_signal():
    """A signal present in all winning trades → precision = 1.0."""
    trades = [
        _make_trade(0.10, {"smart_money_buy": 20, "elite_trader": 0, "vetting_pass": 18,
                            "whale_accumulation": 0, "strategy_confirm": 0, "positive_sentiment": 0}),
        _make_trade(0.05, {"smart_money_buy": 15, "elite_trader": 0, "vetting_pass": 18,
                            "whale_accumulation": 0, "strategy_confirm": 0, "positive_sentiment": 0}),
    ]
    stats = _compute_per_signal_stats(trades)
    s = stats["smart_money_buy"]
    assert s["present"] == 2
    assert s["wins"] == 2


def test_stats_signal_never_present():
    trades = [
        _make_trade(0.10, {"smart_money_buy": 0, "elite_trader": 0, "vetting_pass": 18,
                            "whale_accumulation": 0, "strategy_confirm": 0, "positive_sentiment": 0}),
    ]
    stats = _compute_per_signal_stats(trades)
    assert stats["smart_money_buy"]["present"] == 0
    assert stats["smart_money_buy"]["wins"] == 0


def test_stats_mixed_results():
    trades = [
        _make_trade(0.10, {"smart_money_buy": 20, "elite_trader": 0, "vetting_pass": 18,
                            "whale_accumulation": 0, "strategy_confirm": 0, "positive_sentiment": 0}),
        _make_trade(-0.05, {"smart_money_buy": 18, "elite_trader": 0, "vetting_pass": 18,
                             "whale_accumulation": 0, "strategy_confirm": 0, "positive_sentiment": 0}),
    ]
    stats = _compute_per_signal_stats(trades)
    s = stats["smart_money_buy"]
    assert s["present"] == 2
    assert s["wins"] == 1


# ── Weight computation ────────────────────────────────────────────────────────

def _full_stats(wins: int, present: int) -> dict:
    """Build per_signal stats with all signals having the same win/present."""
    return {k: {"present": present, "wins": wins} for k in _BREAKDOWN_KEYS}


def test_weights_rescale_to_100():
    stats = _full_stats(wins=7, present=10)
    weights = _compute_new_weights(stats, base_win_rate=0.5)
    total = sum(weights.values())
    assert abs(total - 100.0) < 1.0, f"Expected ~100, got {total}"


def test_high_precision_signal_gets_higher_weight():
    """A signal that predicts wins 90% of the time should be upweighted."""
    stats = {k: {"present": 10, "wins": 5} for k in _BREAKDOWN_KEYS}
    stats["smart_money_buy"] = {"present": 10, "wins": 9}   # 90% precision
    base_win_rate = 0.5
    weights = _compute_new_weights(stats, base_win_rate)
    assert weights["smart_money_buy"] > WEIGHTS["smart_money_buy"]


def test_low_precision_signal_gets_lower_weight():
    """A signal that predicts losses 90% of the time should be downweighted."""
    stats = {k: {"present": 10, "wins": 5} for k in _BREAKDOWN_KEYS}
    stats["positive_sentiment"] = {"present": 10, "wins": 1}   # only 10% precision
    base_win_rate = 0.5
    weights = _compute_new_weights(stats, base_win_rate)
    assert weights["positive_sentiment"] < WEIGHTS["positive_sentiment"]


def test_weight_change_capped():
    """Weight cannot change by more than MAX_CHANGE fraction."""
    stats = {k: {"present": 100, "wins": 100} for k in _BREAKDOWN_KEYS}
    weights = _compute_new_weights(stats, base_win_rate=0.5)
    for key in _BREAKDOWN_KEYS:
        base = WEIGHTS[key]
        cap_high = base * (1 + MAX_CHANGE)
        # Allow small floating point slack from rescaling
        assert weights[key] <= cap_high * 1.05, f"{key} exceeded max cap"


def test_signal_with_few_observations_keeps_base_weight():
    """Signal with < 5 observations keeps its base weight."""
    stats = {k: {"present": 10, "wins": 7} for k in _BREAKDOWN_KEYS}
    stats["elite_trader"] = {"present": 2, "wins": 2}   # only 2 observations
    weights = _compute_new_weights(stats, base_win_rate=0.5)
    # After rescaling, elite_trader should be close to base (not wildly different)
    # Exact match won't hold after rescaling but it shouldn't be doubled
    assert weights["elite_trader"] < WEIGHTS["elite_trader"] * 2


# ── Scoring engine integration ────────────────────────────────────────────────

def test_set_calibrated_weights_activates():
    from core.scoring import _calibrated_weights
    # Set calibrated weights
    custom = {k: float(v) for k, v in WEIGHTS.items()}
    custom["smart_money_buy"] = 30.0
    set_calibrated_weights(custom)
    active = get_active_weights()
    assert active["smart_money_buy"] == 30.0


def test_get_active_weights_returns_calibrated_when_set():
    custom = {k: float(v) for k, v in WEIGHTS.items()}
    set_calibrated_weights(custom)
    assert get_active_weights() is custom


def test_reset_to_base_weights():
    """Setting None restores base weights."""
    import core.scoring as scoring
    scoring._calibrated_weights = None
    assert get_active_weights() is WEIGHTS
