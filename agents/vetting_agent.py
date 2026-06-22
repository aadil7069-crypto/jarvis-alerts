import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_token, extract_token_info
from data.goplus import check_token as goplus_check
from data.honeypot import check_bsc
from models.schema import Token, TokenVetting, Watchlist

_SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_BNB_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _valid_address(address: str, chain: str) -> bool:
    if chain == "solana":
        return bool(_SOLANA_RE.match(address))
    if chain in ("bnb", "bsc"):
        return bool(_BNB_RE.match(address))
    return False


class VettingAgent(BaseAgent):
    async def run(self) -> None:
        self.logger.info("Vetting agent ready — waiting for vet_token messages")

    async def process_message(self, message: dict) -> None:
        if message.get("type") != "vet_token":
            return
        payload = message.get("payload", {})
        address = payload.get("address", "").strip()
        chain = payload.get("chain", "solana").lower()

        if not address:
            return

        # Fix #3 + #6: validate address format before touching any API
        if not _valid_address(address, chain):
            self.logger.warning(f"Rejected invalid address format [{address}] for chain {chain}")
            return

        # Fix #3: skip if already vetted in the last 24 hours
        if self._recently_vetted(address):
            self.logger.debug(f"Skipping already-vetted token: {address[:12]}...")
            return

        await self._vet(address, chain)

    def _recently_vetted(self, address: str) -> bool:
        try:
            with self.get_db() as db:
                from sqlalchemy import text
                from datetime import timedelta
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                token = db.query(Token).filter_by(address=address).first()
                if not token:
                    return False
                recent = (
                    db.query(TokenVetting)
                    .filter(
                        TokenVetting.token_id == token.id,
                        TokenVetting.checked_at >= cutoff,
                    )
                    .first()
                )
                return recent is not None
        except Exception:
            return False

    async def _vet(self, address: str, chain: str) -> None:
        loop = asyncio.get_running_loop()
        min_liq = self.config.get("risk", {}).get("min_liquidity_usd", 50000)
        fail_reasons = []

        # ── Step 1: DexScreener ──────────────────────────────────────────────
        await rate_limit("api.dexscreener.com")
        pair = await loop.run_in_executor(None, get_token, address)
        if not pair:
            self.logger.info(f"REJECT [{address[:12]}] — not found on DexScreener")
            self._save(address, chain, False, ["not_found_on_dexscreener"])
            return

        info = extract_token_info(pair)
        liquidity = info["liquidity_usd"]

        # ── Step 2: Liquidity ────────────────────────────────────────────────
        if liquidity < min_liq:
            fail_reasons.append(f"low_liquidity:${liquidity:,.0f}")

        # ── Step 3: Contract age ─────────────────────────────────────────────
        age_hours = None
        created_ms = info.get("pair_created_at_ms")
        if created_ms:
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            age_hours = (now_ms - created_ms) / 3_600_000
            if age_hours < 1:
                fail_reasons.append(f"too_new:{age_hours:.1f}h")

        # ── Step 4: GoPlus contract safety ───────────────────────────────────
        is_honeypot = False
        await rate_limit("api.gopluslabs.io")
        safety = await loop.run_in_executor(None, lambda: goplus_check(address, chain))
        if safety:
            if safety.get("is_honeypot"):
                fail_reasons.append("honeypot:goplus")
                is_honeypot = True
            if safety.get("sell_tax", 0) > 20:
                fail_reasons.append(f"sell_tax:{safety['sell_tax']:.0f}%")
            if safety.get("buy_tax", 0) > 20:
                fail_reasons.append(f"buy_tax:{safety['buy_tax']:.0f}%")
            if safety.get("is_mintable"):
                fail_reasons.append("mintable")
            if safety.get("owner_can_change_balance"):
                fail_reasons.append("owner_can_change_balance")

        # ── Step 5: Honeypot.is (BNB only) ──────────────────────────────────
        if chain in ("bnb", "bsc") and not is_honeypot:
            await rate_limit("api.honeypot.is")
            hp = await loop.run_in_executor(None, check_bsc, address)
            if hp and hp.get("is_honeypot"):
                fail_reasons.append("honeypot:honeypot_is")
                is_honeypot = True

        passed = len(fail_reasons) == 0
        self._save(address, chain, passed, fail_reasons, liquidity, age_hours, is_honeypot, info)

        sym = info.get("symbol", address[:8])
        status = "PASS" if passed else "FAIL"
        self.logger.info(f"Vetting {status} [{sym}] liq=${liquidity:,.0f} | {fail_reasons or 'all clear'}")

        await self.publish("token_vetted", {
            "address": address, "chain": chain, "passed": passed,
            "fail_reasons": fail_reasons, "liquidity_usd": liquidity, "symbol": sym,
        })

    def _save(self, address, chain, passed, fail_reasons,
              liquidity=0, age_hours=None, is_honeypot=False, info=None):
        try:
            with self.get_db() as db:
                token = db.query(Token).filter_by(address=address).first()
                if not token:
                    token = Token(
                        address=address, chain=chain,
                        symbol=(info or {}).get("symbol"),
                        name=(info or {}).get("name"),
                    )
                    db.add(token)
                    db.flush()

                db.add(TokenVetting(
                    token_id=token.id,
                    contract_safe=not is_honeypot,
                    is_honeypot=is_honeypot,
                    liquidity_usd=liquidity,
                    contract_age_hours=age_hours,
                    overall_pass=passed,
                    fail_reasons=json.dumps(fail_reasons),
                ))

                if passed:
                    db.add(Watchlist(
                        token_id=token.id,
                        added_by="vetting_agent",
                        status="watching",
                    ))
        except Exception as e:
            self.logger.error(f"DB save failed for {address}: {e}")
