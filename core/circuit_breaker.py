import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("jarvis.circuit_breaker")


class CircuitBreaker:
    """Emergency stop system. Halts all trading when risk limits are breached."""

    def __init__(self, config: dict):
        risk = config.get("risk", {})
        self.daily_loss_limit = risk.get("daily_loss_limit_pct", 0.05)
        self.max_drawdown = risk.get("max_drawdown_pct", 0.15)
        self._triggered = False
        self._reason: Optional[str] = None
        self._triggered_at: Optional[datetime] = None

    def trigger(self, reason: str) -> None:
        self._triggered = True
        self._reason = reason
        self._triggered_at = datetime.now(timezone.utc)
        logger.critical(f"CIRCUIT BREAKER TRIGGERED — {reason}")

    def reset(self, authorized_by: str = "manual") -> None:
        logger.warning(f"Circuit breaker reset by: {authorized_by}")
        self._triggered = False
        self._reason = None
        self._triggered_at = None

    def check(self, daily_pnl_pct: float = 0.0, drawdown_pct: float = 0.0) -> None:
        if daily_pnl_pct < -self.daily_loss_limit:
            self.trigger(f"Daily loss limit hit: {daily_pnl_pct:.1%}")
        if drawdown_pct > self.max_drawdown:
            self.trigger(f"Max drawdown exceeded: {drawdown_pct:.1%}")

    @property
    def trading_allowed(self) -> bool:
        return not self._triggered

    def status(self) -> dict:
        return {
            "triggered": self._triggered,
            "reason": self._reason,
            "triggered_at": self._triggered_at.isoformat() if self._triggered_at else None,
            "trading_allowed": self.trading_allowed,
        }
