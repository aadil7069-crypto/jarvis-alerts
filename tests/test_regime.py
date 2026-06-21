"""
Market regime classifier tests — detection logic, ATR, SMA, position multipliers.
No real API calls.
"""
import pytest
from core.regime import (
    detect_regime,
    regime_position_multiplier,
    _compute_atr,
    _MIN_CANDLES,
    _TREND_MOMENTUM,
    _VOLATILE_ATR_PCT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candles(count: int, base_price: float = 50_000.0, trend_pct: float = 0.0) -> list:
    """Generate synthetic OHLCV candles with a linear trend."""
    result = []
    price = base_price
    for i in range(count):
        price *= (1 + trend_pct)
        result.append({
            "time":   1700000000 + i * 14400,
            "open":   price * 0.998,
            "high":   price * 1.003,
            "low":    price * 0.997,
            "close":  price,
            "volume": 1_000_000.0,
        })
    return result


def _volatile_candles(count: int, base_price: float = 50_000.0) -> list:
    """Generate candles with very wide high-low ranges (> 5% ATR)."""
    result = []
    price = base_price
    for i in range(count):
        result.append({
            "time":   1700000000 + i * 14400,
            "open":   price * 0.97,
            "high":   price * 1.06,   # 6% high
            "low":    price * 0.94,   # 6% low → ATR >> 5%
            "close":  price,
            "volume": 1_000_000.0,
        })
    return result


# ── Constants ─────────────────────────────────────────────────────────────────

def test_min_candles_is_20():
    assert _MIN_CANDLES == 20


def test_trend_momentum_threshold():
    assert _TREND_MOMENTUM == pytest.approx(0.03)


def test_volatile_atr_threshold():
    assert _VOLATILE_ATR_PCT == pytest.approx(0.05)


# ── Insufficient data → unknown ───────────────────────────────────────────────

def test_regime_unknown_on_empty():
    result = detect_regime([])
    assert result["regime"] == "unknown"


def test_regime_unknown_below_min_candles():
    candles = _candles(10)
    result = detect_regime(candles)
    assert result["regime"] == "unknown"
    assert "reason" in result


def test_regime_unknown_exactly_at_min_minus_1():
    candles = _candles(_MIN_CANDLES - 1)
    assert detect_regime(candles)["regime"] == "unknown"


def test_regime_valid_at_min_candles():
    candles = _candles(_MIN_CANDLES)
    result = detect_regime(candles)
    assert result["regime"] != "unknown"


# ── Trending up ───────────────────────────────────────────────────────────────

def test_regime_trending_up():
    candles = _candles(40, trend_pct=0.008)   # +0.8% per 4h candle = strong uptrend
    result = detect_regime(candles)
    assert result["regime"] == "trending_up"
    assert result["momentum"] > _TREND_MOMENTUM


def test_trending_up_has_positive_momentum():
    candles = _candles(40, trend_pct=0.005)
    result = detect_regime(candles)
    if result["regime"] == "trending_up":
        assert result["momentum"] > 0


# ── Trending down ─────────────────────────────────────────────────────────────

def test_regime_trending_down():
    candles = _candles(40, trend_pct=-0.008)  # -0.8% per 4h candle = downtrend
    result = detect_regime(candles)
    assert result["regime"] == "trending_down"
    assert result["momentum"] < -_TREND_MOMENTUM


# ── Ranging ───────────────────────────────────────────────────────────────────

def test_regime_ranging_flat_market():
    candles = _candles(40, trend_pct=0.0)   # perfectly flat price
    result = detect_regime(candles)
    assert result["regime"] == "ranging"
    assert abs(result["momentum"]) <= _TREND_MOMENTUM


# ── Volatile ─────────────────────────────────────────────────────────────────

def test_regime_volatile_wide_candles():
    candles = _volatile_candles(40)
    result = detect_regime(candles)
    assert result["regime"] == "volatile"
    assert result["atr_pct"] > _VOLATILE_ATR_PCT


def test_volatile_detected_before_trend():
    """A volatile + uptrending market should still return 'volatile'."""
    # Wide candles but slight uptrend
    price = 50_000.0
    candles = []
    for i in range(40):
        price *= 1.002   # slight uptrend
        candles.append({
            "time": 1700000000 + i * 14400,
            "open": price * 0.96,
            "high": price * 1.07,   # 7% high — very volatile
            "low":  price * 0.93,
            "close": price,
            "volume": 1_000_000.0,
        })
    result = detect_regime(candles)
    assert result["regime"] == "volatile"


# ── Result structure ──────────────────────────────────────────────────────────

def test_result_has_all_keys():
    candles = _candles(40)
    result = detect_regime(candles)
    for key in ("regime", "sma", "current", "atr", "atr_pct", "momentum"):
        assert key in result


def test_current_price_is_last_close():
    candles = _candles(40, base_price=60_000.0)
    result = detect_regime(candles)
    assert result["current"] == pytest.approx(candles[-1]["close"], rel=0.01)


def test_sma_within_price_range():
    candles = _candles(40, base_price=50_000.0)
    closes = [c["close"] for c in candles]
    result = detect_regime(candles)
    assert min(closes) <= result["sma"] <= max(closes)


# ── Position multipliers ──────────────────────────────────────────────────────

def test_multiplier_trending_up_is_1():
    assert regime_position_multiplier("trending_up") == 1.0


def test_multiplier_trending_down_is_low():
    assert regime_position_multiplier("trending_down") <= 0.25


def test_multiplier_volatile_is_reduced():
    assert regime_position_multiplier("volatile") < 1.0


def test_multiplier_ranging_is_reduced():
    m = regime_position_multiplier("ranging")
    assert 0.0 < m < 1.0


def test_multiplier_unknown_is_conservative():
    m = regime_position_multiplier("unknown")
    assert 0.5 <= m <= 0.9   # not full size, not zero


def test_multiplier_all_regimes_between_0_and_1():
    for regime in ("trending_up", "trending_down", "ranging", "volatile", "unknown"):
        m = regime_position_multiplier(regime)
        assert 0.0 < m <= 1.0, f"{regime} multiplier {m} out of range"


# ── ATR computation ───────────────────────────────────────────────────────────

def test_atr_positive_for_non_zero_ranges():
    highs  = [100.0] * 20
    lows   = [95.0]  * 20
    closes = [97.0]  * 20
    atr = _compute_atr(highs, lows, closes, 14)
    assert atr > 0.0


def test_atr_zero_for_empty():
    assert _compute_atr([], [], [], 14) == 0.0


def test_atr_wider_candles_higher_atr():
    highs_narrow = [102.0] * 20
    lows_narrow  = [99.0]  * 20
    highs_wide   = [110.0] * 20
    lows_wide    = [90.0]  * 20
    closes = [100.0] * 20
    atr_narrow = _compute_atr(highs_narrow, lows_narrow, closes, 14)
    atr_wide   = _compute_atr(highs_wide,   lows_wide,   closes, 14)
    assert atr_wide > atr_narrow
