import logging
import requests

logger = logging.getLogger("jarvis.data.price_feeds")


def get_price(coin_id: str, currency: str = "usd") -> float:
    """Fetch current price from CoinGecko (free, no API key required)."""
    try:
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies={currency}"
        )
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()[coin_id][currency]
    except Exception as e:
        logger.error(f"Failed to fetch price for {coin_id}: {e}")
        return 0.0


def get_bitcoin_price() -> float:
    return get_price("bitcoin")


def get_solana_price() -> float:
    return get_price("solana")


def get_bnb_price() -> float:
    return get_price("binancecoin")


def get_btc_ohlcv(days: int = 7) -> list:
    """
    Fetch BTC OHLCV candles from CoinGecko for market regime detection.

    days=7  → 4-hour candles (42 candles)
    days=1  → 30-minute candles (48 candles)

    Returns list of {"time", "open", "high", "low", "close", "volume"} dicts,
    oldest first. Volume is always 0.0 (not provided by this endpoint).
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()   # [[timestamp_ms, open, high, low, close], ...]
        return [
            {
                "time":   int(entry[0] / 1000),
                "open":   float(entry[1]),
                "high":   float(entry[2]),
                "low":    float(entry[3]),
                "close":  float(entry[4]),
                "volume": 0.0,
            }
            for entry in raw
            if len(entry) >= 5
        ]
    except Exception as e:
        logger.error(f"BTC OHLCV fetch failed: {e}")
        return []
