import logging

logger = logging.getLogger("jarvis.prediction_markets.sentiment")

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"
RISK_OFF = "risk_off"

_RISK_WORDS = frozenset([
    "crash", "collapse", "ban", "hack", "fail", "below", "drop", "fall",
    "recession", "correction", "dump", "rug", "bankrupt", "contagion",
    "liquidat", "crisis", "panic", "bear",
])


def _is_risk_framing(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in _RISK_WORDS)


def market_to_sentiment(question: str, yes_probability: float) -> dict:
    """
    Convert a market's YES probability into a directional sentiment and numeric score.

    Risk-framing markets (e.g., "Will crypto crash?") are inverted:
      high YES prob → RISK_OFF  (bad for trading)

    Positive-framing markets (e.g., "Will BTC hit $100k?"):
      high YES prob → BULLISH
    """
    risk_framing = _is_risk_framing(question)

    if risk_framing:
        if yes_probability >= 0.60:
            sentiment = RISK_OFF
            # Score: -1 at 60%, -50 at 100%
            score = -int((yes_probability - 0.60) / 0.40 * 50)
        elif yes_probability >= 0.40:
            sentiment = NEUTRAL
            score = 0
        else:
            sentiment = BULLISH
            score = int((0.40 - yes_probability) / 0.40 * 20)
    else:
        if yes_probability >= 0.60:
            sentiment = BULLISH
            score = int((yes_probability - 0.60) / 0.40 * 30)
        elif yes_probability >= 0.40:
            sentiment = NEUTRAL
            score = 0
        else:
            sentiment = BEARISH
            score = -int((0.40 - yes_probability) / 0.40 * 30)

    return {
        "sentiment": sentiment,
        "score": score,
        "yes_probability": yes_probability,
        "is_risk_framing": risk_framing,
    }


def aggregate_sentiment(market_sentiments: list) -> dict:
    """
    Combine sentiment across multiple markets into one macro score.
    Risk-off signals are doubled in weight — capital preservation takes priority.
    """
    if not market_sentiments:
        return {"sentiment": NEUTRAL, "score": 0.0, "market_count": 0, "risk_off_signals": 0}

    total = 0.0
    risk_off_count = 0

    for ms in market_sentiments:
        s = ms.get("score", 0)
        if ms.get("sentiment") == RISK_OFF:
            s *= 2  # asymmetric weighting
            risk_off_count += 1
        total += s

    avg = total / len(market_sentiments)

    if risk_off_count >= 2 or avg <= -30:
        overall = RISK_OFF
    elif avg >= 15:
        overall = BULLISH
    elif avg <= -10:
        overall = BEARISH
    else:
        overall = NEUTRAL

    return {
        "sentiment": overall,
        "score": round(avg, 2),
        "market_count": len(market_sentiments),
        "risk_off_signals": risk_off_count,
    }
