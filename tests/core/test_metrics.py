"""
tests/core/test_metrics.py

Tests for app/core/metrics.py

Covers:
    - MetricName enum completeness and uniqueness
    - MetricsStore initial state (all counters at zero)
    - increment() — default amount, custom amount, validation
    - get() — existing and unknown metrics
    - reset() — single counter, all counters
    - snapshot() — excludes zeros by default, includes zeros on request
    - snapshot() — returns sorted dict, is a copy
    - log_snapshot() — runs without error, handles all-zero case
    - total_errors() — sums all error_* counters
"""

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.metrics import MetricName


# ── MetricName enum ───────────────────────────────────────────────────────────


class TestMetricName:
    @pytest.mark.unit
    def test_all_values_are_strings(self):
        """Every MetricName value is a non-empty string."""
        for metric in MetricName:
            assert isinstance(metric.value, str)
            assert len(metric.value) > 0

    @pytest.mark.unit
    def test_all_values_are_unique(self):
        """No two MetricNames share the same string value."""
        values = [m.value for m in MetricName]
        assert len(values) == len(set(values))

    @pytest.mark.unit
    def test_all_values_are_lowercase(self):
        """MetricName values are lowercase_snake_case for log readability."""
        for metric in MetricName:
            assert metric.value == metric.value.lower(), (
                f"MetricName {metric.name} value is not lowercase: {metric.value}"
            )

    @pytest.mark.unit
    def test_required_categories_present(self):
        """All metric categories are represented."""
        values = {m.value for m in MetricName}
        categories = ["ws_", "rest_", "mapping_", "mqtt_", "entity_", "error_"]
        for cat in categories:
            assert any(v.startswith(cat) for v in values), (
                f"No MetricName found for category '{cat}'"
            )

    @pytest.mark.unit
    def test_key_metrics_exist(self):
        """Specific metric names referenced in application code exist."""
        required = [
            MetricName.WS_MESSAGES_RECEIVED,
            MetricName.WS_RECONNECT_ATTEMPTS,
            MetricName.REST_REQUESTS_SENT,
            MetricName.REST_COMMANDS_SENT,
            MetricName.MAPPING_DEVICES_PROCESSED,
            MetricName.MAPPING_ENTITIES_CREATED,
            MetricName.MAPPING_STATE_UPDATES,
            MetricName.MAPPING_COMMANDS_TRANSLATED,
            MetricName.MAPPING_UNKNOWN_DEVICE_TYPE,
            MetricName.MAPPING_ERRORS,
            MetricName.MQTT_MESSAGES_PUBLISHED,
            MetricName.MQTT_MESSAGES_RECEIVED,
            MetricName.MQTT_CONNECT_ATTEMPTS,
            MetricName.MQTT_CONNECT_SUCCESS,
            MetricName.ENTITY_REGISTERED,
            MetricName.ENTITY_AVAILABILITY_ONLINE,
            MetricName.ENTITY_AVAILABILITY_OFFLINE,
            MetricName.ERROR_TOTAL,
            MetricName.ERROR_WS,
            MetricName.ERROR_REST,
            MetricName.ERROR_MAPPING,
            MetricName.ERROR_MQTT,
        ]
        for metric in required:
            assert isinstance(metric, MetricName)


# ── MetricsStore initial state ────────────────────────────────────────────────


class TestMetricsStoreInitialState:
    @pytest.mark.unit
    def test_all_counters_start_at_zero(self, metrics):
        """Every counter starts at zero."""
        for metric in MetricName:
            assert metrics.get(metric) == 0, f"{metric.name} should start at 0"

    @pytest.mark.unit
    def test_total_errors_starts_at_zero(self, metrics):
        """total_errors() returns 0 on a fresh store."""
        assert metrics.total_errors() == 0

    @pytest.mark.unit
    def test_snapshot_empty_by_default(self, metrics):
        """snapshot() returns an empty dict when all counters are zero."""
        snap = metrics.snapshot(include_zeros=False)
        assert snap == {}

    @pytest.mark.unit
    def test_snapshot_full_when_include_zeros(self, metrics):
        """snapshot(include_zeros=True) includes all counters."""
        snap = metrics.snapshot(include_zeros=True)
        assert len(snap) == len(MetricName)


