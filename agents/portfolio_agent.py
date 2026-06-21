import asyncio
from datetime import datetime, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_token, extract_token_info
from models.schema import Trade, PaperPortfolio, Token


class PortfolioAgent(BaseAgent):
    """
    Manages the paper portfolio: tracks open positions, calculates P&L,
    persists state to the database so restarts don't lose position history.
    """

    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        trading = config.get("trading", {})
        self._starting_balance = trading.get("paper_balance", 10_000.0)
        self._max_position_pct = trading.get("max_position_size_pct", 0.05)
        self._max_memecoin_pct = trading.get("max_memecoin_pct", 0.02)

    async def run(self) -> None:
        state = await self._compute_state()

        self.logger.info(
            f"Portfolio | Cash: ${state['cash_balance']:,.2f} | "
            f"Invested: ${state['total_invested']:,.2f} | "
            f"Total: ${state['total_value']:,.2f} | "
            f"All-time P&L: {state['all_time_pnl_pct']:+.2f}%"
        )

        self._persist_state(state)

        # Feed P&L into risk agent for circuit breaker evaluation
        await self.publish("pnl_update", {
            "daily_pnl_pct": state["daily_pnl_pct"],
            "drawdown_pct": state["drawdown_pct"],
            "total_value": state["total_value"],
            "cash_balance": state["cash_balance"],
        })

        await self.publish("portfolio_state", state)

    async def _compute_state(self) -> dict:
        loop = asyncio.get_running_loop()

        with self.get_db() as db:
            open_trades = db.query(Trade).filter_by(status="open", is_paper=True).all()
            closed_trades = db.query(Trade).filter_by(status="closed", is_paper=True).all()
            last_snapshot = (
                db.query(PaperPortfolio)
                .order_by(PaperPortfolio.updated_at.desc())
                .first()
            )

        # Cash = starting balance minus what's currently in open positions
        invested = sum(t.size_usd or 0 for t in open_trades)
        realised_pnl = sum(t.pnl_usd or 0 for t in closed_trades)
        cash = self._starting_balance + realised_pnl - invested
        cash = max(0.0, cash)

        # Mark open positions to market
        unrealised_pnl = 0.0
        for trade in open_trades:
            price = await self._get_current_price(trade, loop)
            if price and trade.entry_price:
                pnl = (price - trade.entry_price) / trade.entry_price * (trade.size_usd or 0)
                if trade.direction == "sell":
                    pnl = -pnl
                unrealised_pnl += pnl

        total_value = cash + invested + unrealised_pnl
        all_time_pnl = total_value - self._starting_balance
        all_time_pnl_pct = all_time_pnl / self._starting_balance * 100

        # Drawdown from peak
        peak = last_snapshot.total_value if last_snapshot else self._starting_balance
        peak = max(peak, total_value)
        drawdown_pct = (peak - total_value) / peak if peak > 0 else 0.0

        # Daily P&L (simplified: change from today's first snapshot)
        daily_pnl_pct = 0.0
        if last_snapshot:
            daily_pnl_pct = (total_value - last_snapshot.total_value) / last_snapshot.total_value

        win_count = sum(1 for t in closed_trades if (t.pnl_usd or 0) > 0)

        return {
            "cash_balance": round(cash, 2),
            "total_invested": round(invested, 2),
            "unrealised_pnl": round(unrealised_pnl, 2),
            "total_value": round(total_value, 2),
            "all_time_pnl": round(all_time_pnl, 2),
            "all_time_pnl_pct": round(all_time_pnl_pct, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "open_positions": len(open_trades),
            "closed_trades": len(closed_trades),
            "win_count": win_count,
            "win_rate": round(win_count / max(len(closed_trades), 1) * 100, 1),
        }

    async def _get_current_price(self, trade: Trade, loop) -> float | None:
        """Fetch live price for an open position via DexScreener (works for all tokens including memecoins)."""
        try:
            with self.get_db() as db:
                token = db.query(Token).filter_by(id=trade.token_id).first()
            if not token or not token.address:
                return None
            await rate_limit("api.dexscreener.com")
            pair = await loop.run_in_executor(None, lambda a=token.address: get_token(a))
            if not pair:
                return None
            price = extract_token_info(pair).get("price_usd") or 0
            return price if price > 0 else None
        except Exception:
            return None

    def _persist_state(self, state: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(PaperPortfolio(
                    updated_at=datetime.now(timezone.utc),
                    cash_balance=state["cash_balance"],
                    total_invested=state["total_invested"],
                    total_value=state["total_value"],
                    all_time_pnl=state["all_time_pnl"],
                    daily_pnl=state["daily_pnl_pct"] * state["total_value"],
                    trade_count=state["closed_trades"],
                    win_count=state["win_count"],
                ))
        except Exception as e:
            self.logger.error(f"Failed to persist portfolio state: {e}")

    def position_size_for(self, total_value: float, is_memecoin: bool = False) -> float:
        """Return the dollar amount to allocate to a new position."""
        pct = self._max_memecoin_pct if is_memecoin else self._max_position_pct
        return round(total_value * pct, 2)

    async def process_message(self, message: dict) -> None:
        pass
