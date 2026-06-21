"""
ResearchAgent — Multi-Source Token Discovery
=============================================
Sources (in decreasing priority/speed):
  1. GMGN trending    — smart money already in; highest conviction
  2. Birdeye new      — fastest new listing indexer for Solana
  3. Birdeye trending — broader trending with smart money weighting
  4. DexScreener new  — high volume, good BNB coverage
  5. DexScreener trending — fallback, always available

All sources feed the same dedup cache → VettingAgent pipeline.
Tokens that appear in 2+ sources get elevated priority (published twice
equals a stronger signal to the vetting queue, which logs it once due to
24h dedup — but the metadata records the multi-source hit).
"""
import asyncio
import time

from agents.base_agent import BaseAgent
from core.rate_limiter import acquire as rate_limit
from data.dexscreener import get_new_tokens, get_trending_tokens
from data.gmgn import get_trending_tokens as gmgn_trending, get_new_pairs as gmgn_new
from data.birdeye import get_trending_tokens as birdeye_trending, get_new_listings as birdeye_new

_SEEN_TTL = 3600   # don't resubmit the same address within 1 hour
_MULTI_SOURCE_BONUS = "multi_source"   # tag when token found in 2+ sources


class ResearchAgent(BaseAgent):

    def __init__(self, name, message_bus, session_factory, config):
        super().__init__(name, message_bus, session_factory, config)
        self._seen: dict[str, float] = {}           # address → monotonic time
        self._source_hits: dict[str, set] = {}      # address → set of sources (within TTL)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        chains = self.config.get("chains", ["solana", "bnb"])

        # ── GMGN (Solana only) ────────────────────────────────────────────────
        await rate_limit("gmgn.ai")
        gmgn_trend = await loop.run_in_executor(None, lambda: gmgn_trending("sol", 20))
        await self._submit(gmgn_trend, "solana", "gmgn_trending", addr_key="address")

        await rate_limit("gmgn.ai")
        gmgn_pairs = await loop.run_in_executor(None, lambda: gmgn_new("sol", 30))
        await self._submit(gmgn_pairs, "solana", "gmgn_new", addr_key="address")

        # ── Birdeye (Solana, API key required) ───────────────────────────────
        await rate_limit("public-api.birdeye.so")
        be_trend = await loop.run_in_executor(None, lambda: birdeye_trending("solana", 20))
        await self._submit(be_trend, "solana", "birdeye_trending", addr_key="address")

        await rate_limit("public-api.birdeye.so")
        be_new = await loop.run_in_executor(None, lambda: birdeye_new("solana", 30))
        await self._submit(be_new, "solana", "birdeye_new", addr_key="address")

        # ── DexScreener (all configured chains) ──────────────────────────────
        for chain in chains:
            await rate_limit("api.dexscreener.com")
            new_tokens = await loop.run_in_executor(None, get_new_tokens, chain)
            await self._submit(new_tokens, chain, "dex_new", addr_key="tokenAddress")
            await asyncio.sleep(1)

            await rate_limit("api.dexscreener.com")
            trending = await loop.run_in_executor(None, get_trending_tokens, chain)
            await self._submit(trending, chain, "dex_trending", addr_key="tokenAddress")

        self._evict_stale()

    async def _submit(
        self, tokens: list, chain: str, source: str, addr_key: str = "address"
    ) -> None:
        if not tokens:
            return
        submitted = 0
        for token in tokens[:15]:
            address = token.get(addr_key, "").strip()
            if not address:
                continue

            # Track which sources have seen this address
            self._source_hits.setdefault(address, set()).add(source)
            multi = len(self._source_hits[address]) >= 2

            if self._is_seen(address):
                continue   # already submitted this cycle

            self._mark_seen(address)
            await self.publish(
                "vet_token",
                {
                    "address": address,
                    "chain": chain,
                    "source": source,
                    "multi_source": multi,
                    "symbol": token.get("symbol", ""),
                },
                to="vetting",
            )
            submitted += 1
            if multi:
                self.logger.info(
                    f"Multi-source token [{token.get('symbol', address[:8])}] "
                    f"({', '.join(self._source_hits[address])}) — elevated priority"
                )

        if submitted:
            self.logger.info(
                f"Submitted {submitted} token(s) from {source} [{chain}] for vetting"
            )

    def _is_seen(self, address: str) -> bool:
        ts = self._seen.get(address)
        return ts is not None and (time.monotonic() - ts) < _SEEN_TTL

    def _mark_seen(self, address: str) -> None:
        self._seen[address] = time.monotonic()

    def _evict_stale(self) -> None:
        now = time.monotonic()
        self._seen = {a: t for a, t in self._seen.items() if now - t < _SEEN_TTL}
        self._source_hits = {
            a: s for a, s in self._source_hits.items()
            if a in self._seen
        }

    async def process_message(self, message: dict) -> None:
        pass
