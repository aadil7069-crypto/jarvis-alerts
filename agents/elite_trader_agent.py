"""
EliteTraderAgent — Powered by Birdeye Top Traders
===================================================
Tracks wallets that consistently appear in Birdeye's top daily P&L rankings.
A wallet that ranks in the top 20 by profit multiple days in a row is a
statistically significant predictor of trading skill — not luck.

Signal generation:
  - Fetch Birdeye's top traders by realized P&L (daily/weekly)
  - Cross-reference: are any of these elite traders buying watchlist tokens?
  - Promote wallets to WalletScore DB with elite label for SmartMoneyAgent

This agent feeds the smart money layer with an independently sourced wallet list
that has different coverage to GMGN (Birdeye captures institutional and prop-desk
activity more than GMGN's retail-smart-money focus).
"""
import asyncio
from datetime import datetime, timedelta, timezone

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.birdeye import get_top_traders, get_token_price
from data.helius import get_transactions
from models.schema import Signal, Token, WalletScore, Watchlist, SmartMoneyBuy

_ELITE_SCORE = 80.0    # Wallets from Birdeye top-20 start with this score
_MIN_PNL_USD = 5_000   # Only track traders with >$5k daily P&L (filters noise)


class EliteTraderAgent(BaseAgent):

    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self._elite_wallets: list[dict] = []   # refreshed each tick
        self._tick_count = 0

    async def run(self) -> None:
        self._tick_count += 1
        loop = asyncio.get_running_loop()

        # ── Fetch top traders from Birdeye ───────────────────────────────────
        await rate_limit("public-api.birdeye.so")
        today_traders = await loop.run_in_executor(
            None, lambda: get_top_traders(chain="solana", limit=20, period="today")
        )

        if not today_traders:
            self.logger.info("Birdeye top traders unavailable (check BIRDEYE_API_KEY)")
            return

        # Filter by minimum P&L to remove noise
        qualified = [t for t in today_traders if t.get("pnl", 0) >= _MIN_PNL_USD]

        if not qualified:
            self.logger.info(
                f"Birdeye returned {len(today_traders)} traders, "
                f"none exceed ${_MIN_PNL_USD:,.0f} P&L threshold"
            )
            return

        self.logger.info(
            f"Birdeye elite traders: {len(qualified)} traders above ${_MIN_PNL_USD:,.0f} P&L today"
        )

        # Upsert these wallets as elite-tier in our WalletScore DB
        self._upsert_elite_wallets(qualified)
        self._elite_wallets = qualified

        # Cross-reference with watchlist: are any buying our tokens?
        watchlist_addresses = self._get_watchlist_addresses()
        if not watchlist_addresses:
            return

        for trader in qualified[:10]:   # cap per tick
            wallet = trader.get("address")
            if not wallet:
                continue
            await rate_limit("api.helius.xyz")
            txns = await loop.run_in_executor(
                None, lambda w=wallet: get_transactions(w, limit=20)
            )
            await self._check_trader_activity(txns, trader, watchlist_addresses)

    async def _check_trader_activity(
        self, txns: list, trader: dict, watchlist: set
    ) -> None:
        wallet = trader["address"]
        wallet_score = trader.get("pnl", 0) / 1000 + _ELITE_SCORE  # P&L boosts score
        wallet_score = min(99.0, wallet_score)

        for tx in txns:
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint", "")
                if mint not in watchlist:
                    continue

                strength = min(1.0, wallet_score / 100.0)
                token = self._get_token(mint)
                if not token:
                    continue

                self.logger.info(
                    f"Elite trader [{wallet[:8]}] active on {token.symbol or mint[:8]} "
                    f"| today_pnl=${trader.get('pnl', 0):,.0f} | strength={strength:.2f}"
                )

                self._log_buy(token, wallet, trader.get("pnl", 0))
                self._store_signal(token.id, strength, trader)

                await self.publish("elite_trader_signal", {
                    "address": mint,
                    "symbol": token.symbol,
                    "wallet": wallet,
                    "trader_pnl_today": trader.get("pnl"),
                    "trader_win_rate": trader.get("win_rate"),
                    "strength": round(strength, 3),
                    "source": "birdeye",
                })

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _upsert_elite_wallets(self, traders: list) -> None:
        try:
            with self.get_db() as db:
                for t in traders:
                    addr = t.get("address")
                    if not addr:
                        continue
                    score = min(99.0, _ELITE_SCORE + t.get("pnl", 0) / 1000)
                    row = db.query(WalletScore).filter_by(address=addr).first()
                    if row:
                        row.score = max(row.score, score)
                        row.label = "elite"
                        row.source = "birdeye"
                        row.win_rate_7d = t.get("win_rate", 0) or 0
                        row.trade_count_7d = t.get("trade_count", 0) or 0
                        row.updated_at = datetime.now(timezone.utc)
                    else:
                        db.add(WalletScore(
                            address=addr,
                            chain="solana",
                            label="elite",
                            source="birdeye",
                            score=score,
                            win_rate_7d=t.get("win_rate", 0) or 0,
                            trade_count_7d=t.get("trade_count", 0) or 0,
                            first_seen=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        ))
        except Exception as e:
            self.logger.error(f"Elite wallet upsert failed: {e}")

    def _get_watchlist_addresses(self) -> set:
        try:
            with self.get_db() as db:
                entries = db.query(Watchlist).filter_by(status="watching").all()
                token_ids = [e.token_id for e in entries]
                if not token_ids:
                    return set()
                tokens = db.query(Token).filter(Token.id.in_(token_ids)).all()
                return {t.address for t in tokens}
        except Exception as e:
            self.logger.error(f"Watchlist lookup failed: {e}")
            return set()

    def _get_token(self, address: str):
        try:
            with self.get_db() as db:
                return db.query(Token).filter_by(address=address).first()
        except Exception:
            return None

    def _log_buy(self, token: Token, wallet: str, pnl: float) -> None:
        try:
            with self.get_db() as db:
                db.add(SmartMoneyBuy(
                    token_id=token.id,
                    wallet_address=wallet,
                    wallet_label="elite",
                    wallet_score=min(99.0, _ELITE_SCORE + pnl / 1000),
                    source="birdeye",
                    detected_at=datetime.now(timezone.utc),
                ))
        except Exception as e:
            self.logger.error(f"SmartMoneyBuy log failed: {e}")

    def _store_signal(self, token_id: int, strength: float, trader: dict) -> None:
        try:
            with self.get_db() as db:
                db.add(Signal(
                    token_id=token_id,
                    agent_name=self.name,
                    signal_type="bullish",
                    strength=round(strength * 100),
                    reason=f"Elite trader (Birdeye top-20) | P&L today: ${trader.get('pnl', 0):,.0f}",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
                ))
        except Exception as e:
            self.logger.error(f"Signal store failed: {e}")

    async def process_message(self, message: dict) -> None:
        pass
