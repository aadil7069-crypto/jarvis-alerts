"""
WhaleAgent — Large Token Accumulation Detection
================================================
Detects large buy orders (>$25k) in watchlist tokens regardless of wallet identity.

CRITICAL DISTINCTION from SmartMoneyAgent:
  SmartMoneyAgent asks "is this a QUALITY wallet?" (win_rate, history, pnl track record)
  WhaleAgent asks "is anyone spending LARGE AMOUNTS?" — a new wallet that dumps $100k
  into a token is a whale signal even if it has zero track record.

Two complementary signals generated per watchlist token:
  1. Large buy detection — GMGN top traders filtered by buy_amount_usd > $25k
  2. Holder concentration — Helius top-10 holders × DexScreener price to compute
     USD position sizes; a new large holder appearing is accumulation evidence

Both signals map to the "whale_accumulation" scoring bucket.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.gmgn import get_token_smart_money_activity
from data.helius import get_token_largest_accounts, detect_large_token_buys
from data.dexscreener import get_token, extract_token_info
from data.wallets import all_solana_tracked
from models.schema import Signal, Token, Watchlist


_MIN_WHALE_USD = 25_000      # minimum single-wallet buy to count as whale activity
_SCALE_USD = 500_000         # total whale buy USD that maps to strength = 1.0
_TOP_HOLDER_MIN_USD = 50_000 # top holder must hold > $50k to count as whale concentration
_SIGNAL_EXPIRY_HOURS = 3


class WhaleAgent(BaseAgent):

    async def run(self) -> None:
        watchlist = self._get_watchlist_tokens()
        if not watchlist:
            self.logger.info("Watchlist empty — skipping whale scan")
            return

        self.logger.info(f"Whale scan: {len(watchlist)} watchlist token(s)")
        found = 0

        for token in watchlist[:15]:   # cap per tick for rate limits
            detected = await self._scan_large_buys(token)
            if detected:
                found += 1

        # ── Secondary: wallet-first Helius scan (only if wallets.py is populated) ──
        manual_wallets = all_solana_tracked()
        if manual_wallets:
            watchlist_mints = {t.address for t in watchlist}
            for wallet in manual_wallets[:10]:
                await rate_limit("api.helius.xyz")
                loop = asyncio.get_running_loop()
                token_buys = await loop.run_in_executor(
                    None, lambda w=wallet: detect_large_token_buys(w, watchlist_mints)
                )
                if token_buys:
                    self.logger.info(
                        f"Manual wallet [{wallet[:8]}] made {len(token_buys)} "
                        f"watchlist token purchase(s)"
                    )
                    await self._process_helius_buys(token_buys, wallet, watchlist)

        self.logger.info(f"Whale scan complete — {found} large buy signal(s) detected")

    # ── Primary: GMGN large buy detection ────────────────────────────────────

    async def _scan_large_buys(self, token: Token) -> bool:
        """
        Query GMGN top traders for this token, filter by buy_amount_usd.
        Completely independent of wallet quality — pure size signal.
        """
        loop = asyncio.get_running_loop()
        await rate_limit("gmgn.ai")

        traders = await loop.run_in_executor(
            None,
            lambda: get_token_smart_money_activity(token.address, chain="sol", limit=30),
        )
        if not traders:
            return False

        # Filter: only include wallets with large buy amounts AND still holding
        whales = [
            t for t in traders
            if t.get("buy_amount_usd", 0) >= _MIN_WHALE_USD and t.get("holding", True)
        ]
        if not whales:
            return False

        total_usd = sum(w.get("buy_amount_usd", 0) for w in whales)
        strength = min(1.0, total_usd / _SCALE_USD)

        self.logger.info(
            f"Whale buy on {token.symbol or token.address[:8]}: "
            f"{len(whales)} large buyer(s) | "
            f"total=${total_usd:,.0f} | strength={strength:.2f}"
        )

        self._store_signal(token.id, strength, reason=(
            f"Whale accumulation: {len(whales)} wallet(s) bought ${total_usd:,.0f} "
            f"(min ${_MIN_WHALE_USD:,} per wallet)"
        ))

        await self.publish("whale_signal", {
            "address": token.address,
            "symbol": token.symbol,
            "chain": token.chain,
            "whale_count": len(whales),
            "total_buy_usd": round(total_usd, 2),
            "strength": round(strength, 3),
            "source": "gmgn",
        })
        return True

    # ── Secondary: Helius holder concentration ────────────────────────────────

    async def _scan_holder_concentration(self, token: Token) -> bool:
        """
        Check if a large holder (> $50k position) exists via Helius.
        Uses DexScreener price to convert token amounts to USD.
        """
        loop = asyncio.get_running_loop()

        await rate_limit("api.helius.xyz")
        holders = await loop.run_in_executor(
            None, lambda: get_token_largest_accounts(token.address)
        )
        if not holders:
            return False

        await rate_limit("api.dexscreener.com")
        pair = await loop.run_in_executor(None, lambda: get_token(token.address))
        if not pair:
            return False

        info = extract_token_info(pair)
        price_usd = info.get("price_usd", 0)
        if not price_usd:
            return False

        whale_holders = []
        for h in holders[:10]:
            ui_amount = float(h.get("uiAmount") or 0)
            position_usd = ui_amount * price_usd
            if position_usd >= _TOP_HOLDER_MIN_USD:
                whale_holders.append({
                    "address": h.get("address", ""),
                    "position_usd": position_usd,
                    "ui_amount": ui_amount,
                })

        if not whale_holders:
            return False

        total_position_usd = sum(w["position_usd"] for w in whale_holders)
        strength = min(1.0, total_position_usd / (_SCALE_USD * 2))   # larger scale for holders

        self.logger.info(
            f"Whale concentration on {token.symbol or token.address[:8]}: "
            f"{len(whale_holders)} large holder(s) | "
            f"total position=${total_position_usd:,.0f}"
        )

        self._store_signal(token.id, strength, reason=(
            f"Whale holder: {len(whale_holders)} address(es) with "
            f">${_TOP_HOLDER_MIN_USD:,} position (total ${total_position_usd:,.0f})"
        ))
        return True

    # ── Helius wallet-first processing ────────────────────────────────────────

    async def _process_helius_buys(
        self, token_buys: list, wallet: str, watchlist: list
    ) -> None:
        """Process token buys detected via manual wallet scanning."""
        token_map = {t.address: t for t in watchlist}
        for buy in token_buys:
            token = token_map.get(buy["mint"])
            if not token:
                continue
            # Without real-time price we can't compute USD value here,
            # so emit a low-confidence signal to indicate activity was detected.
            self._store_signal(
                token.id,
                strength=0.4,
                reason=f"Manual tracked wallet [{wallet[:8]}] bought {buy['amount_tokens']:.2f} tokens",
            )

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _store_signal(self, token_id: int, strength: float, reason: str = "") -> None:
        try:
            with self.get_db() as db:
                db.add(Signal(
                    token_id=token_id,
                    agent_name=self.name,
                    signal_type="bullish",
                    strength=round(strength * 100),
                    reason=reason,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=_SIGNAL_EXPIRY_HOURS),
                ))
        except Exception as e:
            self.logger.error(f"Signal store failed: {e}")

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

    async def process_message(self, message: dict) -> None:
        pass
