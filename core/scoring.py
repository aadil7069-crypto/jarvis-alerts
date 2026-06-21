import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("jarvis.scoring")

# Signal weights — reliability hierarchy:
# On-chain (smart money, elite, whale) > Safety (vetting) > Strategy > Sentiment
#
# Phase 6 upgrade: elite_trader added as a separate on-chain category.
# smart_money (GMGN) and elite_trader (Birdeye) are independent sources — both
# scoring well is a strong convergence signal. Weights reduced proportionally.
WEIGHTS = {
    "smart_money_buy":     22,   # GMGN smart money wallets
    "elite_trader":        15,   # Birdeye top-20 daily P&L traders
    "vetting_pass":        18,   # Safety pipeline (GoPlus + Honeypot)
    "whale_accumulation":  18,   # Large wallet accumulation
    "strategy_confirm":    15,   # Momentum (price/volume/buy ratio)
    "positive_sentiment":  12,   # Fear & Greed + market direction
}

assert sum(WEIGHTS.values()) == 100, "Weights must sum to 100"

# Calibrated weights override WEIGHTS when set by the LearningAgent.
# None = base WEIGHTS are used (default until enough trade history exists).
_calibrated_weights: dict | None = None


def set_calibrated_weights(weights: dict) -> None:
    """Activate calibrated weights produced by core/calibrator.py."""
    global _calibrated_weights
    _calibrated_weights = weights
    logger.info(
        "Calibrated weights activated: "
        + " ".join(f"{k}={v:.1f}" for k, v in weights.items())
    )


def get_active_weights() -> dict:
    """Return calibrated weights if available, otherwise base WEIGHTS."""
    return _calibrated_weights if _calibrated_weights is not None else WEIGHTS


def compute_score(
    vetting_passed: bool,
    smart_money_strength: float = 0.0,
    elite_trader_strength: float = 0.0,
    whale_strength: float = 0.0,
    sentiment_strength: float = 0.0,
    strategy_strength: float = 0.0,
    pm_modifier: int = 0,
) -> dict:
    """
    Compute a 0-100 confidence score from individual signal strengths.

    strength values: 0.0 (no signal) to 1.0 (maximum confidence).
    pm_modifier: prediction market adjustment, typically −25 to +10.

    Prediction markets never act as a standalone signal — they only shift
    the base score computed from agent signals.
    """
    def _clamp(v: float) -> float:
        return min(1.0, max(0.0, v))

    w = get_active_weights()
    breakdown = {
        "vetting_pass":       w["vetting_pass"] if vetting_passed else 0,
        "smart_money_buy":    round(w["smart_money_buy"]    * _clamp(smart_money_strength)),
        "elite_trader":       round(w["elite_trader"]       * _clamp(elite_trader_strength)),
        "whale_accumulation": round(w["whale_accumulation"] * _clamp(whale_strength)),
        "positive_sentiment": round(w["positive_sentiment"] * _clamp(sentiment_strength)),
        "strategy_confirm":   round(w["strategy_confirm"]   * _clamp(strategy_strength)),
    }

    base_score = sum(breakdown.values())
    final_score = max(0, min(100, base_score + pm_modifier))

    return {
        "total": final_score,
        "base_score": base_score,
        "pm_modifier": pm_modifier,
        "breakdown": breakdown,
        "meets_threshold": False,   # caller sets this against their configured threshold
        "vetting_passed": vetting_passed,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def signals_to_strengths(signals: list) -> dict:
    """
    Convert a list of Signal ORM objects into per-category strength values.

    Rules:
    - Expired signals are ignored.
    - Only the strongest signal per agent is used (prevents double-counting).
    - Returns strengths in range 0.0–1.0.
    """
    now = datetime.now(timezone.utc)
    best: dict[str, float] = {
        "smart_money":   0.0,
        "elite_trader":  0.0,
        "whale":         0.0,
        "sentiment":     0.0,
        "strategy":      0.0,
    }

    seen: set[str] = set()

    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _sort_key(s):
        ts = s.created_at
        if ts is None:
            return _epoch
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    for sig in sorted(signals, key=_sort_key, reverse=True):
        if sig.expires_at:
            exp = sig.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < now:
                continue                 # expired — ignore

        key = sig.agent_name
        if key in seen:
            continue                     # only use the most recent per agent
        seen.add(key)

        strength = min(1.0, max(0.0, (sig.strength or 0) / 100.0))

        if "smart_money" in key:
            best["smart_money"] = max(best["smart_money"], strength)
        elif "elite_trader" in key or "elite" in key:
            best["elite_trader"] = max(best["elite_trader"], strength)
        elif "whale" in key:
            best["whale"] = max(best["whale"], strength)
        elif "sentiment" in key:
            best["sentiment"] = max(best["sentiment"], strength)
        elif "strategy" in key:
            best["strategy"] = max(best["strategy"], strength)

    return best
