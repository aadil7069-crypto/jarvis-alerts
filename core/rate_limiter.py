import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger("jarvis.rate_limiter")

# Max calls allowed per window (seconds) for each API domain
_LIMITS: dict[str, tuple[int, int]] = {
    "api.dexscreener.com":        (55, 60),   # docs say 60/min — leave a small safety margin
    "gamma-api.polymarket.com":   (20, 60),
    "clob.polymarket.com":        (20, 60),
    "api.gopluslabs.io":          (10, 60),
    "api.honeypot.is":            (10, 60),
    "api.helius.xyz":             (50, 60),
    "mainnet.helius-rpc.com":     (50, 60),
    "api.bscscan.com":            (5,  60),
    "api.coingecko.com":          (15, 60),
    "api.anthropic.com":          (10, 60),
    "gmgn.ai":                    (20, 60),   # undocumented public API — keep conservative
    "public-api.birdeye.so":      (30, 60),   # free tier: 100 req/min documented
    "api.arkhamintelligence.com": (10, 60),   # conservative — free tier limits unknown
    "quote-api.jup.ag":           (20, 60),   # Jupiter v6 quote API (public, no key)
    "price.jup.ag":               (20, 60),   # Jupiter price API
    "bsc-dataseed.binance.org":   (20, 60),   # Public BSC RPC
}

# Slots reserved exclusively for priority=True callers (e.g. stop-loss monitoring),
# so bulk scanning agents can never starve out time-critical exit checks.
_RESERVED: dict[str, int] = {
    "api.dexscreener.com": 20,
}


class RateLimiter:
    def __init__(self):
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def acquire(self, domain: str, priority: bool = False) -> None:
        """Wait until a call to `domain` is within the configured rate limit.

        Non-priority callers stop short of the full budget (max_calls - reserved),
        guaranteeing headroom for priority callers even when scanning agents are
        hammering the same domain.
        """
        if domain not in _LIMITS:
            return

        max_calls, window = _LIMITS[domain]
        reserved = 0 if priority else _RESERVED.get(domain, 0)
        cap = max_calls - reserved

        while True:
            async with self._lock:
                now = time.monotonic()
                self._calls[domain] = [t for t in self._calls[domain] if now - t < window]

                if len(self._calls[domain]) < cap:
                    self._calls[domain].append(now)
                    return

                wait = window - (now - self._calls[domain][0]) + 0.05

            # Sleep outside the lock — otherwise one domain backing up stalls
            # acquire() calls for every other domain sharing this limiter.
            logger.debug(f"Rate limit hit for {domain} — waiting {wait:.1f}s")
            await asyncio.sleep(wait)


# Global singleton — import and call `acquire(domain)` before every external API call
_limiter = RateLimiter()


async def acquire(domain: str, priority: bool = False) -> None:
    await _limiter.acquire(domain, priority=priority)
