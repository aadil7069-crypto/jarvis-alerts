import asyncio
import os
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_token, extract_token_info
from data.jupiter import get_quote as jupiter_quote, execute_swap as jupiter_execute
from data.jupiter import USDC_MINT as _USDC_MINT
from data.pancakeswap import get_quote as pancake_quote, execute_swap as pancake_execute
from data.pancakeswap import WBNB_ADDRESS as _WBNB
from models.schema import Trade, TradeIdea, Token, PaperPortfolio


class ExecutionAgent(BaseAgent):
    """
    Trade lifecycle manager — paper and live modes.

    Paper mode  (mode == "paper"):
      Opening  — fetch quote price from Jupiter/PancakeSwap (with price impact),
                 fall back to DexScreener; create Trade record; publish trade_opened
      Monitoring — check stop-loss / trailing-stop / take-profit / timeout each tick

    Live mode (mode == "live"):
      Opening  — same quote fetch, then broadcast swap on-chain via Jupiter/PancakeSwap;
                 store tx_signature in Trade record
      Monitoring — same exit logic as paper mode

    SAFETY:
      • mode gate hardcoded in _open_trade()
      • circuit breaker checked before every action
      • private keys only ever read from env vars, never stored
    """

    def __init__(self, name, message_bus, session_factory, circuit_breaker, config):
        super().__init__(name, message_bus, session_factory, config)
        self.circuit_breaker = circuit_breaker
        trading = config.get("trading", {})
        self._stop_loss_pct     = trading.get("stop_loss_pct", -0.08)
        self._take_profit_pct   = trading.get("take_profit_pct", 0.25)
        self._trailing_stop_pct = trading.get("trailing_stop_pct", 0.15)
        self._max_hold_hours    = trading.get("max_hold_hours", 48)
        self._starting_balance  = trading.get("paper_balance", 10_000.0)
        self._max_position_pct  = trading.get("max_position_size_pct", 0.05)
        self._stop_loss_cooldown = timedelta(
            minutes=trading.get("stop_loss_cooldown_minutes", 30)
        )
        execution = config.get("execution", {})
        self._slippage_bps = execution.get("slippage_bps", 50)
        self._solana_rpc   = (
            execution.get("solana_rpc_url")
            or f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY', '')}"
        )
        self._bnb_rpc = execution.get("bnb_rpc_url") or os.getenv("BNB_RPC_URL", "")

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

            await rate_limit("api.dexscreener.com", priority=True)
            pair = await loop.run_in_executor(None, lambda a=address: get_token(a))
            current_price = extract_token_info(pair).get("price_usd") if pair else None

            if current_price and current_price > 0:
                self._update_high_price(trade, current_price)

            reason = self._check_exit_conditions(trade, current_price)
            if reason:
                await self._close_trade(trade, current_price, reason)

    # ── Open trade (paper + live) ─────────────────────────────────────────────

    async def _open_trade(self, idea: dict) -> None:
        mode = self.config.get("system", {}).get("mode", "paper")
        address   = idea.get("address")
        chain     = idea.get("chain", "solana")
        token_id  = idea.get("token_id")
        trade_idea_id = idea.get("trade_idea_id")
        symbol = idea.get("symbol") or (address[:8] if address else "UNKNOWN")

        if not address or token_id is None:
            self.logger.error("trade_idea missing address or token_id — skipping")
            return

        if self._has_open_position(token_id):
            self.logger.info(f"Already holding {symbol} — duplicate position blocked")
            return

        cooldown_remaining = self._stop_loss_cooldown_remaining(token_id)
        if cooldown_remaining:
            self.logger.info(
                f"{symbol} stopped out recently — cooldown blocks re-entry for "
                f"{cooldown_remaining.total_seconds() / 60:.0f} more min"
            )
            return

        size_usd = self._compute_position_size(idea)

        # ── Fetch entry price (always; live also executes swap) ───────────────
        entry_price, tx_sig = await self._fetch_price_and_maybe_execute(
            address, chain, size_usd, mode, loop=asyncio.get_running_loop()
        )
        if entry_price <= 0:
            self.logger.warning(f"Could not get entry price for {symbol} — trade aborted")
            return

        is_paper = (mode != "live") or (tx_sig is None and mode == "live")

        try:
            with self.get_db() as db:
                trade = Trade(
                    trade_idea_id=trade_idea_id,
                    token_id=token_id,
                    is_paper=(mode == "paper"),
                    direction=idea.get("direction", "buy"),
                    entry_price=entry_price,
                    size_usd=size_usd,
                    opened_at=datetime.now(timezone.utc),
                    status="open",
                    tx_signature=tx_sig,
                )
                db.add(trade)
                db.flush()
                trade_id = trade.id

                if trade_idea_id:
                    ti = db.query(TradeIdea).filter_by(id=trade_idea_id).first()
                    if ti:
                        ti.status = "executed"

        except Exception as e:
            self.logger.error(f"Failed to record trade for {symbol}: {e}")
            return

        mode_tag = "LIVE" if mode == "live" else "PAPER"
        tx_info = f" | tx: {tx_sig[:16]}..." if tx_sig else ""
        self.logger.info(
            f"{mode_tag} TRADE OPENED | {symbol} | "
            f"Entry: ${entry_price:.6g} | Size: ${size_usd:,.2f} | "
            f"ID: {trade_id} | Score: {idea.get('confidence_score')}/100{tx_info}"
        )

        await self.publish("trade_opened", {
            "trade_id": trade_id,
            "trade_idea_id": trade_idea_id,
            "token_id": token_id,
            "address": address,
            "symbol": symbol,
            "chain": chain,
            "entry_price": entry_price,
            "size_usd": size_usd,
            "confidence_score": idea.get("confidence_score"),
            "tx_signature": tx_sig,
            "mode": mode,
        })

    async def _fetch_price_and_maybe_execute(
        self, address: str, chain: str, size_usd: float, mode: str, loop
    ) -> tuple[float, str | None]:
        """
        Fetch the best available entry price and, in live mode, execute the swap.

        Returns (entry_price_usd, tx_signature_or_None).
        """
        # ── Get quote (DEX aggregator for best price accuracy) ────────────────
        entry_price = 0.0
        tx_sig = None

        if chain == "solana":
            await rate_limit("quote-api.jup.ag")
            usdc_units = int(size_usd * 1_000_000)
            quote = await loop.run_in_executor(
                None, lambda: jupiter_quote(_USDC_MINT, address, usdc_units, self._slippage_bps)
            )
            if quote and quote.get("out_amount") and quote.get("in_amount"):
                tokens_out = quote["out_amount"]
                if tokens_out > 0:
                    # price = USD spent / tokens received (raw units; approximates at 9 decimals)
                    entry_price = (quote["in_amount"] / 1_000_000) / (tokens_out / 1e9)
                if mode == "live" and entry_price > 0:
                    result = await loop.run_in_executor(
                        None, lambda q=quote: jupiter_execute(q, self._solana_rpc)
                    )
                    if result.get("status") == "broadcast":
                        tx_sig = result.get("tx_signature")
                    else:
                        self.logger.error(f"Jupiter live swap failed: {result.get('error')}")
                        return 0.0, None

        elif chain in ("bnb", "bsc"):
            await rate_limit("bsc-dataseed.binance.org")
            bnb_units = int((size_usd / 300) * 1e18)  # rough BNB conversion
            quote = await loop.run_in_executor(
                None, lambda: pancake_quote(_WBNB, address, bnb_units, rpc_url=self._bnb_rpc)
            )
            if quote and quote.get("amount_out"):
                tokens_out = quote["amount_out"]
                if tokens_out > 0:
                    entry_price = (bnb_units / 1e18 * 300) / (tokens_out / 1e18)
                if mode == "live" and entry_price > 0:
                    result = await loop.run_in_executor(
                        None,
                        lambda q=quote: pancake_execute(
                            _WBNB, address, bnb_units,
                            q.get("min_amount_out", 0),
                            q.get("fee_tier", 500),
                            rpc_url=self._bnb_rpc,
                        )
                    )
                    if result.get("status") == "broadcast":
                        tx_sig = result.get("tx_hash")
                    else:
                        self.logger.error(f"PancakeSwap live swap failed: {result.get('error')}")
                        return 0.0, None

        # ── Fall back to DexScreener if quote failed ──────────────────────────
        if entry_price <= 0:
            await rate_limit("api.dexscreener.com")
            pair = await loop.run_in_executor(None, lambda: get_token(address))
            if pair:
                entry_price = extract_token_info(pair).get("price_usd") or 0.0
            if entry_price <= 0:
                return 0.0, None

        return entry_price, tx_sig

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

    def _stop_loss_cooldown_remaining(self, token_id: int) -> timedelta | None:
        """If this token stopped us out within the cooldown window, return time left."""
        try:
            with self.get_db() as db:
                last_stop_out = (
                    db.query(Trade)
                    .filter_by(token_id=token_id, status="closed", exit_reason="stop_loss", is_paper=True)
                    .order_by(Trade.closed_at.desc())
                    .first()
                )
                if not last_stop_out or not last_stop_out.closed_at:
                    return None

                closed_at = last_stop_out.closed_at
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=timezone.utc)

                elapsed = datetime.now(timezone.utc) - closed_at
                if elapsed < self._stop_loss_cooldown:
                    return self._stop_loss_cooldown - elapsed
                return None
        except Exception:
            return None

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
            await self._open_trade(message.get("payload", {}))

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
