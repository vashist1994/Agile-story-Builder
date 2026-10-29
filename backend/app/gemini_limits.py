import asyncio
import math
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


class LocalQuotaExceededError(RuntimeError):
    pass


@dataclass
class _Usage:
    timestamp: float
    tokens: int


class GeminiRateLimiter:
    """Process-local sliding-window quotas and concurrency control."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        requests_per_day: int,
        max_concurrency: int,
        max_wait_seconds: float,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_day = requests_per_day
        self.max_wait_seconds = max_wait_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._lock = asyncio.Lock()
        self._usage: deque[_Usage] = deque()

    @asynccontextmanager
    async def request_slot(self, estimated_tokens: int) -> AsyncIterator[_Usage]:
        if self.tokens_per_minute and estimated_tokens > self.tokens_per_minute:
            raise LocalQuotaExceededError(
                "The estimated request exceeds the configured Gemini tokens-per-minute limit."
            )

        deadline = time.monotonic() + self.max_wait_seconds
        while True:
            await self._semaphore.acquire()
            try:
                reservation, delay = await self._reserve(estimated_tokens)
            except BaseException:
                self._semaphore.release()
                raise
            if reservation is not None:
                try:
                    yield reservation
                finally:
                    self._semaphore.release()
                return
            else:
                self._semaphore.release()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalQuotaExceededError(
                    "The configured Gemini request quota is currently exhausted. "
                    "Please retry later."
                )
            await asyncio.sleep(min(max(delay, 0.01), remaining))

    async def _reserve(self, estimated_tokens: int) -> tuple[_Usage | None, float]:
        now = time.monotonic()
        async with self._lock:
            while self._usage and now - self._usage[0].timestamp >= 86_400:
                self._usage.popleft()

            minute_usage = [entry for entry in self._usage if now - entry.timestamp < 60]
            request_limited = (
                self.requests_per_minute and len(minute_usage) >= self.requests_per_minute
            )
            day_limited = self.requests_per_day and len(self._usage) >= self.requests_per_day
            token_total = sum(entry.tokens for entry in minute_usage)
            token_limited = (
                self.tokens_per_minute and token_total + estimated_tokens > self.tokens_per_minute
            )
            if not (request_limited or day_limited or token_limited):
                usage = _Usage(timestamp=now, tokens=estimated_tokens)
                self._usage.append(usage)
                return usage, 0

            delays: list[float] = []
            if request_limited:
                delays.append(max(0.01, 60 - (now - minute_usage[0].timestamp)))
            if token_limited:
                tokens_to_free = token_total + estimated_tokens - self.tokens_per_minute
                released = 0
                for entry in minute_usage:
                    released += entry.tokens
                    if released >= tokens_to_free:
                        delays.append(max(0.01, 60 - (now - entry.timestamp)))
                        break
            if day_limited:
                delays.append(max(0.01, 86_400 - (now - self._usage[0].timestamp)))
            return None, max(delays, default=1.0)

    async def record_actual_tokens(self, usage: _Usage, actual_tokens: int) -> None:
        async with self._lock:
            usage.tokens = max(0, actual_tokens)


def estimate_tokens(text: str, output_token_reserve: int) -> int:
    # A conservative character-based estimate avoids an extra provider token-count call.
    return math.ceil(len(text) / 3) + output_token_reserve
