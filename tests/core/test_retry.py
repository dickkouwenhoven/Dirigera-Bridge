"""
tests/core/test_retry.py

Tests for app/core/retry.py

Covers:
    - RetryConfig construction and validation
    - calculate_delay() — delay sequence, capping, jitter
    - retry_with_backoff() — success on first attempt
    - retry_with_backoff() — retry then succeed
    - retry_with_backoff() — RetryExhaustedError when max_attempts hit
    - retry_with_backoff() — stop_event interrupts the loop cleanly
    - retry_with_backoff() — stop_event interrupts backoff sleep
    - retry_with_backoff() — validation errors
"""

import asyncio
import random

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.retry import (
    RetryConfig,
    RetryExhaustedError,
    calculate_delay,
    retry_with_backoff,
)


# ── RetryConfig ───────────────────────────────────────────────────────────────


class TestRetryConfig:
    @pytest.mark.unit
    def test_default_values(self):
        """RetryConfig default values are sensible."""
        cfg = RetryConfig()
        assert cfg.initial_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.multiplier == 2.0
        assert cfg.jitter_max == 1.0
        assert cfg.max_attempts is None

    @pytest.mark.unit
    def test_custom_values(self):
        """RetryConfig accepts custom values."""
        cfg = RetryConfig(
            initial_delay=0.5,
            max_delay=30.0,
            multiplier=1.5,
            jitter_max=0.0,
            max_attempts=5,
        )
        assert cfg.initial_delay == 0.5
        assert cfg.max_delay == 30.0
        assert cfg.multiplier == 1.5
        assert cfg.jitter_max == 0.0
        assert cfg.max_attempts == 5

    @pytest.mark.unit
    def test_zero_initial_delay_raises(self):
        """initial_delay must be > 0."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(initial_delay=0)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_negative_initial_delay_raises(self):
        """Negative initial_delay raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(initial_delay=-1.0)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_max_delay_less_than_initial_raises(self):
        """max_delay must be >= initial_delay."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(initial_delay=10.0, max_delay=5.0)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_max_delay_equal_to_initial_is_valid(self):
        """max_delay == initial_delay is valid (no backoff, fixed delay)."""
        cfg = RetryConfig(initial_delay=5.0, max_delay=5.0)
        assert cfg.initial_delay == cfg.max_delay

    @pytest.mark.unit
    def test_multiplier_less_than_one_raises(self):
        """multiplier must be >= 1.0."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(multiplier=0.5)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_multiplier_of_one_is_valid(self):
        """multiplier=1.0 is valid (linear, no growth)."""
        cfg = RetryConfig(multiplier=1.0)
        assert cfg.multiplier == 1.0

    @pytest.mark.unit
    def test_negative_jitter_raises(self):
        """jitter_max must be >= 0."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(jitter_max=-0.1)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_zero_jitter_is_valid(self):
        """jitter_max=0.0 disables jitter."""
        cfg = RetryConfig(jitter_max=0.0)
        assert cfg.jitter_max == 0.0

    @pytest.mark.unit
    def test_zero_max_attempts_raises(self):
        """max_attempts must be >= 1 or None."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(max_attempts=0)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_negative_max_attempts_raises(self):
        """Negative max_attempts raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            RetryConfig(max_attempts=-1)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_none_max_attempts_is_valid(self):
        """max_attempts=None means retry indefinitely."""
        cfg = RetryConfig(max_attempts=None)
        assert cfg.max_attempts is None

    @pytest.mark.unit
    def test_max_attempts_one_is_valid(self):
        """max_attempts=1 means try once then exhaust."""
        cfg = RetryConfig(max_attempts=1)
        assert cfg.max_attempts == 1


# ── calculate_delay() ─────────────────────────────────────────────────────────


class TestCalculateDelay:
    @pytest.fixture
    def no_jitter_cfg(self):
        return RetryConfig(
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter_max=0.0,
        )

    @pytest.mark.unit
    def test_first_attempt_is_initial_delay(self, no_jitter_cfg):
        """Attempt 1 delay equals initial_delay."""
        assert calculate_delay(1, no_jitter_cfg) == 1.0

    @pytest.mark.unit
    def test_delay_doubles_each_attempt(self, no_jitter_cfg):
        """Delay doubles with multiplier=2 on each attempt."""
        assert calculate_delay(1, no_jitter_cfg) == 1.0
        assert calculate_delay(2, no_jitter_cfg) == 2.0
        assert calculate_delay(3, no_jitter_cfg) == 4.0
        assert calculate_delay(4, no_jitter_cfg) == 8.0
        assert calculate_delay(5, no_jitter_cfg) == 16.0

    @pytest.mark.unit
    def test_delay_capped_at_max(self, no_jitter_cfg):
        """Delay is capped at max_delay."""
        assert calculate_delay(7, no_jitter_cfg) == 60.0
        assert calculate_delay(10, no_jitter_cfg) == 60.0
        assert calculate_delay(100, no_jitter_cfg) == 60.0

    @pytest.mark.unit
    def test_delay_with_jitter_is_within_range(self):
        """Delay with jitter is between base and base + jitter_max."""
        cfg = RetryConfig(
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter_max=2.0,
        )
        rng = random.Random(42)
        for attempt in range(1, 6):
            delay = calculate_delay(attempt, cfg, _random=rng)
            base = min(1.0 * (2.0 ** (attempt - 1)), 60.0)
            assert delay >= base
            assert delay <= base + 2.0

    @pytest.mark.unit
    def test_zero_jitter_gives_exact_delay(self):
        """jitter_max=0.0 gives exact delay with no randomness."""
        cfg = RetryConfig(
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter_max=0.0,
        )
        assert calculate_delay(1, cfg) == 1.0
        assert calculate_delay(2, cfg) == 2.0

    @pytest.mark.unit
    def test_multiplier_one_gives_constant_delay(self):
        """multiplier=1.0 gives the same delay on every attempt."""
        cfg = RetryConfig(
            initial_delay=5.0,
            max_delay=60.0,
            multiplier=1.0,
            jitter_max=0.0,
        )
        for attempt in range(1, 10):
            assert calculate_delay(attempt, cfg) == 5.0

    @pytest.mark.unit
    def test_attempt_zero_raises(self):
        """attempt must be >= 1."""
        cfg = RetryConfig(jitter_max=0.0)
        with pytest.raises(DirigeraBridgeError) as exc_info:
            calculate_delay(0, cfg)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_negative_attempt_raises(self):
        """Negative attempt raises."""
        cfg = RetryConfig(jitter_max=0.0)
        with pytest.raises(DirigeraBridgeError) as exc_info:
            calculate_delay(-1, cfg)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_config_raises(self):
        """Non-RetryConfig config raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            calculate_delay(1, "not_a_config")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_real_world_sequence(self):
        """Verify the delay sequence matches the documented example."""
        cfg = RetryConfig(
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter_max=0.0,
        )
        expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
        for i, exp in enumerate(expected, start=1):
            assert calculate_delay(i, cfg) == exp, (
                f"Attempt {i}: expected {exp}, got {calculate_delay(i, cfg)}"
            )


