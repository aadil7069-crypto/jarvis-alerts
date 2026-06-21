import logging
from typing import Optional

logger = logging.getLogger("jarvis.prediction_markets.event_detector")


def detect_probability_change(
    old_prob: float,
    new_prob: float,
    threshold: float = 0.10,
) -> Optional[dict]:
    """Return an event dict if the probability shift exceeds the threshold."""
    change = new_prob - old_prob
    if abs(change) < threshold:
        return None
    return {
        "type": "probability_shift",
        "old": old_prob,
        "new": new_prob,
        "change": change,
        "direction": "up" if change > 0 else "down",
        "magnitude": "major" if abs(change) >= 0.20 else "significant",
    }


def detect_approaching_resolution(yes_probability: float, threshold: float = 0.85) -> bool:
    """True if a market is very close to resolving one way or the other."""
    return yes_probability >= threshold or yes_probability <= (1.0 - threshold)


def classify_market_type(question: str, config: dict) -> str:
    """
    Classify a market as 'crypto', 'macro', 'regulatory', or 'other'.
    Determines how it is weighted in confidence scoring.
    """
    q = question.lower()
    pm = config.get("prediction_markets", {})

    if any(kw in q for kw in pm.get("crypto_keywords", [])):
        return "crypto"
    if any(kw in q for kw in pm.get("macro_keywords", [])):
        return "macro"
    if any(w in q for w in ("regulat", "sec ", "cftc", "ban ", "law ", "legislation")):
        return "regulatory"
    return "other"
