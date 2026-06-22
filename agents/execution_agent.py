import asyncio
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_token, extract_token_info
from models.schema import Trade, TradeIdea, Token, PaperPortfolio


class ExecutionAgent(BaseAgent):
    """
    Paper trade lifecycle manager.

    Opening  — triggered by "trade_idea" message from Orchestrator:
                fetch live price → create Trade record → publish trade_opened

    Monitoring — each run() tick:
                fetch current price → check stop-loss / take-profit / timeout
                → close position and publish trade_closed

    SAFETY: only paper trades while mode == "paper".
            live execution is Phase 6, gated behind circuit breaker + mode check.
    """

    def __init__(self, name, message_bus, session_factory, circuit_breaker, config):
        super().__init__(name, message_bus, session_factory, config)
        self.circuit_breaker = circuit_breaker
        trading = config.get("trading", {})
        self._stop_loss_pct = trading.get("stop_loss_pct", -0.08)
        self._take_profit_pct = trading.get("take_profit_pct", 0.25)
        self._trailing_stop_pct = trading.get("trailing_stop_pct", 0.15)
        self._max_hold_hours = trading.get("max_hold_hours", 48)
        self._starting_balance = trading.get("paper_balance", 10_000.0)
        self._max_position_pct = trading.get("max_position_size_pct", 0.05)

    # ── Monitoring tick ───────────────────────────────────────────────────────

    async def run(self) -> None:
        if not self.circuit_breaker.trading_allowed:
            self.logger.warning("Trading halted by circuit breaker — skipping tick")
            return

        open_trades = self._get_open_trades()
        if not open_trades:
            return

        self.logger.info(f"Monitoring {len(open_trades)} open paper position(s)")
        loop = asyncio.get_running_loop()

        for trade in open_trades:
            address = self._get_token_address(trade.token_id)
            if not address:
                continue

            await rate_limit("api.dexscreener.com")
            pair = await loop.run_in_executor(None, lambda a=address: get_token(a))
            current_price = extract_token_info(pair).get("price_usd") if pair else None

            if current_price and current_price > 0:
                self._update_high_price(trade, current_price)

            reason = self._check_exit_conditions(trade, current_price)
            if reason:
                await self._close_trade(trade, current_price, reason)

    # ── Open trade ────────────────────────────────────────────────────────────

    async def _open_paper_trade(self, idea: dict) -> None:
        if self.config.get("system", {}).get("mode", "paper") != "paper":
            self.logger.warning("Not in paper mode — trade opening blocked")
            return

        address = idea.get("address")
        token_id = idea.get("token_id")
        trade_idea_id = idea.get("trade_idea_id")
        symbol = idea.get("symbol") or (address[:8] if address else "UNKNOWN")

        if not address or token_id is None:
            self.logger.error("trade_idea missing address or token_id — skipping")
            return

        if self._has_open_position(token_id):
            self.logger.info(f"Already holding {symbol} — duplicate position blocked")
            return

        loop = asyncio.get_running_loop()
        await rate_limit("api.dexscreener.com")
        pair = await loop.run_in_executor(None, lambda: get_token(address))
        if not pair:
            self.logger.warning(f"DexScreener returned no pair for {symbol} — trade aborted")
            return

        entry_price = extract_token_info(pair).get("price_usd") or 0
        if entry_price <= 0:
            self.logger.warning(f"Zero price returned for {symbol} — trade aborted")
            return

        size_usd = self._compute_position_size(idea)

        try:
            with self.get_db() as db:
                trade = Trade(
                    trade_idea_id=trade_idea_id,
                    token_id=token_id,
                    is_paper=True,
                    direction=idea.get("direction", "buy"),
                    entry_price=entry_price,
                    size_usd=size_usd,
                    opened_at=datetime.now(timezone.utc),
                    status="open",
                )
                db.add(trade)
                db.flush()
                trade_id = trade.id

                # Mark the trade idea as executed
                if trade_idea_id:
                    ti = db.query(TradeIdea).filter_by(id=trade_idea_id).first()
                    if ti:
                        ti.status = "executed"

        except Exception as e:
            self.logger.error(f"Failed to record paper trade for {symbol}: {e}")
            return

        self.logger.info(
            f"PAPER TRADE OPENED | {symbol} | "
            f"Entry: ${entry_price:.6g} | Size: ${size_usd:,.2f} | "
            f"ID: {trade_id} | Score: {idea.get('confidence_score')}/100"
        )

        await self.publish("trade_opened", {
            "trade_id": trade_id,
            "trade_idea_id": trade_idea_id,
            "token_id": token_id,
            "address": address,
            "symbol": symbol,
            "chain": idea.get("chain"),
            "entry_price": entry_price,
            "size_usd": size_usd,
            "confidence_score": idea.get("confidence_score"),
        })

    # ── Close trade ───────────────────────────────────────────────────────────

    async def _close_trade(
        self, trade: Trade, exit_price: float | None, reason: str
    ) -> None:
        now = datetime.now(timezone.utc)

        # For timeout exits price may be unavailable — use entry price (0% loss)
        effective_exit = exit_price if exit_price else trade.entry_price

        pnl_pct = 0.0
        pnl_usd = 0.0
        if trade.entry_price and effective_exit:
            pnl_pct = (effective_exit - trade.entry_price) / trade.entry_price
            if trade.direction == "sell":
                pnl_pct = -pnl_pct
            pnl_usd = pnl_pct * (trade.size_usd or 0)

        try:
            with self.get_db() as db:
                t = db.query(Trade).filter_by(id=trade.id).first()
                if not t or t.status != "open":
                    return
                t.exit_price = effective_exit
                t.pnl_usd = round(pnl_usd, 4)
                t.pnl_pct = round(pnl_pct, 6)
                t.status = "closed"
                t.closed_at = now
                t.exit_reason = reason
        except Exception as e:
            self.logger.error(f"Failed to close trade {trade.id}: {e}")
            return

        result_str = f"{pnl_pct:+.2%} (${pnl_usd:+.2f})"
        symbol = self._get_token_symbol(trade.token_id)

        self.logger.info(
            f"PAPER TRADE CLOSED | {symbol} | "
            f"Exit: ${effective_exit:.6g} | P&L: {result_str} | "
            f"Reason: {reason.upper()} | ID: {trade.id}"
        )

        await self.publish("trade_closed", {
            "trade_id": trade.id,
            "token_id": trade.token_id,
            "symbol": symbol,
            "entry_price": trade.entry_price,
            "exit_price": effective_exit,
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct, 6),
            "exit_reason": reason,
            "size_usd": trade.size_usd,
        })

    # ── High-water mark tracking ──────────────────────────────────────────────

    def _update_high_price(self, trade: Trade, current_price: float) -> None:
        """Persist peak price for trailing stop calculation."""
        peak = trade.high_price or trade.entry_price or 0
        if current_price > peak:
            try:
                with self.get_db() as db:
                    t = db.query(Trade).filter_by(id=trade.id).first()
                    if t and t.status == "open":
                        t.high_price = current_price
                trade.high_price = current_price  # keep in-memory object in sync
            except Exception as e:
                self.logger.error(f"high_price update failed for trade {trade.id}: {e}")

    # ── Exit condition logic ──────────────────────────────────────────────────

    def _check_exit_conditions(self, trade: Trade, current_price: float | None) -> str | None:
        if trade.opened_at:
            opened = trade.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - opened > timedelta(hours=self._max_hold_hours):
                return "timeout"

        if current_price is None or trade.entry_price is None:
            return None

        pnl_pct = (current_price - trade.entry_price) / trade.entry_price
        if trade.direction == "sell":
            pnl_pct = -pnl_pct

        if pnl_pct <= self._stop_loss_pct:
            return "stop_loss"

        # Once the trade has gone profitable the trailing stop takes over from
        # the fixed take-profit, letting memecoins run beyond the initial target.
        high = trade.high_price or trade.entry_price
        if trade.direction == "buy" and high > trade.entry_price:
            # Trailing stop active — fire if price retreated trailing_stop_pct from peak
            if current_price < high * (1 - self._trailing_stop_pct):
                return "trailing_stop"
        else:
            # No peak above entry yet (or short position) — use fixed take-profit
            if pnl_pct >= self._take_profit_pct:
                return "take_profit"

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_position_size(self, idea: dict) -> float:
        """Dollar size for this position, scaled by suggested_size_pct and portfolio value."""
        portfolio_value = self._get_portfolio_value()
        size_pct = idea.get("suggested_size_pct", self._max_position_pct)
        return round(portfolio_value * size_pct, 2)

    def _get_portfolio_value(self) -> float:
        try:
            with self.get_db() as db:
                snapshot = (
                    db.query(PaperPortfolio)
                    .order_by(PaperPortfolio.updated_at.desc())
                    .first()
                )
                if snapshot:
                    return snapshot.total_value
        except Exception:
            pass
        return self._starting_balance

    def _has_open_position(self, token_id: int) -> bool:
        try:
            with self.get_db() as db:
                return (
                    db.query(Trade)
                    .filter_by(token_id=token_id, status="open", is_paper=True)
                    .count()
                ) > 0
        except Exception:
            return False

    def _get_open_trades(self) -> list:
        try:
            with self.get_db() as db:
                return db.query(Trade).filter_by(status="open", is_paper=True).all()
        except Exception as e:
            self.logger.error(f"Failed to load open trades: {e}")
            return []

    def _get_token_address(self, token_id: int) -> str | None:
        try:
            with self.get_db() as db:
                token = db.query(Token).filter_by(id=token_id).first()
                return token.address if token else None
        except Exception:
            return None

    def _get_token_id(self, address: str) -> int | None:
        try:
            with self.get_db() as db:
                from models.schema import Token as _Token
                token = db.query(_Token).filter_by(address=address).first()
                return token.id if token else None
        except Exception:
            return None

    def _get_open_trades_for_token(self, token_id: int) -> list:
        try:
            with self.get_db() as db:
                return db.query(Trade).filter_by(token_id=token_id, status="open", is_paper=True).all()
        except Exception:
            return []

    def _get_token_symbol(self, token_id: int) -> str:
        try:
            with self.get_db() as db:
                token = db.query(Token).filter_by(id=token_id).first()
                return token.symbol or f"token:{token_id}" if token else f"token:{token_id}"
        except Exception:
            return f"token:{token_id}"

    # ── Message handling ──────────────────────────────────────────────────────

    async def process_message(self, message: dict) -> None:
        msg_type = message.get("type")

        if msg_type == "trade_idea":
            if not self.circuit_breaker.trading_allowed:
                self.logger.warning("Trade blocked — circuit breaker is active")
                return
            await self._open_paper_trade(message.get("payload", {}))

        elif msg_type == "token_vetted":
            payload = message.get("payload", {})
            if not payload.get("passed", True):
                await self._emergency_exit_on_rug(payload)

    async def _emergency_exit_on_rug(self, payload: dict) -> None:
        """Close any open position on a token that just failed re-vetting."""
        address = payload.get("address")
        fail_reasons = payload.get("fail_reasons", [])
        # Only emergency-exit on hard failures (honeypot / sell tax) not just low liquidity
        hard_failures = [r for r in fail_reasons if any(
            kw in r for kw in ("honeypot", "mintable", "owner_can_change_balance", "sell_tax")
        )]
        if not hard_failures or not address:
            return

        token_id = self._get_token_id(address)
        if token_id is None:
            return

        open_trades = self._get_open_trades_for_token(token_id)
        if not open_trades:
            return

        sym = payload.get("symbol", address[:8])
        self.logger.warning(
            f"RUG DETECTED on held token {sym} — closing {len(open_trades)} position(s) | "
            f"reasons: {hard_failures}"
        )
        for trade in open_trades:
            await self._close_trade(trade, None, "rug_detected")
