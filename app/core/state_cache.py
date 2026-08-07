"""
state_cache.py

In-memory device state cache.

Role & Responsibility:
    Maintains the last-known state of every Dirigera device attribute
    seen by the bridge. Acts as the authoritative in-memory view of
    the Dirigera world at any given moment.

    The cache serves two purposes:
    1. Deduplication — if a WebSocket event arrives with a value
       identical to what is already cached, the bridge skips the MQTT
       publish. This prevents unnecessary chatter toward Home Assistant
       when Dirigera sends repeat events (which it does, particularly
       for periodic heartbeat-style updates).
    2. Full state recovery — when the MQTT connection reconnects, the
       orchestrator can replay the entire cached state to Home Assistant
       so HA does not show stale values after a brief disconnect.

What it does:
    - Stores device attribute values keyed by (logical_id, attribute)
    - set() updates a single attribute value and returns True if the
      value changed, False if it was already identical (for dedup)
    - get() retrieves a single attribute value
    - get_device_state() returns all cached attributes for a device
    - has_changed() checks whether a value differs from cache without
      writing (useful for conditional logic)
    - get_all_logical_ids() returns the set of all known device ids
    - clear_device() removes all cached state for a single device
    - clear() wipes the entire cache (used on full reconnect)
    - snapshot() returns a deep copy of the entire cache for replay

Arguments / Configuration:
    No runtime configuration. Instantiated once by the orchestrator
    and injected into components that need to read or write state.

Used by:
    - app/orchestrator.py (creates cache, drives replay)
    - app/dirigera/websocket_client.py (reads is_changed before publish)
    - app/ha/ha_client.py (writes on state update)
    - app/core/event_bus.py (subscribed handler updates cache)

Not responsible for:
    - Persisting state to disk (in-memory only — intentional)
    - Discovery/registration state (that is discovery_cache.py)
    - Any I/O or async operations (all methods are synchronous)
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Optional, Set

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "StateCache",
]

logger = logging.getLogger(__name__)

# Internal type alias: maps attribute_name -> value
_DeviceState = Dict[str, Any]

# Internal type alias: maps logical_id -> _DeviceState
_CacheStore = Dict[str, _DeviceState]


class StateCache:
    """
    In-memory cache of the last-known attribute values for every
    Dirigera logical device seen by the bridge.

    All methods are synchronous — the cache contains no async logic.
    It is safe to call from any async context without awaiting.

    Keys:
    logical_id (str): The Dirigera logical device id,
        e.g. 'fff75d00-607c-4f23-a0e7-3dbed0e18b12_1'.
    attribute (str): The Dirigera attribute name,
        e.g. 'isOn', 'lightLevel', 'isDetected'.
    """

    def __init__(self) -> None:
        self._store: _CacheStore = {}
        logger.debug("StateCache initialised")

    # ── Public API — write ────────────────────────────────────────────────

    def set(
        self,
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> bool:
        """
        Update a single attribute value in the cache.

        Returns True if the value changed (i.e. is different from
        what was previously cached), False if it was already identical.

        Callers use the return value for deduplication:
        if cache.set(logical_id, attribute, new_value):
            await ha_client.publish_state(...)

        Args:
        logical_id (str): Dirigera logical device id.
        attribute (str): Attribute name (e.g. 'isOn').
        value (Any): New attribute value. It may be any JSON-
            serializable type.

        Returns:
        bool: True if the value changed, False if unchanged.

        Raises:
        DirigeraBridgeError: If logical_id or attribute are not
            non-empty strings.
        """

        # ── Validation ────────────────────────────────────────────────────
        _validate_id(logical_id, "logical_id")
        _validate_id(attribute, "attribute")

        # ── Compare and write ─────────────────────────────────────────────
        device_state = self._store.setdefault(logical_id, {})
        previous = device_state.get(attribute, _SENTINEL)

        if previous is not _SENTINEL and previous == value:
            logger.debug(
                "StateCache: no change for %s.%s = %r",
                logical_id,
                attribute,
                value,
            )
            return False

        device_state[attribute] = value

        logger.debug(
            "StateCache: set %s.%s = %r (was %r)",
            logical_id,
            attribute,
            value,
            previous if previous is not _SENTINEL else "<not set>",
        )

        return True

    def set_device_state(
        self,
        logical_id: str,
        attributes: Dict[str, Any],
    ) -> Dict[str, bool]:
        """
        Update multiple attribute values for a device in one call.

        Useful at startup when the full device state is loaded from
        the Dirigera REST API.

        Args:
        logical_id (str): Dirigera logical device id.
        attributes (dict): Mapping of attribute_name → value.

        Returns:
        Dict[str, bool]: Mapping of attribute_name → changed flag,
            mirroring the return value of set() per
            attribute.

        Raises:
        DirigeraBridgeError: If logical_id is not a non-empty string
            or attributes is not a dict.
        """

        # ── Validation ────────────────────────────────────────────────────
        _validate_id(logical_id, "logical_id")

        if not isinstance(attributes, dict):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"set_device_state: attributes must be dict, "
                f"got {type(attributes).__name__}",
            )

        # ── Write each attribute ──────────────────────────────────────────
        results: Dict[str, bool] = {}
        for attr, value in attributes.items():
            results[attr] = self.set(logical_id, attr, value)

            logger.debug(
                "StateCache: set_device_state for %s — %d attribute(s), %d changed",
                logical_id,
                len(attributes),
                sum(results.values()),
            )

        return results

    # ── Public API — read ─────────────────────────────────────────────────

    def get(
        self,
        logical_id: str,
        attribute: str,
        default: Any = None,
    ) -> Any:
        """
        Retrieve a single cached attribute value.

        Args:
        logical_id (str): Dirigera logical device id.
        attribute (str): Attribute name.
        default (Any): Value to return if the attribute is not
            cached. Defaults to None.

        Returns:
        Any: Cached value or default.

        Raises:
        DirigeraBridgeError: If logical_id or attribute are not
            non-empty strings.
        """

        _validate_id(logical_id, "logical_id")
        _validate_id(attribute, "attribute")

        return self._store.get(logical_id, {}).get(attribute, default)

    def get_device_state(
        self,
        logical_id: str,
    ) -> Dict[str, Any]:
        """
        Return all cached attributes for a specific device.

        Returns a shallow copy so callers cannot mutate the cache.
        Returns an empty dict if the device is not in the cache.

        Args:
            logical_id (str): Dirigera logical device id.

        Returns:
            Dict[str, Any]: Attribute name → value mapping.

        Raises:
            DirigeraBridgeError: If logical_id is not a non-empty string.
        """

        _validate_id(logical_id, "logical_id")
        return dict(self._store.get(logical_id, {}))

    def has_changed(
        self,
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> bool:
        """
        Return True if value differs from what is currently cached,
        without writing anything to the cache.

        Use this for conditional logic that needs to check before
        deciding whether to write.

        Args:
            logical_id (str):    Dirigera logical device id.
            attribute (str):    Attribute name.
            value (Any):        Value to compare against cache.

        Returns:
            bool:            True if value is different from (or not present in)
                        the cache, False if identical.

        Raises:
            DirigeraBridgeError: If logical_id or attribute are not
            non-empty strings.
        """

        _validate_id(logical_id, "logical_id")
        _validate_id(attribute, "attribute")

        cached = self._store.get(logical_id, {}).get(attribute, _SENTINEL)

        if cached is _SENTINEL:
            return True  # Not cached yet — treat as changed

        return cached != value

    def get_all_logical_ids(self) -> Set[str]:
        """
        Return the set of all logical device ids currently in the cache.

        Returns a copy so callers cannot mutate the internal key set.

        Returns:
            Set[str]:    All known logical device ids.
        """

        return set(self._store.keys())

    def device_count(self) -> int:
        """
        Return the number of distinct logical devices in the cache.

        Returns:
            int:    Number of devices.
        """

        return len(self._store)

    def attribute_count(self, logical_id: Optional[str] = None) -> int:
        """
        Return the number of cached attributes.

        Args:
            logical_id (str | None):    If provided, return the count for
                            that device only. If None, return
                            the total across all devices.

        Returns:
            int:                Attribute count.

        Raises:
            DirigeraBridgeError:        If logical_id is provided but not a
                            non-empty string.
        """

        if logical_id is not None:
            _validate_id(logical_id, "logical_id")
            return len(self._store.get(logical_id, {}))

        return sum(len(attrs) for attrs in self._store.values())

    # ── Public API — delete ───────────────────────────────────────────────

    def clear_device(self, logical_id: str) -> None:
        """
        Remove all cached state for a specific device.

        Safe to call even if the device is not in the cache (no-op).

        Args:
            logical_id (str):    Dirigera logical device id.

        Raises:
            DirigeraBridgeError:    If logical_id is not a non-empty string.
        """

        _validate_id(logical_id, "logical_id")

        if logical_id in self._store:
            del self._store[logical_id]
            logger.debug(
                "StateCache: cleared state for device %s",
                logical_id,
            )

    def clear(self) -> None:
        """
        Wipe the entire cache.

        Called by the orchestrator before a full state replay, e.g.
        after a reconnect where the entire device list may have changed.
        """

        count = self.device_count()
        self._store.clear()
        logger.info(
            "StateCache: cleared all state (%d device(s) removed)",
            count,
        )

    # ── Public API — snapshot ─────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Return a deep copy of the entire cache.

        Used by the orchestrator to replay the full device state to
        Home Assistant after an MQTT reconnect, ensuring HA does not
        show stale values. The deep copy prevents the replay from being
        affected by concurrent cache updates during iteration.

        Returns:
            Dict[str, Dict[str, Any]]:
                logical_id → {attribute_name → value}
        """

        snap = copy.deepcopy(self._store)

        logger.debug(
            "StateCache: snapshot taken — %d device(s), %d attribute(s)",
            len(snap),
            sum(len(attrs) for attrs in snap.values()),
        )

        return snap


# ── Module-level helpers ──────────────────────────────────────────────────────

# Sentinel object used to distinguish "attribute not cached" from
# "attribute cached with value None"
_SENTINEL = object()


def _validate_id(value: Any, name: str) -> None:
    """
    Validate that a cache key is a non-empty string.

    Args:
        value:            The value to validate.
        name:            The argument name for the error message.

    Raises:
        DirigeraBridgeError:    INTERNAL_INVALID_ARGUMENT if invalid.
    """

    if not isinstance(value, str) or not value.strip():
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"StateCache: {name} must be a non-empty string, got {value!r}",
        )
