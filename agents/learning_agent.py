import asyncio
import json
import logging
import os
import re
from agents.base_agent import BaseAgent
from core import calibrator
from core.scoring import set_calibrated_weights
from models.schema import Trade, Token, Signal, TokenVetting, Lesson

logger = logging.getLogger("jarvis.learning")


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


class LearningAgent(BaseAgent):
    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self._client = None
        if api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=api_key)
                self.logger.info("Claude AI connected for trade post-mortems")
            except ImportError:
                self.logger.warning("anthropic package not installed")
        else:
            self.logger.warning("ANTHROPIC_API_KEY not set — AI learning disabled")

    async def run(self) -> None:
        if not self._client:
            self.logger.info("Learning agent idle — no AI client configured")
            return

        with self.get_db() as db:
            # Find closed trades that haven't been analysed yet
            analysed_ids = [
                row[0] for row in db.query(Lesson.trade_id)
                .filter(Lesson.trade_id.isnot(None)).all()
            ]
            pending = (
                db.query(Trade)
                .filter(Trade.status == "closed")
                .filter(Trade.id.notin_(analysed_ids) if analysed_ids else True)
                .limit(3)
                .all()
            )

        if not pending:
            self.logger.info("No new closed trades to analyse")
        else:
            self.logger.info(f"Running post-mortems on {len(pending)} closed trade(s)")
            for trade in pending:
                await self._post_mortem(trade)

        # Run calibration after every learning cycle (regardless of new trades)
        weights = calibrator.calibrate(self._session_factory)
        if weights:
            set_calibrated_weights(weights)

    async def _post_mortem(self, trade: Trade) -> None:
        loop = asyncio.get_running_loop()

        with self.get_db() as db:
            token = db.query(Token).filter_by(id=trade.token_id).first()
            signals = db.query(Signal).filter_by(token_id=trade.token_id).all()
            vetting = db.query(TokenVetting).filter_by(token_id=trade.token_id).first()

        prompt = self._build_prompt(trade, token, signals, vetting)

        try:
            from core.rate_limiter import acquire as rate_limit
            await rate_limit("api.anthropic.com")

            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            raw = response.content[0].text
            analysis = _extract_json(raw)

            with self.get_db() as db:
                db.add(Lesson(
                    trade_id=trade.id,
                    lesson_type=analysis.get("lesson_type", "loss" if (trade.pnl_pct or 0) < 0 else "win"),
                    what_worked=analysis.get("what_worked"),
                    what_failed=analysis.get("what_failed"),
                    rule_update=analysis.get("rule_update"),
                    applied=False,
                ))

            sym = token.symbol if token else "unknown"
            self.logger.info(
                f"Post-mortem complete for {sym} | "
                f"P&L: {(trade.pnl_pct or 0):.1%} | "
                f"Type: {analysis.get('lesson_type', 'unknown')}"
            )

        except Exception as e:
            self.logger.error(f"Post-mortem failed for trade {trade.id}: {e}")

    def _build_prompt(self, trade, token, signals, vetting) -> str:
        sym = token.symbol if token else "unknown"
        chain = token.chain if token else "unknown"
        pnl_pct = (trade.pnl_pct or 0) * 100
        hold_hours = 0
        if trade.opened_at and trade.closed_at:
            hold_hours = (trade.closed_at - trade.opened_at).total_seconds() / 3600

        signals_summary = "\n".join(
            f"  - {s.agent_name}: {s.signal_type} (strength {s.strength}) — {s.reason}"
            for s in signals
        ) or "  No signals recorded"

        vetting_summary = "Not vetted"
        if vetting:
            vetting_summary = (
                f"Pass: {vetting.overall_pass} | "
                f"Honeypot: {vetting.is_honeypot} | "
                f"Liquidity: ${(vetting.liquidity_usd or 0):,.0f} | "
                f"Fails: {vetting.fail_reasons}"
            )

        return f"""You are the Learning Agent for Jarvis 360°, an AI crypto trading system.
Analyse this completed trade and generate a structured post-mortem.

TOKEN: {sym} on {chain}
ADDRESS: {token.address if token else 'unknown'}
DIRECTION: {trade.direction}
ENTRY PRICE: ${trade.entry_price or 0:,.6f}
EXIT PRICE: ${trade.exit_price or 0:,.6f}
P&L: {pnl_pct:+.2f}% (${trade.pnl_usd or 0:+.2f})
HOLD TIME: {hold_hours:.1f} hours
EXIT REASON: {trade.exit_reason or 'unknown'}
PAPER TRADE: {trade.is_paper}

SIGNALS THAT INFLUENCED THIS TRADE:
{signals_summary}

VETTING RESULT:
{vetting_summary}

Respond ONLY with valid JSON in this exact format:
{{
  "lesson_type": "win" or "loss" or "near_miss",
  "what_worked": "one sentence on what signal or factor was correct",
  "what_failed": "one sentence on what was wrong or missing",
  "rule_update": "one concrete rule change to apply going forward, or null if none"
}}"""

    async def process_message(self, message: dict) -> None:
        if message.get("type") == "trade_closed":
            self.logger.info(f"Trade closed — queued for next learning cycle")
