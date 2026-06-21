import asyncio
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_token, extract_token_info
from models.schema import Token, Watchlist, Signal


def _momentum_strength(info: dict) -> float:
    """
    Score token momentum 0.0–1.0 from DexScreener data.

    Components:
      - 1h price change (40%): positive momentum
      - 24h volume (30%):      sufficient liquidity
      - Buy/sell ratio (30%):  net buying pressure
    """
    score = 0.0

    # Price change last hour (up to 0.40)
    pc1h = info.get("price_change_1h") or 0
    if pc1h >= 20:
        score += 0.40
    elif pc1h >= 10:
        score += 0.30
    elif pc1h >= 5:
        score += 0.20
    elif pc1h >= 2:
        score += 0.10
    elif pc1h < -5:
        score -= 0.10   # penalty for declining price

    # 24h volume (up to 0.30)
    vol = info.get("volume_24h") or 0
    if vol >= 2_000_000:
        score += 0.30
    elif vol >= 500_000:
        score += 0.20
    elif vol >= 100_000:
        score += 0.10
    elif vol >= 50_000:
        score += 0.05

    # Buy/sell ratio (up to 0.30)
    buys = info.get("buys_24h") or 0
    sells = info.get("sells_24h") or 1
    ratio = buys / max(sells, 1)
    if ratio >= 2.5:
        score += 0.30
    elif ratio >= 1.8:
        score += 0.20
    elif ratio >= 1.3:
        score += 0.10

    return round(min(1.0, max(0.0, score)), 3)


class StrategyAgent(BaseAgent):
    MIN_MOMENTUM = 0.45   # Only publish a signal if momentum is above this threshold

    async def run(self) -> None:
        watchlist = self._get_watchlist()
        if not watchlist:
            self.logger.info("Watchlist is empty — no momentum analysis to run")
            return

        self.logger.info(f"Analysing momentum for {len(watchlist)} watchlist token(s)")
        loop = asyncio.get_running_loop()

        for token in watchlist:
            await rate_limit("api.dexscreener.com")
            pair = await loop.run_in_executor(None, get_token, token.address)
            if not pair:
                continue

            info = extract_token_info(pair)
            strength = _momentum_strength(info)
            signal_type = "bullish" if strength >= self.MIN_MOMENTUM else "neutral"

            if strength >= self.MIN_MOMENTUM:
                self.logger.info(
                    f"Momentum signal: {token.symbol or token.address[:8]} | "
                    f"strength={strength:.2f} | "
                    f"1h={info.get('price_change_1h', 0):+.1f}% | "
                    f"vol=${info.get('volume_24h', 0):,.0f}"
                )
                self._store_signal(token.id, strength, signal_type, info)
                await self.publish("strategy_signal", {
                    "address": token.address,
                    "symbol": token.symbol,
                    "chain": token.chain,
                    "momentum_strength": strength,
                    "signal_type": signal_type,
                    "price_change_1h": info.get("price_change_1h"),
                    "volume_24h": info.get("volume_24h"),
                    "buy_sell_ratio": (info.get("buys_24h") or 0) / max(info.get("sells_24h") or 1, 1),
                })

    def _get_watchlist(self) -> list:
        try:
            with self.get_db() as db:
                entries = (
                    db.query(Watchlist)
                    .filter_by(status="watching")
                    .limit(50)
                    .all()
                )
                token_ids = [e.token_id for e in entries]
                if not token_ids:
                    return []
                return db.query(Token).filter(Token.id.in_(token_ids)).all()
        except Exception as e:
            self.logger.error(f"Failed to load watchlist: {e}")
            return []

    def _store_signal(self, token_id: int, strength: float, signal_type: str, info: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(Signal(
                    token_id=token_id,
                    agent_name=self.name,
                    signal_type=signal_type,
                    strength=round(strength * 100),
                    reason=(
                        f"Momentum: 1h={info.get('price_change_1h', 0):+.1f}% "
                        f"vol=${info.get('volume_24h', 0):,.0f}"
                    ),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                ))
        except Exception as e:
            self.logger.error(f"Failed to store strategy signal: {e}")

    async def process_message(self, message: dict) -> None:
        pass
