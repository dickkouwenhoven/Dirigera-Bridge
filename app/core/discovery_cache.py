"""
discovery_cache.py

In-memory Home Assistant entity discovery and registration cache.

Role & Responsibility:
    Tracks which Dirigera logical device ids have already had their
    entities registered (discovered) in Home Assistant via MQTT
    discovery. Prevents the bridge from re-sending discovery payloads
    on every reconnect, which would cause unnecessary MQTT traffic and
    brief HA entity flickering.

    This is the single source of truth for the question:
    "Has this logical device already been registered in HA?"

    It also stores the relation_id for each registered logical_id so
    that the orchestrator can determine which physical devices are fully
    registered (all sibling logical ids discovered) vs partially
    registered (some siblings not yet discovered).

What it does:
    - register() marks a logical_id as discovered and records its
    relation_id and the HA domain(s) registered for it
    - is_registered() checks whether a logical_id is already in HA
    - get_registered_domains() returns which HA domains were registered
    for a specific logical_id (used to avoid partial re-registration)
    - get_all_logical_ids() returns all registered logical ids
    - get_logical_ids_for_relation() returns all registered logical ids
    that share a given relation_id (i.e. all siblings of a physical
    device that have been registered)
    - unregister() removes a logical_id (used when a device is removed)
    - clear() wipes the entire cache (used on full restart / reconnect
    where re-discovery is desired)
    - snapshot() returns an immutable copy for diagnostics and logging

Arguments / Configuration:
    No runtime configuration. Instantiated once by the orchestrator
    and injected into ha_client.py and the orchestrator's startup loop.

Used by:
    - app/orchestrator.py       (creates cache, drives startup discovery)
    - app/ha/ha_client.py       (checks before registering entities)

Not responsible for:
    - Device state (that is state_cache.py)
    - MQTT publishing (that is ha_client.py)
    - Any async operations (all methods are synchronous)
    - Persisting registrations across restarts — on restart the bridge
    re-discovers all devices and re-registers them. HA handles
    duplicate discovery gracefully (retained messages + unique_id
    deduplication). The cache only prevents redundant re-registration
    within a single run of the bridge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "RegistrationRecord",
    "DiscoveryCache",
]

logger = logging.getLogger(__name__)


# ── Registration record ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegistrationRecord:
    """
    Immutable record of a single logical device's HA registration.

    Args:
        logical_id (str):
            The Dirigera logical device id
            (e.g. 'fff75d00-607c-4f23-a0e7-3dbed0e18b12_1').

        relation_id (str):
            The physical device relation id. Equal to logical_id for
            single-deviceType devices; equal to the shared relationId
            for multi-deviceType devices.

        ha_domains (frozenset[str]):
            The HA domain strings registered for this logical id,
            e.g. frozenset({'binary_sensor', 'sensor'}).
            Stored as strings (not HADomain enum) so this module has
            no dependency on the HASDK.

        device_name (str):
            The human-readable device name used at registration time.
            Stored for diagnostics and log output only.
    """

    logical_id: str
    relation_id: str
    ha_domains: FrozenSet[str]
    device_name: str


# ── Discovery cache ───────────────────────────────────────────────────────────


class DiscoveryCache:
    """
    In-memory cache tracking which Dirigera logical devices have had
    their entities registered in Home Assistant.

    All methods are synchronous. Safe to call from any async context.

    The cache is keyed by logical_id — each Dirigera logical device
    (one per deviceType) has its own registration record. Devices that
    expose multiple deviceTypes (e.g. VALLHORN with motionSensor +
    lightSensor) will have two records, each with the same relation_id
    but different logical_ids and different ha_domains.
    """

    def __init__(self) -> None:
        # Primary index: logical_id → RegistrationRecord
        self._by_logical_id: Dict[str, RegistrationRecord] = {}

        # Secondary index: relation_id → set of logical_ids
        # Maintained in sync with _by_logical_id for O(1) sibling lookup
        self._by_relation_id: Dict[str, Set[str]] = {}

        logger.debug("DiscoveryCache initialised")

    # ── Public API — write ────────────────────────────────────────────────

    def register(
        self,
        logical_id: str,
        relation_id: str,
        ha_domains: List[str],
        device_name: str,
    ) -> None:
        """
        Mark a logical device as registered in Home Assistant.

        Idempotent — calling register() for an already-registered
        logical_id updates its record rather than raising an error.
        This handles the case where a reconnect triggers re-registration
        with a different set of domains (e.g. after a firmware update
        that exposes new capabilities).

        Args:
            logical_id (str):    Dirigera logical device id.
            relation_id (str):    Physical device relation id.
            ha_domains (list):    List of HA domain strings registered
                        for this logical id (e.g. ['sensor']).
                        Must be a non-empty list of non-empty
                        strings.
            device_name (str):    Human-readable name for log output.

        Raises:
            DirigeraBridgeError:    If any argument fails validation.
        """

        # ── Validation ────────────────────────────────────────────────────
        _validate_str(logical_id, "logical_id")
        _validate_str(relation_id, "relation_id")
        _validate_str(device_name, "device_name")
        _validate_domains(ha_domains)

        # ── Build record ──────────────────────────────────────────────────
        record = RegistrationRecord(
            logical_id=logical_id,
            relation_id=relation_id,
            ha_domains=frozenset(ha_domains),
            device_name=device_name,
        )

        previously_registered = logical_id in self._by_logical_id

        # ── Update primary index ──────────────────────────────────────────
        self._by_logical_id[logical_id] = record

        # ── Update secondary index ────────────────────────────────────────
        if relation_id not in self._by_relation_id:
            self._by_relation_id[relation_id] = set()
        self._by_relation_id[relation_id].add(logical_id)

        # ── Log ───────────────────────────────────────────────────────────
        if previously_registered:
            logger.debug(
                "DiscoveryCache: updated registration for '%s' "
                "(logical_id=%s, domains=%s)",
                device_name,
                logical_id,
                sorted(ha_domains),
            )
        else:
            logger.info(
                "DiscoveryCache: registered '%s' "
                "(logical_id=%s, relation_id=%s, domains=%s)",
                device_name,
                logical_id,
                relation_id,
                sorted(ha_domains),
            )

    def unregister(self, logical_id: str) -> None:
        """
        Remove a logical device from the cache.

        Safe to call even if the logical_id is not registered (no-op).
        Also cleans up the secondary relation_id index.

        Args:
            logical_id (str):    Dirigera logical device id to remove.

        Raises:
            DirigeraBridgeError:    If logical_id is not a non-empty string.
        """

        _validate_str(logical_id, "logical_id")

        record = self._by_logical_id.pop(logical_id, None)

        if record is None:
            logger.debug(
                "DiscoveryCache: unregister called for unknown logical_id=%s — no-op",
                logical_id,
            )
            return

        # Clean up secondary index
        sibling_set = self._by_relation_id.get(record.relation_id)
        if sibling_set is not None:
            sibling_set.discard(logical_id)
            if not sibling_set:
                del self._by_relation_id[record.relation_id]

        logger.info(
            "DiscoveryCache: unregistered '%s' (logical_id=%s)",
            record.device_name,
            logical_id,
        )

    def clear(self) -> None:
        """
        Wipe the entire cache.

        Called by the orchestrator when a full re-discovery is needed
        (e.g. on initial startup or after a long disconnect where
        devices may have been added or removed from the hub).
        """

        count = len(self._by_logical_id)
        self._by_logical_id.clear()
        self._by_relation_id.clear()
        logger.info(
            "DiscoveryCache: cleared all registrations (%d logical device(s) removed)",
            count,
        )

    # ── Public API — read ─────────────────────────────────────────────────

    def is_registered(self, logical_id: str) -> bool:
        """
        Return True if the logical device has been registered in HA.

        Args:
            logical_id (str):    Dirigera logical device id.

        Returns:
            bool:            True if registered.

        Raises:
            DirigeraBridgeError:    If logical_id is not a non-empty string.
        """

        _validate_str(logical_id, "logical_id")
        return logical_id in self._by_logical_id

    def get_record(self, logical_id: str) -> Optional[RegistrationRecord]:
        """
        Return the full RegistrationRecord for a logical device,
        or None if not registered.

        Args:
            logical_id (str):    Dirigera logical device id.

        Returns:
            RegistrationRecord | None

        Raises:
            DirigeraBridgeError:    If logical_id is not a non-empty string.
        """

        _validate_str(logical_id, "logical_id")
        return self._by_logical_id.get(logical_id)

    def get_registered_domains(self, logical_id: str) -> FrozenSet[str]:
        """
        Return the HA domains registered for a specific logical device.

        Returns an empty frozenset if the device is not registered.

        Args:
            logical_id (str): Dirigera logical device id.

        Returns:
            FrozenSet[str]: Registered HA domain strings.

        Raises:
            DirigeraBridgeError: If logical_id is not a non-empty string.
        """

        _validate_str(logical_id, "logical_id")
        record = self._by_logical_id.get(logical_id)
        return record.ha_domains if record is not None else frozenset()

    def get_all_logical_ids(self) -> Set[str]:
        """
        Return the set of all registered logical device ids.

        Returns a copy so callers cannot mutate the internal key set.

        Returns:
            Set[str]: All registered logical device ids.
        """

        return set(self._by_logical_id.keys())

    def get_logical_ids_for_relation(
        self,
        relation_id: str,
    ) -> Set[str]:
        """
        Return all registered logical ids that share a relation_id.

        For a single-deviceType device this returns a set of one.
        For a multi-deviceType device (e.g. VALLHORN) this returns
        all siblings that have been registered so far.

        Used by the orchestrator to determine whether all deviceTypes
        of a physical device have been discovered.

        Args:
            relation_id (str):    Physical device relation id.

        Returns:
            Set[str]:        Registered logical ids for this relation.
                        Empty set if the relation_id is unknown.

        Raises:
            DirigeraBridgeError:    If relation_id is not a non-empty
                        string.
        """

        _validate_str(relation_id, "relation_id")
        return set(self._by_relation_id.get(relation_id, set()))

    def registered_count(self) -> int:
        """
        Return the total number of registered logical devices.

        Returns:
            int:    Count of registered logical devices.
        """

        return len(self._by_logical_id)

    def relation_count(self) -> int:
        """
        Return the total number of distinct physical devices
        (relation ids) that have at least one registered logical id.

        Returns:
            int:    Count of distinct physical devices registered.
        """

        return len(self._by_relation_id)

    # ── Public API — snapshot ─────────────────────────────────────────────

    def snapshot(self) -> Dict[str, RegistrationRecord]:
        """
        Return a shallow copy of the full registration index.

        The RegistrationRecord values are frozen dataclasses and safe
        to read without copying. The outer dict is copied so callers
        cannot add or remove keys from the internal store.

        Used for diagnostics, health logging, and test assertions.

        Returns:
            Dict[str, RegistrationRecord]:
                logical_id → RegistrationRecord
        """

        snap = dict(self._by_logical_id)

        logger.debug(
            "DiscoveryCache: snapshot taken — %d logical device(s), "
            "%d physical device(s)",
            len(snap),
            self.relation_count(),
        )

        return snap


# ── Module-level validation helpers ──────────────────────────────────────────


def _validate_str(value: object, name: str) -> None:
    """
    Validate that a value is a non-empty string.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if invalid.
    """

    if not isinstance(value, str) or not value.strip():
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"DiscoveryCache: {name} must be a non-empty string, got {value!r}",
        )


def _validate_domains(ha_domains: object) -> None:
    """
    Validate that ha_domains is a non-empty list of non-empty strings.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if invalid.
    """

    if not isinstance(ha_domains, list) or not ha_domains:
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"DiscoveryCache: ha_domains must be a non-empty list, got {ha_domains!r}",
        )

    for domain in ha_domains:
        if not isinstance(domain, str) or not domain.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DiscoveryCache: each domain must be a non-empty "
                f"string, got {domain!r}",
            )
