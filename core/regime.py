"""
Market Regime Classifier
========================
Classifies the current crypto market into one of four regimes using BTC OHLCV:

  trending_up   — BTC above 20-SMA, momentum > +3%
  trending_down — BTC below 20-SMA, momentum < -3%
  ranging       — price near SMA, no directional conviction
  volatile      — ATR > 5% of current price (large candles, unpredictable)

Volatile is evaluated first: a market can trend AND be volatile simultaneously.
When volatility is extreme the size reduction takes priority over direction.

Position size multipliers per regime (applied on top of PM safe-mode multiplier):
  trending_up:   1.0  — ideal conditions, full size
  ranging:       0.6  — momentum strategies underperform in choppy markets
  volatile:      0.5  — risk management; stops hit more frequently
  trending_down: 0.2  — don't buy a falling market
  unknown:       0.7  — conservative default until first BTC data arrives
"""

_MIN_CANDLES = 20
_ATR_PERIOD = 14
_SMA_PERIOD = 20
_VOLATILE_ATR_PCT = 0.05   # ATR > 5% of price → volatile
_TREND_MOMENTUM = 0.03     # |momentum| > 3% → trending

_MULTIPLIERS = {
    "trending_up":   1.0,
    "ranging":       0.6,
    "volatile":      0.5,
    "trending_down": 0.2,
    "unknown":       0.7,
}


def detect_regime(ohlcv: list) -> dict:
    """
    Classify market regime from a list of OHLCV candle dicts (oldest first).

    Each candle: {"time": int, "open": float, "high": float,
                  "low": float, "close": float, "volume": float}

    Returns:
      regime:    "trending_up" | "trending_down" | "ranging" | "volatile" | "unknown"
      sma:       20-period SMA of close prices
      current:   latest close price
      atr:       14-period average true range
      atr_pct:   ATR as fraction of current price
      momentum:  (current - sma) / sma
    """
    if len(ohlcv) < _MIN_CANDLES:
        return _unknown(f"Insufficient candles ({len(ohlcv)} < {_MIN_CANDLES})")

    closes = [float(c["close"]) for c in ohlcv]
    highs  = [float(c["high"])  for c in ohlcv]
    lows   = [float(c["low"])   for c in ohlcv]

    current = closes[-1]
    if current <= 0:
        return _unknown("current price is zero")

    sma = sum(closes[-_SMA_PERIOD:]) / _SMA_PERIOD
    atr = _compute_atr(highs, lows, closes, _ATR_PERIOD)
    atr_pct = atr / current
    momentum = (current - sma) / sma if sma > 0 else 0.0

    if atr_pct > _VOLATILE_ATR_PCT:
        regime = "volatile"
    elif momentum > _TREND_MOMENTUM:
        regime = "trending_up"
    elif momentum < -_TREND_MOMENTUM:
        regime = "trending_down"
    else:
        regime = "ranging"

    return {
        "regime": regime,
        "sma": round(sma, 4),
        "current": round(current, 4),
        "atr": round(atr, 4),
        "atr_pct": round(atr_pct, 4),
        "momentum": round(momentum, 4),
    }


def regime_position_multiplier(regime: str) -> float:
    """Return position size multiplier for a given regime (0.2 – 1.0)."""
    return _MULTIPLIERS.get(regime, 0.7)


def _compute_atr(highs: list, lows: list, closes: list, period: int) -> float:
    """14-period Average True Range using Wilder's method."""
    if len(highs) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(highs)):
        prev_close = closes[i - 1]
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
        true_ranges.append(tr)

    recent = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
    return sum(recent) / len(recent) if recent else 0.0


def _unknown(reason: str = "") -> dict:
    return {
        "regime": "unknown",
        "sma": 0.0,
        "current": 0.0,
        "atr": 0.0,
        "atr_pct": 0.0,
        "momentum": 0.0,
        "reason": reason,
    }