# ── increment() ───────────────────────────────────────────────────────────────


class TestIncrement:
    @pytest.mark.unit
    def test_increment_by_one_default(self, metrics):
        """increment() with no amount increments by 1."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED)
        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 1

    @pytest.mark.unit
    def test_increment_by_custom_amount(self, metrics):
        """increment() with amount increments by that amount."""
        metrics.increment(MetricName.MAPPING_ENTITIES_CREATED, amount=5)
        assert metrics.get(MetricName.MAPPING_ENTITIES_CREATED) == 5

    @pytest.mark.unit
    def test_increment_is_cumulative(self, metrics):
        """Multiple increments accumulate correctly."""
        metrics.increment(MetricName.REST_REQUESTS_SENT)
        metrics.increment(MetricName.REST_REQUESTS_SENT)
        metrics.increment(MetricName.REST_REQUESTS_SENT, amount=3)
        assert metrics.get(MetricName.REST_REQUESTS_SENT) == 5

    @pytest.mark.unit
    def test_increment_invalid_metric_raises(self, metrics):
        """increment() raises for non-MetricName metric."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.increment("not_a_metric")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_increment_zero_amount_raises(self, metrics):
        """increment() raises for amount=0."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=0)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_increment_negative_amount_raises(self, metrics):
        """increment() raises for negative amount."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=-1)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_increment_float_amount_raises(self, metrics):
        """increment() raises for float amount."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=1.5)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_increment_different_counters_are_independent(self, metrics):
        """Incrementing one counter does not affect others."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=10)
        assert metrics.get(MetricName.REST_REQUESTS_SENT) == 0
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) == 0


# ── get() ─────────────────────────────────────────────────────────────────────


class TestGet:
    @pytest.mark.unit
    def test_get_returns_current_value(self, metrics):
        """get() returns the current counter value."""
        metrics.increment(MetricName.ENTITY_REGISTERED, amount=3)
        assert metrics.get(MetricName.ENTITY_REGISTERED) == 3

    @pytest.mark.unit
    def test_get_returns_zero_for_untouched_counter(self, metrics):
        """get() returns 0 for a counter that has never been incremented."""
        assert metrics.get(MetricName.MAPPING_UNKNOWN_DEVICE_TYPE) == 0

    @pytest.mark.unit
    def test_get_invalid_metric_raises(self, metrics):
        """get() raises for non-MetricName metric."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.get("not_a_metric")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── reset() ───────────────────────────────────────────────────────────────────


class TestReset:
    @pytest.mark.unit
    def test_reset_single_counter(self, metrics):
        """reset(metric) resets only that counter to zero."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=5)
        metrics.increment(MetricName.REST_REQUESTS_SENT, amount=3)

        metrics.reset(MetricName.WS_MESSAGES_RECEIVED)

        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 0
        assert metrics.get(MetricName.REST_REQUESTS_SENT) == 3

    @pytest.mark.unit
    def test_reset_all_counters(self, metrics):
        """reset() with no argument resets all counters to zero."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=5)
        metrics.increment(MetricName.REST_REQUESTS_SENT, amount=3)
        metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED, amount=10)

        metrics.reset()

        for metric in MetricName:
            assert metrics.get(metric) == 0

    @pytest.mark.unit
    def test_reset_invalid_metric_raises(self, metrics):
        """reset() raises for non-MetricName metric."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            metrics.reset("not_a_metric")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_reset_already_zero_is_noop(self, metrics):
        """reset() on a zero counter does not raise."""
        metrics.reset(MetricName.WS_MESSAGES_RECEIVED)
        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 0


# ── snapshot() ────────────────────────────────────────────────────────────────


