"""
metrics.py

Lightweight in-memory metrics counter store.

Role & Responsibility:
    Provides a centralized place to track operational counters for the
    bridge service. No external dependencies, no HTTP endpoint, no
    Prometheus. Counters are incremented throughout the application and
    periodically emitted to the structured log by the orchestrator.

    The goal is to make common operational questions answerable from
    the logs without SSH-ing into the Pi:
        - How many WebSocket messages have been received?
        - How many state updates have been forwarded to HA?
        - How many commands have arrived from HA?
        - How many reconnect attempts have occurred?
        - How many mapping errors have been seen?

What it does:
    - Defines MetricName enum as the single source of truth for all
    counter names used in the application
    - Provides MetricsStore with increment(), get(), reset(),
    snapshot(), and log_snapshot() methods
    - All operations are O(1) using a plain dict internally
    - snapshot() returns a sorted, immutable copy safe to log or
    inspect without holding any lock
    - log_snapshot() emits a structured INFO log line with all
    non-zero counters — suitable for periodic health reporting

Arguments / Configuration:
    No runtime configuration. Instantiated once by the orchestrator
    and injected into components that need to record metrics.

Used by:
    - app/orchestrator.py            (creates store, calls
                        log_snapshot() periodically)
    - app/dirigera/websocket_client.py    (WS message counters)
    - app/dirigera/rest_client.py        (REST call counters)
    - app/mapping/device_mapper.py        (mapping counters)
    - app/ha/ha_client.py            (MQTT publish counters)

Not responsible for:
    - Persisting metrics across restarts (in-memory only)
    - Exposing metrics over HTTP or any external protocol
    - Timing / histogram / gauge style metrics (counters only)
    - Thread safety (single-threaded asyncio application)
"""

from __future__ import annotations

import logging
from enum import Enum, unique
from typing import Dict

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "MetricName",
    "MetricsStore",
]

logger = logging.getLogger(__name__)


# ── Metric names ──────────────────────────────────────────────────────────────


@unique
class MetricName(str, Enum):
    """
    All metric counter names used in the application.

    Categories:
        WS_*        Dirigera WebSocket connection and message counters
        REST_*        Dirigera REST API call counters
        MAPPING_*    Device/state/command mapping counters
        MQTT_*        MQTT publish and subscribe counters
        ENTITY_*    HA entity registration counters
        ERROR_*        Error counters per category
    """

    # ── WebSocket ─────────────────────────────────────────────────────────
    WS_MESSAGES_RECEIVED = "ws_messages_received"
    WS_MESSAGES_PARSE_ERROR = "ws_messages_parse_error"
    WS_CONNECT_ATTEMPTS = "ws_connect_attempts"
    WS_CONNECT_SUCCESS = "ws_connect_success"
    WS_RECONNECT_ATTEMPTS = "ws_reconnect_attempts"
    WS_DISCONNECTS = "ws_disconnects"

    # ── REST ──────────────────────────────────────────────────────────────
    REST_REQUESTS_SENT = "rest_requests_sent"
    REST_REQUESTS_SUCCESS = "rest_requests_success"
    REST_REQUESTS_FAILED = "rest_requests_failed"
    REST_COMMANDS_SENT = "rest_commands_sent"

    # ── Mapping ───────────────────────────────────────────────────────────
    MAPPING_DEVICES_PROCESSED = "mapping_devices_processed"
    MAPPING_ENTITIES_CREATED = "mapping_entities_created"
    MAPPING_STATE_UPDATES = "mapping_state_updates"
    MAPPING_COMMANDS_TRANSLATED = "mapping_commands_translated"
    MAPPING_UNKNOWN_DEVICE_TYPE = "mapping_unknown_device_type"
    MAPPING_ERRORS = "mapping_errors"

    # ── MQTT ──────────────────────────────────────────────────────────────
    MQTT_MESSAGES_PUBLISHED = "mqtt_messages_published"
    MQTT_MESSAGES_RECEIVED = "mqtt_messages_received"
    MQTT_CONNECT_ATTEMPTS = "mqtt_connect_attempts"
    MQTT_CONNECT_SUCCESS = "mqtt_connect_success"
    MQTT_RECONNECT_ATTEMPTS = "mqtt_reconnect_attempts"
    MQTT_PUBLISH_ERRORS = "mqtt_publish_errors"

    # ── Entity ────────────────────────────────────────────────────────────
    ENTITY_REGISTERED = "entity_registered"
    ENTITY_ALREADY_REGISTERED = "entity_already_registered"
    ENTITY_AVAILABILITY_ONLINE = "entity_availability_online"
    ENTITY_AVAILABILITY_OFFLINE = "entity_availability_offline"

    # ── Errors (cross-cutting) ────────────────────────────────────────────
    ERROR_TOTAL = "error_total"
    ERROR_WS = "error_ws"
    ERROR_REST = "error_rest"
    ERROR_MAPPING = "error_mapping"
    ERROR_MQTT = "error_mqtt"


