"""
retry.py

Shared async exponential backoff retry utility.

Role & Responsibility:
    Provides a single, reusable implementation of async exponential
    backoff with optional jitter. Both the Dirigera WebSocket client
    and the MQTT connection logic import from here — there is no
    duplicated retry logic anywhere in the application.

    Keeping retry logic centralized means:
        - Backoff behavior is consistent across all reconnect paths
        - Tuning (initial delay, max delay, jitter, max attempts) is done
        in one place
        - The retry loop is independently testable without real connections

What it does:
    - Defines RetryConfig dataclass: all parameters controlling the
    backoff behavior, validated at construction time
    - Provides retry_with_backoff() async context manager that wraps
    a coroutine factory and retries it on failure, yielding
    successive delay values to the caller
    - Provides calculate_delay() as a pure function for testing and
    for cases where the caller needs to control the sleep itself
    - Jitter is additive uniform random noise (0...jitter_max seconds)
    to prevent thundering-herd reconnects when multiple services
    restart simultaneously

Arguments / Configuration:
    RetryConfig fields (all validated at construction):
        initial_delay (float):        First retry delay in seconds. Default 1.0
        max_delay (float):        Maximum delay cap in seconds. Default 60.0
        multiplier (float):        Backoff multiplier per attempt. Default 2.0
        jitter_max (float):        Max random jitter added to each delay.
                        Default 1.0. Set to 0.0 to disable.
        max_attempts (int|None):    Maximum number of attempts before
                        giving up. None means retry forever.

Used by:
    - app/dirigera/websocket_client.py  (WebSocket reconnect loop)
    - app/ha/ha_client.py               (MQTT reconnect loop)
    - app/orchestrator.py               (startup retry)

Not responsible for:
    - Deciding what constitutes a retryable vs fatal error (that is
    the caller's responsibility — callers raise to trigger retry or
    return normally to signal success)
    - Logging the retry reason (callers log before re-raising)
    - Any network I/O
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "RetryConfig",
    "RetryExhaustedError",
    "calculate_delay",
    "retry_with_backoff",
]

logger = logging.getLogger(__name__)


# ── Retry config ──────────────────────────────────────────────────────────────


@dataclass
class RetryConfig:
    """
    Configuration for exponential backoff retry behavior.

    Args:
        initial_delay (float):        Delay before the first retry in seconds.
                        Must be > 0. Default: 1.0
        max_delay (float):        Upper cap on delay in seconds.
                        Must be >= initial_delay. Default: 60.0
        multiplier (float):        Factor applied to delay after each failed
                        attempt. Must be >= 1.0. Default: 2.0
        jitter_max (float):        Maximum random seconds added to each
                        delay to spread reconnect attempts.
                        Must be >= 0. Default: 1.0
        max_attempts (int|None):    Maximum number of retry attempts.
                        None = retry indefinitely. Default: None

    Raises:
        DirigeraBridgeError: If any value fails validation.

    Delay sequence example (initial=1, multiplier=2, max=60, jitter=0):
        attempt 1: 1s
        attempt 2: 2s
        attempt 3: 4s
        attempt 4: 8s
        attempt 5: 16s
        attempt 6: 32s
        attempt 7: 60s  ← capped
        attempt 8: 60s  ← stays capped
    """

    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter_max: float = 1.0
    max_attempts: Optional[int] = None

    def __post_init__(self) -> None:
        """Validate all fields immediately after construction."""
        self._validate()

    # ── Internal ─────────────────────────────────────────────────────────

    def _validate(self) -> None:
        """
        Validate all RetryConfig fields.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT on any
            invalid field value.
        """

        if not isinstance(self.initial_delay, (int, float)) or self.initial_delay <= 0:
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"RetryConfig.initial_delay must be > 0, got {self.initial_delay!r}",
            )

        if (
            not isinstance(self.max_delay, (int, float))
            or self.max_delay < self.initial_delay
        ):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"RetryConfig.max_delay must be >= initial_delay "
                f"({self.initial_delay}), got {self.max_delay!r}",
            )

        if not isinstance(self.multiplier, (int, float)) or self.multiplier < 1.0:
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"RetryConfig.multiplier must be >= 1.0, got {self.multiplier!r}",
            )

        if not isinstance(self.jitter_max, (int, float)) or self.jitter_max < 0:
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"RetryConfig.jitter_max must be >= 0, got {self.jitter_max!r}",
            )

        if self.max_attempts is not None:
            if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
                raise DirigeraBridgeError(
                    ErrorCode.INTERNAL_INVALID_ARGUMENT,
                    f"RetryConfig.max_attempts must be a positive int "
                    f"or None, got {self.max_attempts!r}",
                )


# ── Retry exhausted error ─────────────────────────────────────────────────────


class RetryExhaustedError(Exception):
    """
    Raised by retry_with_backoff() when max_attempts is set and all
    attempts have been exhausted without success.

    Args:
        attempts (int):        Number of attempts made.
        last_error (Exception):    The exception from the final attempt.
        config (RetryConfig):    The config used for the retry loop.
    """

    def __init__(
        self,
        attempts: int,
        last_error: Exception,
        config: RetryConfig,
    ) -> None:
        self.attempts = attempts
        self.last_error = last_error
        self.config = config
        super().__init__(f"Retry exhausted after {attempts} attempt(s): {last_error}")


# ── Pure delay calculator ─────────────────────────────────────────────────────


def calculate_delay(
    attempt: int,
    config: RetryConfig,
    *,
    _random: Optional[random.Random] = None,
) -> float:
    """
    Calculate the delay for a given attempt number using exponential
    backoff with optional jitter.

    This is a pure function — it does not sleep. Use it directly when
    you need the delay value for logging, or use retry_with_backoff()
    for the full retry loop.

    Args:
        attempt (int):            Attempt number, starting at 1 for the
                        first retry delay. Must be >= 1.
        config (RetryConfig):        Backoff configuration.
        _random (random.Random):    Optional seeded RNG for deterministic
                        testing. Uses module-level random if
                        not provided.

    Returns:
        float:    Delay in seconds, capped at config.max_delay, with
            jitter applied if config.jitter_max > 0.

    Raises:
        DirigeraBridgeError: If attempt < 1 or config is not a
        RetryConfig.
    """

    # ── Validation ────────────────────────────────────────────────────────
    if not isinstance(attempt, int) or attempt < 1:
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"calculate_delay: attempt must be int >= 1, got {attempt!r}",
        )

    if not isinstance(config, RetryConfig):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"calculate_delay: config must be RetryConfig, got {type(config).__name__}",
        )

    # ── Base delay: initial * multiplier^(attempt-1), capped at max ───────
    base = config.initial_delay * (config.multiplier ** (attempt - 1))
    capped = min(base, config.max_delay)

    # ── Jitter: uniform random in [0, jitter_max] ─────────────────────────
    jitter = 0.0
    if config.jitter_max > 0:
        rng = _random or random
        jitter = rng.uniform(0, config.jitter_max)

    return capped + jitter


# ── Async retry loop ──────────────────────────────────────────────────────────


async def retry_with_backoff(
    config: RetryConfig,
    *,
    stop_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[int]:
    """
    Async generator that yields attempt numbers and sleeps between
    failed attempts using exponential backoff.

    Usage pattern — the caller wraps their connection/operation logic
    and breaks out of the loop on success:

    async for attempt in retry_with_backoff(config, stop_event=stop):
        try:
            await connect()
            break          # ← success: exit the retry loop
        except SomeError as e:
            logger.warning('Attempt %d failed: %s', attempt, e)
            # do NOT break — let the generator sleep and retry

    The generator:
        1. Yields the current attempt number (starting at 1)
        2. Waits for the caller's body to execute
        3. If the caller broke out of the loop → done (success)
        4. If max_attempts is set and exhausted → raises
        RetryExhaustedError
        5. Otherwise → sleeps for calculate_delay(attempt, config)
        seconds, then yields the next attempt number

    The stop_event parameter allows external shutdown to cancel the
    retry loop cleanly without waiting for the next sleep to expire.

    Args:
        config (RetryConfig):
            Backoff configuration controlling delays and limits.

        stop_event (asyncio.Event | None):
            Optional event that, when set, causes the generator to
            stop retrying and return (not raise). Use this to integrate
            with service shutdown — set the event from the orchestrator
            when a graceful stop is requested.

    Yields:
        int: Current attempt number, starting at 1.

    Raises:
        RetryExhaustedError:    When max_attempts is set and all attempts
                        have been used up.
        Any non-retryable exception propagates immediately.

    Raises (validation):
        DirigeraBridgeError: If config is not a RetryConfig.
    """

    # ── Validation ────────────────────────────────────────────────────────
    if not isinstance(config, RetryConfig):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"retry_with_backoff: config must be RetryConfig, "
            f"got {type(config).__name__}",
        )

    if stop_event is not None and not isinstance(stop_event, asyncio.Event):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            "retry_with_backoff: stop_event must be asyncio.Event or None",
        )

    attempt = 0
    last_error: Optional[Exception] = None

    # ── Retry loop ────────────────────────────────────────────────────────
    while True:
        # Check stop signal before each attempt
        if stop_event is not None and stop_event.is_set():
            logger.debug(
                "Retry loop stopped by stop_event after %d attempt(s)",
                attempt,
            )
            return

        attempt += 1

        # ── Yield attempt number to caller ────────────────────────────────
        yield attempt

        # ── If we reach here the caller did not break → attempt failed ────
        # (If the caller broke, the generator stops, and we never reach here)

        # Check max_attempts
        if config.max_attempts is not None and attempt >= config.max_attempts:
            raise RetryExhaustedError(
                attempts=attempt,
                last_error=last_error or Exception("unknown"),
                config=config,
            )

        # Check stop signal again before sleeping
        if stop_event is not None and stop_event.is_set():
            logger.debug(
                "Retry loop stopped by stop_event (pre-sleep) after %d attempt(s)",
                attempt,
            )
            return

        # ── Calculate and sleep for backoff delay ─────────────────────────
        delay = calculate_delay(attempt, config)

        logger.debug(
            "Retry attempt %d failed — sleeping %.2fs before attempt %d",
            attempt,
            delay,
            attempt + 1,
        )

        # Use wait_for with the stop_event so shutdown does not have
        # to wait for a full 60-second sleep to expire
        if stop_event is not None:
            try:
                await asyncio.wait_for(
                    _wait_for_event(stop_event),
                    timeout=delay,
                )
                # stop_event was set during sleep — exit cleanly
                logger.debug(
                    "Retry sleep interrupted by stop_event after %d attempt(s)",
                    attempt,
                )
                return
            except asyncio.TimeoutError:
                pass  # Normal case: sleep expired, continue to next attempt
        else:
            await asyncio.sleep(delay)


async def _wait_for_event(event: asyncio.Event) -> None:
    """
    Internal coroutine: waits until the given asyncio.Event is set.

    Used by retry_with_backoff() to make the backoff sleep interruptible
    by a stop signal without busy-waiting.
    """
    await event.wait()
