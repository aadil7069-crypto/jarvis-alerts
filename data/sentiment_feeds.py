import logging
import requests

logger = logging.getLogger("jarvis.data.sentiment_feeds")


def get_fear_greed() -> dict:
    """
    Fetch the Crypto Fear & Greed Index from Alternative.me.
    Free, no API key, updated daily.
    Returns value 0-100: 0=Extreme Fear, 100=Extreme Greed.
    """
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        r.raise_for_status()
        data = r.json()["data"][0]
        return {
            "value": int(data["value"]),
            "classification": data["value_classification"],
            "timestamp": data["timestamp"],
        }
    except Exception as e:
        logger.error(f"Fear & Greed fetch failed: {e}")
        return {"value": 50, "classification": "Neutral", "timestamp": None}


def fear_greed_to_strength(value: int) -> float:
    """
    Convert a Fear & Greed value (0-100) to a sentiment strength (0.0-1.0).

    Extreme fear → 0.0 (negative signal)
    Neutral (50)  → 0.5
    Extreme greed → 0.9 (not 1.0 — extreme greed historically precedes corrections)
    """
    if value < 25:
        return max(0.0, value / 25 * 0.15)   # 0.00 – 0.15
    elif value < 50:
        return 0.15 + (value - 25) / 25 * 0.35   # 0.15 – 0.50
    elif value < 75:
        return 0.50 + (value - 50) / 25 * 0.30   # 0.50 – 0.80
    else:
        return 0.80 + (value - 75) / 25 * 0.10   # 0.80 – 0.90


def fear_greed_to_signal_type(value: int) -> str:
    if value < 25:
        return "bearish"
    elif value < 45:
        return "neutral"
    elif value < 75:
        return "bullish"
    else:
        return "bullish"   # extreme greed still bullish short-term


def get_trending_coins() -> list:
    """
    Fetch the top trending coins from CoinGecko (free, no API key).
    Returns list of coin dicts with id, name, symbol, market_cap_rank.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=10,
        )
        r.raise_for_status()
        coins = r.json().get("coins", [])
        return [
            {
                "id": c["item"]["id"],
                "name": c["item"]["name"],
                "symbol": c["item"]["symbol"],
                "market_cap_rank": c["item"].get("market_cap_rank"),
                "score": c["item"].get("score", 0),
            }
            for c in coins
        ]
    except Exception as e:
        logger.error(f"CoinGecko trending fetch failed: {e}")
        return []


def get_global_market_data() -> dict:
    """
    Fetch global crypto market stats: total market cap, 24h change, BTC dominance.
    Used to determine overall market direction.
    """
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        r.raise_for_status()
        d = r.json().get("data", {})
        return {
            "total_market_cap_usd": d.get("total_market_cap", {}).get("usd", 0),
            "market_cap_change_24h_pct": d.get("market_cap_change_percentage_24h_usd", 0),
            "btc_dominance": d.get("market_cap_percentage", {}).get("btc", 0),
            "active_coins": d.get("active_cryptocurrencies", 0),
        }
    except Exception as e:
        logger.error(f"CoinGecko global data fetch failed: {e}")
        return {}