# ── retry_with_backoff() ──────────────────────────────────────────────────────


class TestRetryWithBackoff:
    @pytest.fixture
    def fast_cfg(self):
        """RetryConfig with tiny delays for fast tests."""
        return RetryConfig(
            initial_delay=0.01,
            max_delay=0.05,
            multiplier=2.0,
            jitter_max=0.0,
        )

    @pytest.mark.unit
    async def test_success_on_first_attempt(self, fast_cfg):
        """Breaking on first yield means only one attempt."""
        attempts_seen = []
        async for attempt in retry_with_backoff(fast_cfg):
            attempts_seen.append(attempt)
            break  # success

        assert attempts_seen == [1]

    @pytest.mark.unit
    async def test_retry_then_succeed(self, fast_cfg):
        """Retries until success — attempts increment correctly."""
        counter = [0]
        attempts_seen = []

        async for attempt in retry_with_backoff(fast_cfg):
            attempts_seen.append(attempt)
            counter[0] += 1
            if counter[0] >= 3:
                break  # succeed on attempt 3

        assert attempts_seen == [1, 2, 3]

    @pytest.mark.unit
    async def test_exhausted_raises_retry_exhausted_error(self, fast_cfg):
        """RetryExhaustedError is raised when max_attempts is exhausted."""
        cfg = RetryConfig(
            initial_delay=0.01,
            max_delay=0.05,
            multiplier=2.0,
            jitter_max=0.0,
            max_attempts=3,
        )

        with pytest.raises(RetryExhaustedError) as exc_info:
            async for attempt in retry_with_backoff(cfg):
                pass  # never break — always "fail"

        assert exc_info.value.attempts == 3
        assert exc_info.value.config is cfg

    @pytest.mark.unit
    async def test_exhausted_error_has_attempt_count(self):
        """RetryExhaustedError.attempts reflects the configured max."""
        cfg = RetryConfig(
            initial_delay=0.01,
            max_delay=0.05,
            multiplier=1.0,
            jitter_max=0.0,
            max_attempts=2,
        )
        with pytest.raises(RetryExhaustedError) as exc_info:
            async for _ in retry_with_backoff(cfg):
                pass
        assert exc_info.value.attempts == 2

    @pytest.mark.unit
    async def test_stop_event_cancels_loop(self, fast_cfg):
        """stop_event being set causes the loop to exit cleanly."""
        stop = asyncio.Event()
        attempts_seen = []

        async def set_stop():
            await asyncio.sleep(0.02)
            stop.set()

        asyncio.create_task(set_stop())

        async for attempt in retry_with_backoff(fast_cfg, stop_event=stop):
            attempts_seen.append(attempt)
            # never break — let stop_event cancel it

        assert len(attempts_seen) >= 1

    @pytest.mark.unit
    async def test_stop_event_set_before_start_exits_immediately(self):
        """If stop_event is already set, loop exits without any attempt."""
        cfg = RetryConfig(
            initial_delay=0.01,
            max_delay=0.05,
            multiplier=2.0,
            jitter_max=0.0,
        )
        stop = asyncio.Event()
        stop.set()  # set before entering the loop

        attempts_seen = []
        async for attempt in retry_with_backoff(cfg, stop_event=stop):
            attempts_seen.append(attempt)

        assert attempts_seen == []

    @pytest.mark.unit
    async def test_invalid_config_raises(self):
        """Non-RetryConfig raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            async for _ in retry_with_backoff("not_a_config"):
                break
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_invalid_stop_event_raises(self, fast_cfg):
        """Non-asyncio.Event stop_event raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            async for _ in retry_with_backoff(fast_cfg, stop_event="not_an_event"):
                break
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_attempt_numbers_are_sequential(self, fast_cfg):
        """Yielded attempt numbers start at 1 and increment by 1."""
        seen = []
        async for attempt in retry_with_backoff(fast_cfg):
            seen.append(attempt)
            if len(seen) >= 4:
                break

        assert seen == [1, 2, 3, 4]

    @pytest.mark.unit
    async def test_no_max_attempts_retries_indefinitely(self):
        """With max_attempts=None, loop runs until break or stop_event."""
        cfg = RetryConfig(
            initial_delay=0.001,
            max_delay=0.01,
            multiplier=1.0,
            jitter_max=0.0,
            max_attempts=None,
        )
        count = [0]
        async for _ in retry_with_backoff(cfg):
            count[0] += 1
            if count[0] >= 10:
                break

        assert count[0] == 10


# ── RetryExhaustedError ───────────────────────────────────────────────────────


class TestRetryExhaustedError:
    @pytest.mark.unit
    def test_is_exception(self):
        """RetryExhaustedError is an Exception."""
        cfg = RetryConfig(max_attempts=3)
        err = RetryExhaustedError(
            attempts=3,
            last_error=RuntimeError("failed"),
            config=cfg,
        )
        assert isinstance(err, Exception)

    @pytest.mark.unit
    def test_attributes(self):
        """RetryExhaustedError stores attempts, last_error, and config."""
        cfg = RetryConfig(max_attempts=5)
        last = ValueError("last error")
        err = RetryExhaustedError(attempts=5, last_error=last, config=cfg)

        assert err.attempts == 5
        assert err.last_error is last
        assert err.config is cfg

    @pytest.mark.unit
    def test_str_includes_attempt_count(self):
        """str(RetryExhaustedError) mentions the attempt count."""
        cfg = RetryConfig(max_attempts=3)
        err = RetryExhaustedError(
            attempts=3,
            last_error=RuntimeError("oops"),
            config=cfg,
        )
        assert "3" in str(err)
