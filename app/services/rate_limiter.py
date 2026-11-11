"""
Token Bucket Rate Limiter for LLM API calls.

Designed for Oracle Cloud Always Free VM (1 GB RAM) constraints.
Prevents 429 Too Many Requests errors on Groq / NVIDIA NIM free tiers
by throttling requests to stay within configured RPM limits.
"""

import asyncio
import time
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("Veyronix.rate_limiter")


class TokenBucketRateLimiter:
    """
    Asyncio-compatible token bucket rate limiter.

    Tokens are added at a rate of (RPM / 60) per second.
    Each LLM call consumes one token via `acquire()`.
    If the bucket is empty, `acquire()` waits until a token is available.
    """

    def __init__(self, rpm: int = 10, max_concurrent: int = 3):
        self.rpm = rpm
        self.max_concurrent = max_concurrent
        self.tokens = float(rpm)
        self.last_fill = time.monotonic()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._fill_rate = rpm / 60.0  # tokens per second

    def _fill_tokens(self) -> None:
        """Add tokens based on elapsed time since last fill."""
        now = time.monotonic()
        elapsed = now - self.last_fill
        self.tokens = min(self.rpm, self.tokens + elapsed * self._fill_rate)
        self.last_fill = now

    async def acquire(self) -> None:
        """
        Acquire a token before making an LLM call.

        Blocks (with async sleep) until a token is available,
        ensuring we never exceed the configured RPM.
        """
        async with self._lock:
            self._fill_tokens()
            while self.tokens < 1.0:
                needed = 1.0 - self.tokens
                wait_time = needed / self._fill_rate
                await asyncio.sleep(wait_time)
                self._fill_tokens()
            self.tokens -= 1.0

    async def call_llm(
        self,
        messages: Any,
        model: str,
        task_type: str = "general",
        llm_client: Any = None,
        max_retries: int = 4,
    ) -> Any:
        """
        Wrap an LLM call with rate limiting and exponential backoff on 429.

        Args:
            messages: The message payload to send to the LLM.
            model: The model name to use.
            task_type: Label for logging (e.g., "action", "report").
            llm_client: The LLM client instance with an `acreate` or `create` method.
            max_retries: Maximum number of retries on 429 errors.

        Returns:
            The LLM response object.

        Raises:
            Exception: If all retries are exhausted.
        """
        await self.acquire()

        async with self._semaphore:
            backoff = 1.0
            for attempt in range(max_retries):
                try:
                    # Support both async (acreate) and sync (create) clients
                    if llm_client is None:
                        raise ValueError("llm_client is required")

                    if hasattr(llm_client, "acreate"):
                        response = await llm_client.acreate(
                            model=model,
                            messages=messages,
                        )
                    elif hasattr(llm_client, "create"):
                        response = llm_client.create(
                            model=model,
                            messages=messages,
                        )
                    else:
                        # Fallback: assume the client itself is callable
                        response = await llm_client(
                            model=model,
                            messages=messages,
                        )

                    logger.debug(
                        "LLM call succeeded | task=%s model=%s attempt=%d",
                        task_type,
                        model,
                        attempt + 1,
                    )
                    return response

                except Exception as exc:
                    is_rate_limit = False
                    exc_str = str(exc).lower()
                    if "429" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
                        is_rate_limit = True

                    if is_rate_limit and attempt < max_retries - 1:
                        logger.warning(
                            "LLM 429 rate limit hit | task=%s model=%s attempt=%d backing off %.1fs",
                            task_type,
                            model,
                            attempt + 1,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        # Re-acquire token after backoff
                        await self.acquire()
                        continue

                    logger.error(
                        "LLM call failed | task=%s model=%s attempt=%d error=%s",
                        task_type,
                        model,
                        attempt + 1,
                        exc,
                    )
                    raise

            raise RuntimeError(
                f"LLM call exhausted {max_retries} retries for task={task_type} model={model}"
            )

    @property
    def current_rpm(self) -> float:
        """Return the current configured RPM."""
        return self.rpm

    @property
    def available_tokens(self) -> float:
        """Return approximate available tokens (for monitoring)."""
        self._fill_tokens()
        return self.tokens


__all__ = ["TokenBucketRateLimiter"]
