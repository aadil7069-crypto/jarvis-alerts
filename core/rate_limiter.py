import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger("jarvis.rate_limiter")

# Max calls allowed per window (seconds) for each API domain
_LIMITS: dict[str, tuple[int, int]] = {
    "api.dexscreener.com":        (30, 60),
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
}


class RateLimiter:
    def __init__(self):
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def acquire(self, domain: str) -> None:
        """Wait until a call to `domain` is within the configured rate limit."""
        if domain not in _LIMITS:
            return

        max_calls, window = _LIMITS[domain]

        async with self._lock:
            while True:
                now = time.monotonic()
                self._calls[domain] = [t for t in self._calls[domain] if now - t < window]

                if len(self._calls[domain]) < max_calls:
                    self._calls[domain].append(now)
                    return

                wait = window - (now - self._calls[domain][0]) + 0.05
                logger.debug(f"Rate limit hit for {domain} — waiting {wait:.1f}s")
                await asyncio.sleep(wait)


# Global singleton — import and call `acquire(domain)` before every external API call
_limiter = RateLimiter()


async def acquire(domain: str) -> None:
    await _limiter.acquire(domain)
