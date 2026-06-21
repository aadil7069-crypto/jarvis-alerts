from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from models.schema import Trade, Performance, PaperPortfolio


class ReportingAgent(BaseAgent):
    """
    Daily performance tracker + Telegram notification dispatcher.

    Trade events (trade_opened / trade_closed) arrive via the message bus
    and trigger immediate Telegram alerts (when configured).

    The daily run() tick computes 24h stats, writes a Performance record,
    and sends the daily briefing to Telegram.
    """

    async def run(self) -> None:
        today_stats = self._compute_daily_stats()
        self._persist_stats(today_stats)

        if today_stats["total_trades"] == 0:
            self.logger.info("Daily report: no trades closed in the last 24 hours")
        else:
            win_rate = today_stats["win_rate"]
            self.logger.info(
                f"Daily report | Trades: {today_stats['total_trades']} | "
                f"Wins: {today_stats['winning_trades']} ({win_rate:.0f}%) | "
                f"P&L: ${today_stats['total_pnl_usd']:+.2f} | "
                f"Portfolio: ${today_stats['portfolio_value']:,.2f}"
            )
            if today_stats.get("best_trade_pnl") is not None:
                self.logger.info(
                    f"  Best: ${today_stats['best_trade_pnl']:+.2f} | "
                    f"Worst: ${today_stats['worst_trade_pnl']:+.2f}"
                )

        await self.publish("daily_report", today_stats)
        await self._send_daily_briefing(today_stats)

    async def _send_daily_briefing(self, stats: dict) -> None:
        if not self._notifications_enabled():
            return
        from notifications.telegram import send_message, format_daily_briefing
        text = format_daily_briefing(stats, stats.get("portfolio_value", 0))
        await send_message(text)

    async def process_message(self, message: dict) -> None:
        msg_type = message.get("type")

        if msg_type == "trade_opened":
            await self._notify_trade_opened(message.get("payload", {}))

        elif msg_type == "trade_closed":
            await self._notify_trade_closed(message.get("payload", {}))

    async def _notify_trade_opened(self, payload: dict) -> None:
        symbol = payload.get("symbol", "?")
        self.logger.info(
            f"Trade opened notification: {symbol} @ ${payload.get('entry_price', 0):.6g} "
            f"| Size: ${payload.get('size_usd', 0):,.2f}"
        )
        if not self._notifications_enabled():
            return
        from notifications.telegram import send_message, format_trade_opened
        await send_message(format_trade_opened(payload))

    async def _notify_trade_closed(self, payload: dict) -> None:
        symbol = payload.get("symbol", "?")
        pnl = payload.get("pnl_usd", 0)
        self.logger.info(
            f"Trade closed notification: {symbol} | P&L: ${pnl:+.2f} | "
            f"Reason: {payload.get('exit_reason', '?')}"
        )
        if not self._notifications_enabled():
            return
        from notifications.telegram import send_message, format_trade_closed
        await send_message(format_trade_closed(payload))

    def _notifications_enabled(self) -> bool:
        return self.config.get("notifications", {}).get("enabled", False)

    # ── Daily stats ────────────────────────────────────────────────────────────

    def _compute_daily_stats(self) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            with self.get_db() as db:
                closed = (
                    db.query(Trade)
                    .filter(
                        Trade.is_paper == True,
                        Trade.status == "closed",
                        Trade.closed_at >= cutoff,
                    )
                    .all()
                )
                snapshot = (
                    db.query(PaperPortfolio)
                    .order_by(PaperPortfolio.updated_at.desc())
                    .first()
                )
                portfolio_value = snapshot.total_value if snapshot else 0.0
        except Exception as e:
            self.logger.error(f"DB error computing daily stats: {e}")
            return {**self._empty_stats(), "portfolio_value": 0.0}

        if not closed:
            return {**self._empty_stats(), "portfolio_value": portfolio_value}

        pnl_values = [t.pnl_usd or 0.0 for t in closed]
        wins = [p for p in pnl_values if p > 0]
        total_pnl = sum(pnl_values)
        win_rate = len(wins) / len(closed) * 100

        exit_reasons: dict[str, int] = {}
        for t in closed:
            r = t.exit_reason or "unknown"
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        return {
            "total_trades": len(closed),
            "winning_trades": len(wins),
            "total_pnl_usd": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
            "best_trade_pnl": round(max(pnl_values), 4),
            "worst_trade_pnl": round(min(pnl_values), 4),
            "portfolio_value": round(portfolio_value, 2),
            "exit_reasons": exit_reasons,
        }

    def _persist_stats(self, stats: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(Performance(
                    date=datetime.now(timezone.utc),
                    is_paper=True,
                    total_trades=stats["total_trades"],
                    winning_trades=stats["winning_trades"],
                    total_pnl_usd=stats["total_pnl_usd"],
                    win_rate=stats["win_rate"],
                    best_trade_pnl=stats.get("best_trade_pnl"),
                    worst_trade_pnl=stats.get("worst_trade_pnl"),
                    portfolio_value=stats["portfolio_value"],
                ))
        except Exception as e:
            self.logger.error(f"Failed to persist daily performance record: {e}")

    def _empty_stats(self) -> dict:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "total_pnl_usd": 0.0,
            "win_rate": 0.0,
            "best_trade_pnl": None,
            "worst_trade_pnl": None,
            "exit_reasons": {},
        }
