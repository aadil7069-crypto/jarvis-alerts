"""
Confidence Calibrator
=====================
Reweights the 6 scoring signals based on historical paper trading outcomes.

Algorithm (per signal category):
  1. Scan all closed Trade records that have an associated TradeIdea with
     a score_breakdown JSON field (populated since Phase 4).
  2. For each trade, determine whether the signal category contributed
     (breakdown[category] > 0) and whether the trade was a win (pnl_pct > 0).
  3. Compute per-signal precision = wins where present / total where present.
  4. Compute uplift = precision - base_win_rate  (how much better than random)
  5. Adjust weight: new_weight = base_weight * (1 + UPLIFT_SCALE * uplift)
     clamped to [base * (1 - MAX_CHANGE), base * (1 + MAX_CHANGE)]

Results are stored in the CalibrationWeight table (one row per calibration run)
and activated in the scoring engine via scoring.set_calibrated_weights().

Minimum trades: 20. Below this threshold returns None (base weights unchanged).
"""
import json
import logging
from datetime import datetime, timezone

from core.scoring import WEIGHTS
from models.schema import CalibrationWeight, Trade, TradeIdea

logger = logging.getLogger("jarvis.calibrator")

MIN_TRADES = 20       # minimum closed trades before calibration is meaningful
UPLIFT_SCALE = 1.5   # how aggressively to move weights (1.5 = moderate)
MAX_CHANGE = 0.40    # cap adjustment at ±40% of base weight

# Map score_breakdown keys → CalibrationWeight column names
_BREAKDOWN_KEYS = [
    "smart_money_buy",
    "elite_trader",
    "vetting_pass",
    "whale_accumulation",
    "strategy_confirm",
    "positive_sentiment",
]


def calibrate(session_factory) -> dict | None:
    """
    Run calibration against all closed paper trades.

    Returns a dict of calibrated weights {category: float} if MIN_TRADES is met,
    or None if insufficient data.

    Side effect: writes a CalibrationWeight row to the DB.
    """
    db = session_factory()
    try:
        trades = _load_closed_trades(db)
        if len(trades) < MIN_TRADES:
            logger.info(
                f"Calibration deferred — {len(trades)}/{MIN_TRADES} closed trades available"
            )
            return None

        base_win_rate = sum(1 for t in trades if (t.pnl_pct or 0) > 0) / len(trades)
        per_signal = _compute_per_signal_stats(trades)
        calibrated = _compute_new_weights(per_signal, base_win_rate)

        _persist(db, calibrated, len(trades), base_win_rate)

        logger.info(
            f"Calibration complete | {len(trades)} trades | "
            f"win_rate={base_win_rate:.1%} | "
            + " ".join(f"{k}={v:.1f}" for k, v in calibrated.items())
        )
        return calibrated

    except Exception as e:
        logger.error(f"Calibration failed: {e}")
        return None
    finally:
        db.close()


def _load_closed_trades(db) -> list:
    """Load closed trades that have a linked TradeIdea with score breakdown."""
    trades = (
        db.query(Trade, TradeIdea)
        .join(TradeIdea, Trade.trade_idea_id == TradeIdea.id, isouter=True)
        .filter(Trade.status == "closed", Trade.is_paper == True)
        .all()
    )
    result = []
    for trade, idea in trades:
        if not idea or not idea.score_breakdown:
            continue
        try:
            breakdown = json.loads(idea.score_breakdown)
        except (json.JSONDecodeError, TypeError):
            continue
        trade._breakdown = breakdown   # attach for use in stats computation
        result.append(trade)
    return result


def _compute_per_signal_stats(trades: list) -> dict:
    """
    For each signal category, compute:
      present_count: trades where this signal contributed (score > 0)
      win_count:     wins among those trades
    """
    stats = {k: {"present": 0, "wins": 0} for k in _BREAKDOWN_KEYS}
    for trade in trades:
        is_win = (trade.pnl_pct or 0) > 0
        for key in _BREAKDOWN_KEYS:
            if trade._breakdown.get(key, 0) > 0:
                stats[key]["present"] += 1
                if is_win:
                    stats[key]["wins"] += 1
    return stats


def _compute_new_weights(per_signal: dict, base_win_rate: float) -> dict:
    """
    Compute calibrated weights. Signal categories with no data keep base weight.
    """
    new_weights = {}
    base_total = sum(WEIGHTS.values())

    for key in _BREAKDOWN_KEYS:
        base = WEIGHTS.get(key, 10)
        stat = per_signal.get(key, {})
        present = stat.get("present", 0)
        wins = stat.get("wins", 0)

        if present < 5:
            # Not enough observations — keep base weight
            new_weights[key] = float(base)
            continue

        precision = wins / present
        uplift = precision - base_win_rate
        adjustment = 1.0 + UPLIFT_SCALE * uplift
        adjustment = max(1.0 - MAX_CHANGE, min(1.0 + MAX_CHANGE, adjustment))
        new_weights[key] = round(base * adjustment, 2)

    # Rescale so weights still sum to 100
    raw_total = sum(new_weights.values())
    if raw_total > 0:
        scale = base_total / raw_total
        new_weights = {k: round(v * scale, 2) for k, v in new_weights.items()}

    return new_weights


def _persist(db, weights: dict, trade_count: int, base_win_rate: float) -> None:
    try:
        db.add(CalibrationWeight(
            calibrated_at=datetime.now(timezone.utc),
            trade_count=trade_count,
            base_win_rate=base_win_rate,
            smart_money_buy=weights.get("smart_money_buy"),
            elite_trader=weights.get("elite_trader"),
            vetting_pass=weights.get("vetting_pass"),
            whale_accumulation=weights.get("whale_accumulation"),
            strategy_confirm=weights.get("strategy_confirm"),
            positive_sentiment=weights.get("positive_sentiment"),
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"CalibrationWeight persist failed: {e}")
