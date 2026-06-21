from agents.base_agent import BaseAgent


class RiskAgent(BaseAgent):
    def __init__(self, name, message_bus, db_session, circuit_breaker, config):
        super().__init__(name, message_bus, db_session, config)
        self.circuit_breaker = circuit_breaker
        self._safe_mode = "normal"
        self._position_multiplier = 1.0

    async def run(self) -> None:
        self.logger.info(
            f"Risk check | Circuit breaker: {'OPEN' if self.circuit_breaker.trading_allowed else 'TRIGGERED'} | "
            f"Safe mode: {self._safe_mode.upper()} | "
            f"Position multiplier: {self._position_multiplier:.0%}"
        )
        # Phase 3: calculate real P&L and drawdown, feed into circuit_breaker.check()

    async def process_message(self, message: dict) -> None:
        msg_type = message.get("type")

        if msg_type == "pnl_update":
            payload = message.get("payload", {})
            self.circuit_breaker.check(
                daily_pnl_pct=payload.get("daily_pnl_pct", 0.0),
                drawdown_pct=payload.get("drawdown_pct", 0.0),
            )

        elif msg_type == "macro_sentiment":
            payload = message.get("payload", {})
            safe = payload.get("safe_mode", {})
            self._safe_mode = safe.get("mode", "normal")
            self._position_multiplier = safe.get("position_multiplier", 1.0)

            if self._safe_mode == "safe":
                self.logger.warning(
                    f"Safe mode ACTIVE — positions reduced to "
                    f"{self._position_multiplier:.0%} of normal"
                )

        elif msg_type == "safe_mode_alert":
            payload = message.get("payload", {})
            self.logger.warning(
                f"Safe mode alert: {payload.get('mode', '').upper()} | "
                f"Triggers: {len(payload.get('triggers', []))}"
            )
