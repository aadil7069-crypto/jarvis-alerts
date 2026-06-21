import logging

logger = logging.getLogger("jarvis.prediction_markets.backtester")


def evaluate_accuracy(db_session, days: int = 30) -> dict:
    """
    Compare historical prediction market probabilities with actual trade outcomes.

    Implementation roadmap (requires 30+ days of live data first):
    1. Query MarketProbability records over the past N days
    2. Match MacroRiskState entries with Trade P&L over the same period
    3. Calculate correlation between market sentiment and trade performance
    4. Measure how often RISK_OFF mode correctly avoided losses
    5. Compute calibration score (did 70% markets resolve ~70% of the time?)
    6. Generate a written report

    Status: data collection active since Phase 3 launch.
    Run this after 30+ days of live operation.
    """
    logger.info(f"Backtesting prediction market accuracy over {days} days...")

    # Placeholder until sufficient data is collected
    return {
        "status": "pending_data",
        "days_requested": days,
        "message": "Backtesting available after 30+ days of market data collection.",
    }


def generate_report(db_session) -> str:
    """Generate a plain-English accuracy report for the daily briefing."""
    result = evaluate_accuracy(db_session)
    if result["status"] == "pending_data":
        return result["message"]
    return f"Prediction market accuracy: {result}"
