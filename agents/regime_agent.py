"""
RegimeAgent — Market Regime Classification
==========================================
Runs every 5 minutes. Fetches 7 days of BTC 4-hour candles from CoinGecko
and classifies the market into one of four regimes:

  trending_up | trending_down | ranging | volatile

Publishes "regime_update" to the Orchestrator, which compounds the regime's
position multiplier with the prediction market safe-mode multiplier.

Why BTC? BTC dominance means its trend and volatility regime predicts
memecoin behaviour more reliably than any individual token's chart.
"""
import asyncio
from datetime import datetime, timezone

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from core.regime import detect_regime, regime_position_multiplier
from data.price_feeds import get_btc_ohlcv
from models.schema import Signal


class RegimeAgent(BaseAgent):

    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self._current_regime = "unknown"

    async def run(self) -> None:
        loop = asyncio.get_running_loop()

        await rate_limit("api.coingecko.com")
        ohlcv = await loop.run_in_executor(None, lambda: get_btc_ohlcv(days=7))

        if not ohlcv:
            self.logger.warning("BTC OHLCV unavailable — regime unchanged")
            return

        result = detect_regime(ohlcv)
        regime = result["regime"]
        multiplier = regime_position_multiplier(regime)

        if regime != self._current_regime:
            self.logger.info(
                f"Regime change: {self._current_regime.upper()} → {regime.upper()} | "
                f"BTC=${result['current']:,.0f} | "
                f"momentum={result['momentum']:+.2%} | "
                f"atr={result['atr_pct']:.2%} | "
                f"position_multiplier={multiplier:.0%}"
            )
            self._current_regime = regime
        else:
            self.logger.debug(
                f"Regime: {regime.upper()} | "
                f"BTC=${result['current']:,.0f} | "
                f"momentum={result['momentum']:+.2%}"
            )

        self._store_regime_signal(regime, multiplier)

        await self.publish("regime_update", {
            "regime": regime,
            "position_multiplier": multiplier,
            "btc_price": result["current"],
            "btc_sma20": result["sma"],
            "momentum": result["momentum"],
            "atr_pct": result["atr_pct"],
        })

    def _store_regime_signal(self, regime: str, multiplier: float) -> None:
        """Persist regime as a global Signal (token_id=None) so the dashboard can read it."""
        signal_type = {
            "trending_up": "bullish",
            "trending_down": "bearish",
        }.get(regime, "neutral")
        try:
            with self.get_db() as db:
                db.add(Signal(
                    token_id=None,
                    agent_name="regime",
                    signal_type=signal_type,
                    strength=multiplier * 100,
                    reason=regime,
                    created_at=datetime.now(timezone.utc),
                ))
        except Exception as e:
            self.logger.error(f"Failed to store regime signal: {e}")

    async def process_message(self, message: dict) -> None:
        pass
