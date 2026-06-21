import json
import logging
import requests
from typing import Optional

logger = logging.getLogger("jarvis.prediction_markets.polymarket")

_GAMMA = "https://gamma-api.polymarket.com"
_CLOB = "https://clob.polymarket.com"


def get_active_markets(limit: int = 200, offset: int = 0) -> list:
    """Fetch currently active Polymarket prediction markets."""
    try:
        r = requests.get(
            f"{_GAMMA}/markets",
            params={"active": "true", "closed": "false", "limit": limit, "offset": offset},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch active markets: {e}")
        return []


def get_market(market_id: str) -> Optional[dict]:
    """Fetch a single market by its Polymarket ID."""
    try:
        r = requests.get(f"{_GAMMA}/markets/{market_id}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch market {market_id}: {e}")
        return None


def get_price_history(market_id: str, fidelity: int = 60) -> list:
    """
    Fetch historical probability data for a market.
    fidelity = data point interval in minutes (60 = hourly snapshots).
    """
    try:
        r = requests.get(
            f"{_CLOB}/prices-history",
            params={"market": market_id, "fidelity": fidelity},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("history", [])
    except Exception as e:
        logger.error(f"Failed to fetch price history for {market_id}: {e}")
        return []


def get_events(limit: int = 50) -> list:
    """Fetch active Polymarket events (groups of related markets)."""
    try:
        r = requests.get(
            f"{_GAMMA}/events",
            params={"active": "true", "closed": "false", "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch events: {e}")
        return []


def parse_market(raw: dict) -> dict:
    """Normalise a raw Polymarket market object into a clean dict."""
    outcomes = raw.get("outcomes", "[]")
    prices = raw.get("outcomePrices", "[]")

    # Some API versions return these as JSON strings
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []

    outcome_data = [
        {"outcome": o, "probability": float(prices[i]) if i < len(prices) else 0.0}
        for i, o in enumerate(outcomes)
    ]

    yes_prob = float(prices[0]) if prices else 0.0

    return {
        "id": raw.get("id", ""),
        "question": raw.get("question", ""),
        "category": raw.get("category", ""),
        "outcomes": outcome_data,
        "yes_probability": yes_prob,
        "volume": float(raw.get("volumeNum", 0) or 0),
        "active": raw.get("active", False),
        "closed": raw.get("closed", False),
        "end_date": raw.get("endDate"),
        "start_date": raw.get("startDate"),
    }
