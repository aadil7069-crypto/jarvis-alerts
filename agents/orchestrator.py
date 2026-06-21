import json
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.scoring import compute_score, signals_to_strengths
from models.schema import Signal, Token, TokenVetting, TradeIdea, Trade, Watchlist


class Orchestrator(BaseAgent):
    """
    Central decision-maker.

    Each tick it:
    1. Collects all unexpired signals per watchlist token
    2. Computes a confidence score using core/scoring.py
    3. Gates on minimum score (72) and minimum agent consensus (3 agents)
    4. Enforces a max-concurrent-positions cap
    5. Creates a TradeIdea record and publishes to ExecutionAgent

    Only paper trades — never touches real funds (mode enforced in config).
    """

    MIN_CONFIDENCE: int = 72     # minimum score to create a trade idea
    MIN_AGENTS: int = 3          # minimum distinct agents that must agree

    def __init__(self, name, message_bus, session_factory, circuit_breaker, config):
        super().__init__(name, message_bus, session_factory, config)
        self.circuit_breaker = circuit_breaker
        self._confidence_modifier: int = 0     # from prediction market agent
        self._safe_mode: str = "normal"        # from prediction market agent
        self._position_multiplier: float = 1.0
        self._max_positions: int = config.get("trading", {}).get("max_concurrent_positions", 5)

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        cb = self.circuit_breaker.status()
        if not cb["trading_allowed"]:
            self.logger.warning(f"Trading HALTED — {cb['reason']}")
            return

        open_count = self._count_open_positions()
        if open_count >= self._max_positions:
            self.logger.info(
                f"Position cap reached ({open_count}/{self._max_positions}) — skipping this tick"
            )
            return

        self.logger.info(
            f"Orchestrator tick | Open: {open_count}/{self._max_positions} | "
            f"Mode: {self._safe_mode.upper()} | PM modifier: {self._confidence_modifier:+d}"
        )

        watchlist = self._get_watchlist_tokens()
        if not watchlist:
            self.logger.info("Watchlist empty — nothing to score")
            return

        slots_available = self._max_positions - open_count

        for token in watchlist:
            if slots_available <= 0:
                break

            scored = self._score_token(token)
            if not scored:
                continue

            if scored["meets_threshold"]:
                await self._create_trade_idea(token, scored)
                slots_available -= 1
            else:
                self.logger.debug(
                    f"{token.symbol or token.address[:8]} | score={scored['total']} < "
                    f"{self.MIN_CONFIDENCE} threshold or < {self.MIN_AGENTS} agents"
                )

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score_token(self, token: Token) -> dict | None:
        try:
            with self.get_db() as db:
                # Recent signals only (last 6 hours to stay warm)
                cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
                signals = (
                    db.query(Signal)
                    .filter(
                        Signal.token_id == token.id,
                        Signal.created_at >= cutoff,
                        Signal.signal_type == "bullish",
                    )
                    .order_by(Signal.created_at.desc())
                    .limit(50)
                    .all()
                )

                # Global sentiment signals (token_id = None)
                sentiment_signals = (
                    db.query(Signal)
                    .filter(
                        Signal.token_id.is_(None),
                        Signal.agent_name.contains("sentiment"),
                        Signal.created_at >= cutoff,
                    )
                    .order_by(Signal.created_at.desc())
                    .limit(5)
                    .all()
                )

                all_signals = signals + sentiment_signals

                # Check vetting
                vetting = (
                    db.query(TokenVetting)
                    .filter_by(token_id=token.id, overall_pass=True)
                    .order_by(TokenVetting.checked_at.desc())
                    .first()
                )
                vetting_passed = vetting is not None

            strengths = signals_to_strengths(all_signals)

            result = compute_score(
                vetting_passed=vetting_passed,
                smart_money_strength=strengths["smart_money"],
                elite_trader_strength=strengths["elite_trader"],
                whale_strength=strengths["whale"],
                sentiment_strength=strengths["sentiment"],
                strategy_strength=strengths["strategy"],
                pm_modifier=self._confidence_modifier,
            )

            # Count distinct bullish agents (gates minimum consensus)
            bullish_agents = {
                s.agent_name for s in all_signals
                if s.signal_type == "bullish"
            }
            agent_count = len(bullish_agents)

            result["meets_threshold"] = (
                result["total"] >= self.MIN_CONFIDENCE
                and agent_count >= self.MIN_AGENTS
            )
            result["agents_bullish"] = agent_count

            return result

        except Exception as e:
            self.logger.error(f"Scoring failed for {token.address}: {e}")
            return None

    # ── Trade idea creation ────────────────────────────────────────────────────

    async def _create_trade_idea(self, token: Token, scored: dict) -> None:
        base_size_pct = self.config.get("trading", {}).get("max_position_size_pct", 0.05)
        adjusted_size_pct = base_size_pct * self._position_multiplier

        breakdown_json = json.dumps(scored["breakdown"])

        try:
            with self.get_db() as db:
                idea = TradeIdea(
                    token_id=token.id,
                    confidence_score=scored["total"],
                    agents_bullish=scored["agents_bullish"],
                    score_breakdown=breakdown_json,
                    direction="buy",
                    suggested_size_pct=adjusted_size_pct,
                    status="pending",
                )
                db.add(idea)
                db.flush()
                idea_id = idea.id

        except Exception as e:
            self.logger.error(f"Failed to create trade idea for {token.address}: {e}")
            return

        self.logger.info(
            f"TRADE IDEA created | {token.symbol or token.address[:8]} | "
            f"Score: {scored['total']}/100 (base {scored['base_score']} "
            f"PM {self._confidence_modifier:+d}) | "
            f"Agents: {scored['agents_bullish']} | "
            f"Size: {adjusted_size_pct:.1%} | "
            f"Mode: {self._safe_mode.upper()}"
        )

        await self.publish("trade_idea", {
            "trade_idea_id": idea_id,
            "token_id": token.id,
            "address": token.address,
            "symbol": token.symbol,
            "chain": token.chain,
            "confidence_score": scored["total"],
            "score_breakdown": scored["breakdown"],
            "agents_bullish": scored["agents_bullish"],
            "direction": "buy",
            "suggested_size_pct": adjusted_size_pct,
            "safe_mode": self._safe_mode,
        })

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_watchlist_tokens(self) -> list:
        try:
            with self.get_db() as db:
                entries = db.query(Watchlist).filter_by(status="watching").limit(100).all()
                token_ids = [e.token_id for e in entries]
                if not token_ids:
                    return []
                return db.query(Token).filter(Token.id.in_(token_ids)).all()
        except Exception as e:
            self.logger.error(f"Failed to load watchlist: {e}")
            return []

    def _count_open_positions(self) -> int:
        try:
            with self.get_db() as db:
                return db.query(Trade).filter_by(status="open", is_paper=True).count()
        except Exception as e:
            self.logger.error(f"Failed to count open positions: {e}")
            return 0

    # ── Message handling ──────────────────────────────────────────────────────

    async def process_message(self, message: dict) -> None:
        msg_type = message.get("type")

        if msg_type == "macro_sentiment":
            payload = message.get("payload", {})
            self._confidence_modifier = payload.get("confidence_modifier", {}).get("modifier", 0)
            mode_info = payload.get("safe_mode", {})
            self._safe_mode = mode_info.get("mode", "normal")
            self._position_multiplier = mode_info.get("position_multiplier", 1.0)

        elif msg_type == "safe_mode_alert":
            payload = message.get("payload", {})
            mode = payload.get("mode", "normal")
            self._safe_mode = mode
            self._position_multiplier = payload.get("position_multiplier", 1.0)
            triggers = payload.get("triggers", [])
            self.logger.warning(
                f"SAFE MODE ALERT: {mode.upper()} | "
                f"{len(triggers)} trigger(s) | "
                f"Position multiplier: {self._position_multiplier:.0%}"
            )

        elif msg_type == "kill_switch":
            self.circuit_breaker.trigger("Kill switch activated")
            self.logger.critical("KILL SWITCH ACTIVATED — all trading halted")
