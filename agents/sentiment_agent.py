import asyncio
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.sentiment_feeds import (
    get_fear_greed, fear_greed_to_strength, fear_greed_to_signal_type,
    get_global_market_data,
)
from models.schema import Signal


class SentimentAgent(BaseAgent):
    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self._last_fg_value: int = 50

    async def run(self) -> None:
        loop = asyncio.get_running_loop()

        # ── Fear & Greed Index ───────────────────────────────────────────────
        await rate_limit("api.alternative.me")
        fg = await loop.run_in_executor(None, get_fear_greed)
        fg_value = fg.get("value", 50)
        fg_class = fg.get("classification", "Neutral")
        fg_strength = fear_greed_to_strength(fg_value)
        fg_signal_type = fear_greed_to_signal_type(fg_value)

        self.logger.info(
            f"Fear & Greed: {fg_value}/100 ({fg_class}) | "
            f"Strength: {fg_strength:.2f} | Signal: {fg_signal_type}"
        )

        # ── Global market direction ───────────────────────────────────────────
        await rate_limit("api.coingecko.com")
        global_data = await loop.run_in_executor(None, get_global_market_data)
        mkt_change = global_data.get("market_cap_change_24h_pct", 0)

        # Combine: weighted average (FG 70%, market direction 30%)
        mkt_strength = self._market_change_to_strength(mkt_change)
        combined_strength = fg_strength * 0.70 + mkt_strength * 0.30
        combined_strength = round(combined_strength, 3)

        self._last_fg_value = fg_value

        # Store signal (global — not tied to a specific token)
        self._store_global_signal(fg_value, combined_strength, fg_signal_type, fg_class)

        await self.publish("sentiment_signal", {
            "fear_greed": fg_value,
            "fear_greed_class": fg_class,
            "market_change_24h": mkt_change,
            "combined_strength": combined_strength,
            "signal_type": fg_signal_type,
        })

    def _market_change_to_strength(self, pct_change: float) -> float:
        """Convert 24h total market cap change % to sentiment strength 0-1."""
        if pct_change >= 5:
            return 0.9
        elif pct_change >= 2:
            return 0.7
        elif pct_change >= 0:
            return 0.55
        elif pct_change >= -2:
            return 0.4
        elif pct_change >= -5:
            return 0.2
        else:
            return 0.05

    def _store_global_signal(
        self, fg_value: int, strength: float, signal_type: str, label: str
    ) -> None:
        try:
            with self.get_db() as db:
                # Global sentiment signal has no token_id
                db.add(Signal(
                    token_id=None,
                    agent_name=self.name,
                    signal_type=signal_type,
                    strength=round(strength * 100),
                    reason=f"Fear & Greed {fg_value}/100 ({label})",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                ))
        except Exception as e:
            self.logger.error(f"Failed to store sentiment signal: {e}")

    async def process_message(self, message: dict) -> None:
        pass