# ── Metrics store ─────────────────────────────────────────────────────────────


class MetricsStore:
    """
    Lightweight in-memory counter store.

    All counters start at zero. Counters can only be incremented —
    never decremented. Use reset() to clear individual counters or
    all counters at once (e.g. for periodic windowed reporting).

    Instantiate once and inject into all components that need to
    record metrics.
    """

    def __init__(self) -> None:
        # Initialize all known counters at zero
        self._counters: Dict[MetricName, int] = {metric: 0 for metric in MetricName}
        logger.debug(
            "MetricsStore initialised with %d counters",
            len(self._counters),
        )

    # ── Public API ────────────────────────────────────────────────────────

    def increment(
        self,
        metric: MetricName,
        amount: int = 1,
    ) -> None:
        """
        Increment a counter by the given amount.

        Args:
            metric (MetricName):    The counter to increment.
            amount (int):        Amount to add. Must be a positive
                        integer. Defaults to 1.

        Raises:
            DirigeraBridgeError: If metric is not a MetricName or
            amount is not a positive integer.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(metric, MetricName):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"increment: metric must be MetricName, got {type(metric).__name__}",
            )

        if not isinstance(amount, int) or amount < 1:
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"increment: amount must be a positive integer, got {amount!r}",
            )

        # ── Increment ─────────────────────────────────────────────────────
        self._counters[metric] += amount

        logger.debug(
            "Metric '%s' incremented by %d → %d",
            metric.value,
            amount,
            self._counters[metric],
        )

    def get(self, metric: MetricName) -> int:
        """
        Return the current value of a counter.

        Args:
            metric (MetricName):    The counter to read.

        Returns:
            int:            Current counter value (always >= 0).

        Raises:
            DirigeraBridgeError: If metric is not a MetricName.
        """

        if not isinstance(metric, MetricName):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"get: metric must be MetricName, got {type(metric).__name__}",
            )

        return self._counters[metric]

    def reset(self, metric: MetricName | None = None) -> None:
        """
        Reset one counter or all counters to zero.

        Args:
            metric (MetricName | None):    Counter to reset. If None,
                            all counters are reset to zero.

        Raises:
            DirigeraBridgeError: If metric is provided but is not a
            MetricName.
        """

        if metric is not None:
            if not isinstance(metric, MetricName):
                raise DirigeraBridgeError(
                    ErrorCode.INTERNAL_INVALID_ARGUMENT,
                    f"reset: metric must be MetricName or None, "
                    f"got {type(metric).__name__}",
                )
            self._counters[metric] = 0
            logger.debug("Metric '%s' reset to 0", metric.value)
        else:
            for m in MetricName:
                self._counters[m] = 0
            logger.debug("All metrics reset to 0")

    def snapshot(self, include_zeros: bool = False) -> Dict[str, int]:
        """
        Return a sorted, immutable snapshot of all counter values.

        The snapshot is a plain dict keyed by the string value of
        each MetricName (e.g. 'ws_messages_received'). It is safe
        to log, serialize, or inspect without holding any lock.

        Args:
            include_zeros (bool):    If True, include counters with a
                        value of zero. If False (default),
                        only non-zero counters are returned.
                        Keeping zeros out keeps log lines
                        short during normal operation.

        Returns:
            Dict[str, int]: Sorted snapshot of counter values.
        """

        items = (
            (metric.value, value)
            for metric, value in self._counters.items()
            if include_zeros or value > 0
        )

        return dict(sorted(items))

    def log_snapshot(self, include_zeros: bool = False) -> None:
        """
        Emit all counter values as a single structured INFO log line.

        Called periodically by the orchestrator to provide a health
        summary without requiring external monitoring tools.

        If no counters have been incremented yet and include_zeros is
        False, logs a single INFO line noting that all counters are zero
        rather than logging an empty dict.

        Args:
            include_zeros (bool):    Passed through to snapshot(). Defaults
                        to False so routine health reports stay
                        concise.
        """

        data = self.snapshot(include_zeros=include_zeros)

        if not data:
            logger.info("Metrics snapshot: all counters are zero")
            return

        # Format as key=value pairs for easy grep/parsing
        formatted = "  ".join(f"{key}={value}" for key, value in data.items())

        logger.info("Metrics snapshot: %s", formatted)

    def total_errors(self) -> int:
        """
        Return the sum of all error counters.

        Convenience method for health checks and lifecycle decisions.

        Returns:
            int:    Total error count across all ERROR_* metrics.
        """

        return sum(
            self._counters[metric]
            for metric in MetricName
            if metric.value.startswith("error_")
        )
