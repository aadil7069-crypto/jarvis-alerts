"""
SmartMoneyAgent — Rewritten for Phase 6 Intelligence Upgrade
=============================================================
Old design: watch wallets from wallets.py (empty → zero signals)
New design: for each watchlist token, ask GMGN who is buying it right now

This inversion is the key change. Instead of hoping a pre-curated wallet
happens to buy something we care about, we directly ask "is smart money
buying THIS specific token?" and score the buying wallets on the fly.

Two complementary approaches run in parallel:
  1. Token-first:  for each watchlist token → GMGN top traders → who is buying?
  2. Wallet-first: fetch GMGN's top smart money wallets → track what they buy

GMGN auto-discovers the smart money wallets — no manual curation needed.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.gmgn import (
    get_smart_money_wallets,
    get_token_smart_money_activity,
    get_wallet_stats,
)
from data.wallets import all_solana_tracked, all_bnb_tracked
from data.helius import get_transactions
from models.schema import Signal, Token, WalletScore, Watchlist, SmartMoneyBuy


# Wallets with scores at or above this threshold are considered reliable signals
_MIN_WALLET_SCORE = 55.0

# Coordinated buy: this many distinct smart money wallets buying same token
# within the last hour triggers a stronger signal
_COORDINATED_BUY_THRESHOLD = 3


class SmartMoneyAgent(BaseAgent):

    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self._gmgn_wallet_cache: list[dict] = []   # refreshed every 4 ticks
        self._tick_count = 0

    async def run(self) -> None:
        self._tick_count += 1
        loop = asyncio.get_running_loop()

        # ── Refresh GMGN wallet list every 4 ticks ───────────────────────────
        if self._tick_count == 1 or self._tick_count % 4 == 0:
            await rate_limit("gmgn.ai")
            wallets = await loop.run_in_executor(
                None, lambda: get_smart_money_wallets(chain="sol", limit=50)
            )
            if wallets:
                self._gmgn_wallet_cache = wallets
                self._upsert_wallet_scores(wallets, source="gmgn")
                self.logger.info(
                    f"GMGN wallet list refreshed — {len(wallets)} smart money wallets loaded"
                )

        watchlist_tokens = self._get_watchlist_tokens()
        if not watchlist_tokens:
            self.logger.info("Watchlist empty — nothing to scan for smart money activity")
            return

        self.logger.info(
            f"Scanning smart money activity on {len(watchlist_tokens)} watchlist token(s)"
        )

        # ── Token-first: who is buying each watchlist token? ─────────────────
        for token in watchlist_tokens[:15]:   # cap per tick for rate limits
            await rate_limit("gmgn.ai")
            buyers = await loop.run_in_executor(
                None,
                lambda a=token.address: get_token_smart_money_activity(a, chain="sol", limit=20),
            )
            if not buyers:
                continue

            # Only count wallets that are actively holding (bought, haven't sold)
            active_buyers = [b for b in buyers if b.get("holding", True)]
            if not active_buyers:
                continue

            await self._process_token_buyers(token, active_buyers)

        # ── Wallet-first: manual wallets from wallets.py (if populated) ──────
        manual_wallets = all_solana_tracked()
        if manual_wallets:
            watchlist_addresses = {t.address for t in watchlist_tokens}
            for wallet in manual_wallets[:5]:
                await rate_limit("api.helius.xyz")
                txns = await loop.run_in_executor(
                    None, lambda w=wallet: get_transactions(w, limit=20)
                )
                await self._scan_helius_transactions(txns, wallet, watchlist_addresses)

    async def _process_token_buyers(self, token: Token, buyers: list) -> None:
        """
        Score and store a batch of smart money buyers for a specific token.
        Detects coordinated buying when multiple buyers are active simultaneously.
        """
        buyer_count = len(buyers)
        total_buy_usd = sum(b.get("buy_amount_usd", 0) for b in buyers)

        # Retrieve wallet scores for all buyers to compute signal strength
        scores = [self._get_wallet_score(b["wallet_address"]) for b in buyers]
        avg_score = sum(scores) / len(scores) if scores else 50.0

        # Signal strength: buyer quality × buyer count (coordinated = stronger)
        base_strength = avg_score / 100.0
        coordination_bonus = min(0.3, (buyer_count - 1) * 0.1)
        strength = min(1.0, base_strength + coordination_bonus)

        is_coordinated = buyer_count >= _COORDINATED_BUY_THRESHOLD

        self.logger.info(
            f"Smart money on {token.symbol or token.address[:8]}: "
            f"{buyer_count} buyer(s) | avg_score={avg_score:.0f} | "
            f"strength={strength:.2f} | total_buy=${total_buy_usd:,.0f}"
            + (" | COORDINATED BUY" if is_coordinated else "")
        )

        # Store each buy in the SmartMoneyBuy log
        for buyer in buyers:
            self._log_smart_money_buy(token, buyer)

        # Store signal
        self._store_signal(
            token.id,
            strength,
            buyer_count,
            is_coordinated,
            total_buy_usd,
        )

        await self.publish("smart_money_signal", {
            "address": token.address,
            "symbol": token.symbol,
            "chain": token.chain,
            "buyer_count": buyer_count,
            "avg_wallet_score": round(avg_score, 1),
            "strength": round(strength, 3),
            "total_buy_usd": round(total_buy_usd, 2),
            "is_coordinated": is_coordinated,
            "source": "gmgn",
        })

    async def _scan_helius_transactions(
        self, txns: list, wallet: str, watchlist_addresses: set
    ) -> None:
        """Secondary scan: check manual wallets against watchlist tokens via Helius."""
        wallet_score = self._get_wallet_score(wallet)
        for tx in txns:
            for transfer in tx.get("tokenTransfers", []):
                mint = transfer.get("mint", "")
                if mint not in watchlist_addresses:
                    continue
                strength = min(1.0, (wallet_score / 100.0) * 0.8)
                self.logger.info(
                    f"Manual wallet [{wallet[:8]}] active on {mint[:8]} | score={wallet_score:.0f}"
                )
                token = self._get_token_by_address(mint)
                if token:
                    self._log_smart_money_buy(
                        token,
                        {
                            "wallet_address": wallet,
                            "wallet_label": "manual",
                            "buy_amount_usd": transfer.get("tokenAmount", 0),
                        },
                        source="helius",
                    )
                    self._store_signal(token.id, strength, 1, False, 0)

    # ── Wallet score computation ──────────────────────────────────────────────

    def _compute_score(self, stats: dict) -> float:
        """
        Composite wallet quality score (0-100).

        Components:
          - win_rate (30%):      fraction of profitable trades
          - realized_pnl (40%): profit in last 7 days (scales to $100k)
          - history (15%):      number of trades (scales to 50)
          - trade_size (15%):   average trade size (scales to $10k)
        """
        win_rate = min(1.0, (stats.get("win_rate") or 0) / 100.0)
        pnl = stats.get("realized_pnl", 0) or 0
        profit_score = min(1.0, pnl / 100_000) if pnl > 0 else 0.0
        history_score = min(1.0, (stats.get("trade_count") or 0) / 50)
        size_score = min(1.0, (stats.get("avg_trade_size_usd") or 0) / 10_000)

        raw = (
            win_rate * 0.30
            + profit_score * 0.40
            + history_score * 0.15
            + size_score * 0.15
        )
        return round(raw * 100, 1)

    # ── DB operations ─────────────────────────────────────────────────────────

    def _upsert_wallet_scores(self, wallets: list, source: str = "gmgn") -> None:
        """Bulk upsert wallet quality scores from GMGN data."""
        try:
            with self.get_db() as db:
                for w in wallets:
                    addr = w.get("address")
                    if not addr:
                        continue
                    score = self._compute_score(w)
                    row = db.query(WalletScore).filter_by(address=addr).first()
                    if row:
                        row.score = score
                        row.win_rate_7d = w.get("win_rate", 0) or 0
                        row.realized_pnl_7d = w.get("realized_pnl_7d", 0) or 0
                        row.trade_count_7d = w.get("trade_count", 0) or 0
                        row.avg_trade_size_usd = w.get("avg_trade_size_usd", 0) or 0
                        row.label = w.get("wallet_label")
                        row.source = source
                        row.updated_at = datetime.now(timezone.utc)
                    else:
                        db.add(WalletScore(
                            address=addr,
                            chain="solana",
                            label=w.get("wallet_label"),
                            source=source,
                            score=score,
                            win_rate_7d=w.get("win_rate", 0) or 0,
                            realized_pnl_7d=w.get("realized_pnl_7d", 0) or 0,
                            trade_count_7d=w.get("trade_count", 0) or 0,
                            avg_trade_size_usd=w.get("avg_trade_size_usd", 0) or 0,
                            first_seen=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        ))
        except Exception as e:
            self.logger.error(f"Wallet score upsert failed: {e}")

    def _get_wallet_score(self, address: str) -> float:
        try:
            with self.get_db() as db:
                row = db.query(WalletScore).filter_by(address=address).first()
                if row:
                    return row.score
                db.add(WalletScore(
                    address=address,
                    chain="solana",
                    source="gmgn",
                    score=50.0,
                    first_seen=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ))
            return 50.0
        except Exception as e:
            self.logger.error(f"Wallet score lookup failed: {e}")
            return 50.0

    def _get_watchlist_tokens(self) -> list:
        try:
            with self.get_db() as db:
                entries = db.query(Watchlist).filter_by(status="watching").limit(50).all()
                token_ids = [e.token_id for e in entries]
                if not token_ids:
                    return []
                return db.query(Token).filter(Token.id.in_(token_ids)).all()
        except Exception as e:
            self.logger.error(f"Watchlist lookup failed: {e}")
            return []

    def _get_token_by_address(self, address: str):
        try:
            with self.get_db() as db:
                return db.query(Token).filter_by(address=address).first()
        except Exception:
            return None

    def _log_smart_money_buy(
        self, token: Token, buyer: dict, source: str = "gmgn"
    ) -> None:
        try:
            with self.get_db() as db:
                db.add(SmartMoneyBuy(
                    token_id=token.id,
                    wallet_address=buyer.get("wallet_address", ""),
                    wallet_label=buyer.get("wallet_label"),
                    wallet_score=self._get_wallet_score(buyer.get("wallet_address", "")),
                    source=source,
                    buy_amount_usd=buyer.get("buy_amount_usd"),
                    detected_at=datetime.now(timezone.utc),
                    is_holding=buyer.get("holding", True),
                ))
        except Exception as e:
            self.logger.error(f"SmartMoneyBuy log failed: {e}")

    def _store_signal(
        self,
        token_id: int,
        strength: float,
        buyer_count: int,
        is_coordinated: bool,
        total_usd: float,
    ) -> None:
        try:
            with self.get_db() as db:
                reason = (
                    f"{'COORDINATED ' if is_coordinated else ''}"
                    f"Smart money: {buyer_count} wallet(s) | ${total_usd:,.0f} bought"
                )
                db.add(Signal(
                    token_id=token_id,
                    agent_name=self.name,
                    signal_type="bullish",
                    strength=round(strength * 100),
                    reason=reason,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
                ))
        except Exception as e:
            self.logger.error(f"Signal store failed: {e}")

    async def process_message(self, message: dict) -> None:
        if message.get("type") == "trade_closed":
            pass   # Future: update wallet scores based on whether the trade won
