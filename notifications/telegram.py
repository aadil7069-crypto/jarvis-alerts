import logging
import os

import aiohttp

logger = logging.getLogger("jarvis.notifications.telegram")

_ENABLED: bool | None = None   # cached after first check


def _is_configured() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID"))
        if not _ENABLED:
            logger.info(
                "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing) "
                "— notifications disabled"
            )
    return _ENABLED


async def send_message(text: str) -> bool:
    """Send a pre-formatted HTML message via Telegram Bot API."""
    if not _is_configured():
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info("Telegram message sent")
                    return True
                logger.error(f"Telegram API error: HTTP {resp.status}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


# ── Message formatters ────────────────────────────────────────────────────────

def format_trade_opened(payload: dict) -> str:
    symbol = payload.get("symbol") or "?"
    chain = (payload.get("chain") or "").upper()
    price = payload.get("entry_price") or 0
    size = payload.get("size_usd") or 0
    score = payload.get("confidence_score") or 0
    return (
        f"<b>📈 PAPER TRADE OPENED</b>\n"
        f"Token:  <code>{symbol}</code> ({chain})\n"
        f"Entry:  <b>${price:.6g}</b>\n"
        f"Size:   <b>${size:,.2f}</b>\n"
        f"Score:  {score}/100\n"
    )


def format_trade_closed(payload: dict) -> str:
    symbol = payload.get("symbol") or "?"
    pnl_usd = payload.get("pnl_usd") or 0
    pnl_pct = (payload.get("pnl_pct") or 0) * 100
    reason = (payload.get("exit_reason") or "").replace("_", " ").upper()
    size = payload.get("size_usd") or 0

    icon = "✅" if pnl_usd >= 0 else "❌"
    sign = "+" if pnl_usd >= 0 else ""

    return (
        f"<b>{icon} PAPER TRADE CLOSED</b>\n"
        f"Token:    <code>{symbol}</code>\n"
        f"P&L:      <b>{sign}${pnl_usd:.2f}  ({sign}{pnl_pct:.1f}%)</b>\n"
        f"Size:     ${size:,.2f}\n"
        f"Exit:     {reason}\n"
    )


def format_daily_briefing(stats: dict, portfolio_value: float) -> str:
    total = stats.get("total_trades", 0)
    wins = stats.get("winning_trades", 0)
    pnl = stats.get("total_pnl_usd", 0)
    win_rate = stats.get("win_rate", 0)
    best = stats.get("best_trade_pnl")
    worst = stats.get("worst_trade_pnl")

    sign = "+" if pnl >= 0 else ""
    portfolio_line = f"Portfolio: <b>${portfolio_value:,.2f}</b>\n" if portfolio_value else ""
    best_line = f"Best:      <b>+${best:.2f}</b>\n" if best is not None else ""
    worst_line = f"Worst:     <b>${worst:.2f}</b>\n" if worst is not None else ""

    header = "📊 <b>JARVIS DAILY BRIEFING</b>\n"
    if total == 0:
        return header + portfolio_line + "No trades closed in the last 24 hours.\n"

    return (
        header
        + portfolio_line
        + f"Trades:    {total} closed | {wins} wins ({win_rate:.0f}%)\n"
        + f"Day P&L:   <b>{sign}${pnl:.2f}</b>\n"
        + best_line
        + worst_line
    )
