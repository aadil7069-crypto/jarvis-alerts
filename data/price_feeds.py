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