class TestSnapshot:
    @pytest.mark.unit
    def test_snapshot_excludes_zeros_by_default(self, metrics):
        """snapshot() excludes zero-value counters by default."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=5)
        snap = metrics.snapshot()
        assert "ws_messages_received" in snap
        assert "rest_requests_sent" not in snap

    @pytest.mark.unit
    def test_snapshot_includes_zeros_when_requested(self, metrics):
        """snapshot(include_zeros=True) includes all counters."""
        snap = metrics.snapshot(include_zeros=True)
        assert len(snap) == len(MetricName)
        for value in snap.values():
            assert value == 0

    @pytest.mark.unit
    def test_snapshot_keys_are_sorted(self, metrics):
        """snapshot() returns keys in sorted order."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED)
        metrics.increment(MetricName.REST_REQUESTS_SENT)
        metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED)

        snap = metrics.snapshot()
        keys = list(snap.keys())
        assert keys == sorted(keys)

    @pytest.mark.unit
    def test_snapshot_is_a_copy(self, metrics):
        """Mutating the snapshot dict does not affect the store."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=5)
        snap = metrics.snapshot()
        snap["ws_messages_received"] = 9999

        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 5

    @pytest.mark.unit
    def test_snapshot_values_are_correct(self, metrics):
        """snapshot() values match the current counter values."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=7)
        metrics.increment(MetricName.REST_REQUESTS_SENT, amount=3)

        snap = metrics.snapshot(include_zeros=False)
        assert snap["ws_messages_received"] == 7
        assert snap["rest_requests_sent"] == 3

    @pytest.mark.unit
    def test_snapshot_uses_string_keys(self, metrics):
        """snapshot() keys are string values, not MetricName enums."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED)
        snap = metrics.snapshot()
        for key in snap:
            assert isinstance(key, str)


# ── log_snapshot() ────────────────────────────────────────────────────────────


class TestLogSnapshot:
    @pytest.mark.unit
    def test_log_snapshot_all_zeros(self, metrics, caplog):
        """log_snapshot() with all zeros logs a specific message."""
        import logging

        with caplog.at_level(logging.INFO):
            metrics.log_snapshot()
        assert "all counters are zero" in caplog.text

    @pytest.mark.unit
    def test_log_snapshot_with_values(self, metrics, caplog):
        """log_snapshot() with non-zero counters logs the values."""
        import logging

        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=42)

        with caplog.at_level(logging.INFO):
            metrics.log_snapshot()

        assert "ws_messages_received=42" in caplog.text

    @pytest.mark.unit
    def test_log_snapshot_does_not_raise(self, metrics):
        """log_snapshot() never raises regardless of state."""
        metrics.increment(MetricName.ERROR_TOTAL, amount=100)
        metrics.log_snapshot()
        metrics.log_snapshot(include_zeros=True)


# ── total_errors() ────────────────────────────────────────────────────────────


class TestTotalErrors:
    @pytest.mark.unit
    def test_total_errors_sums_error_counters(self, metrics):
        """total_errors() sums all error_* counters."""
        metrics.increment(MetricName.ERROR_TOTAL, amount=2)
        metrics.increment(MetricName.ERROR_WS, amount=1)
        metrics.increment(MetricName.ERROR_REST, amount=3)
        metrics.increment(MetricName.ERROR_MAPPING, amount=1)
        metrics.increment(MetricName.ERROR_MQTT, amount=2)

        assert metrics.total_errors() == 9

    @pytest.mark.unit
    def test_total_errors_ignores_non_error_counters(self, metrics):
        """total_errors() does not count non-error metrics."""
        metrics.increment(MetricName.WS_MESSAGES_RECEIVED, amount=100)
        metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED, amount=50)

        assert metrics.total_errors() == 0

    @pytest.mark.unit
    def test_total_errors_zero_initially(self, metrics):
        """total_errors() returns 0 on a fresh store."""
        assert metrics.total_errors() == 0

    @pytest.mark.unit
    def test_total_errors_resets_with_reset_all(self, metrics):
        """total_errors() returns 0 after reset()."""
        metrics.increment(MetricName.ERROR_WS, amount=5)
        metrics.reset()
        assert metrics.total_errors() == 0
