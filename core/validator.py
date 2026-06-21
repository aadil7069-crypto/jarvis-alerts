import logging
import os

logger = logging.getLogger("jarvis.validator")

_WARNINGS = [
    ("HELIUS_API_KEY",        "Solana whale tracking disabled"),
    ("BSCSCAN_API_KEY",       "BNB chain on-chain data limited"),
    ("ANTHROPIC_API_KEY",     "AI learning and reasoning disabled"),
    ("TELEGRAM_BOT_TOKEN",    "Telegram notifications disabled"),
    ("TELEGRAM_CHAT_ID",      "Telegram notifications disabled"),
]

_LIVE_REQUIRED = [
    "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET",
]


def validate_startup(config: dict) -> bool:
    """
    Check all required secrets and config values on startup.
    Logs warnings for missing optional keys.
    Returns False and logs ERROR if required secrets for the current mode are absent.
    """
    ok = True
    mode = config.get("system", {}).get("mode", "paper")

    for key, impact in _WARNINGS:
        if not os.getenv(key):
            logger.warning(f"Missing {key} — {impact}")

    if mode == "live":
        for key in _LIVE_REQUIRED:
            if not os.getenv(key):
                logger.error(f"MISSING REQUIRED SECRET FOR LIVE MODE: {key}")
                ok = False

    if not ok:
        logger.error("Startup validation failed — fix missing secrets before running in live mode.")

    return ok
