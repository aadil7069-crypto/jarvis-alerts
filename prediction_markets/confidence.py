import logging

logger = logging.getLogger("jarvis.prediction_markets.confidence")


def calculate_modifier(macro_sentiment: dict, config: dict) -> dict:
    """
    Convert macro sentiment into a confidence score modifier.

    Prediction markets NEVER act as a standalone trade signal.
    They only shift the existing confidence score up or down.
    The modifier is capped by max_confidence_bonus / max_confidence_penalty in config.
    """
    pm = config.get("prediction_markets", {})
    max_bonus = pm.get("max_confidence_bonus", 10)
    max_penalty = pm.get("max_confidence_penalty", 25)

    sentiment = macro_sentiment.get("sentiment", NEUTRAL)
    score = macro_sentiment.get("score", 0.0)

    if sentiment == "risk_off":
        modifier = -max_penalty
        reason = f"Risk-off macro environment (prediction markets, score: {score:.1f})"
    elif sentiment == "bullish":
        raw = min((score / 30.0) * max_bonus, max_bonus)
        modifier = round(raw)
        reason = f"Bullish macro environment (prediction markets, score: {score:.1f})"
    elif sentiment == "bearish":
        raw = max((score / 30.0) * max_penalty, -max_penalty)
        modifier = round(raw)
        reason = f"Bearish macro environment (prediction markets, score: {score:.1f})"
    else:
        modifier = 0
        reason = "Neutral macro environment"

    return {
        "modifier": modifier,
        "reason": reason,
        "sentiment": sentiment,
        "safe_mode_triggered": sentiment == "risk_off",
    }


NEUTRAL = "neutral"
