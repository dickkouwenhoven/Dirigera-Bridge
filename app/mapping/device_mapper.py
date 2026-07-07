"""
device_mapper.py

Maps Dirigera DeviceContext objects to lists of HA Entity objects
using the plugin registry from domains/__init__.py.

Role & Responsibility:
    Owns the translation from a normalised DeviceContext (produced by
    device_registry.py) to a list of HA Entity objects ready for
    registration via the HASDK. This is the central routing point of
    the mapping layer — it looks up the correct domain mapper for each
    deviceType and delegates to it.

    Also, responsible for constructing the DeviceInfo object that groups
    all entities from the same physical device under one device entry
    in the HA device registry.

What it does:
    - Receives a DeviceContext from the orchestrator
    - Builds a DeviceInfo using create_device_info() from the HASDK,
      keyed on context.serial_number (the physical device identifier)
    - Looks up the domain mapper function in DEVICE_TYPE_REGISTRY
    - Calls the mapper and returns the resulting List[Entity]
    - Logs and increments metrics for unknown device types
    - Returns an empty list for unknown/unsupported device types so
      one unknown type never prevents other devices from loading

Arguments / Configuration:
    metrics (MetricsStore): Injected metrics store for counters.

Used by:
    - app/orchestrator.py  (calls map_device() for each DeviceContext
                            returned by build_device_contexts())

Not responsible for:
    - Grouping logical devices by physical device (device_registry.py)
    - Publishing entities to HA (ha_client.py)
    - Caching discovery or state (core/discovery_cache, core/state_cache)
    - Individual device type logic (domain mapper files)

Design notes:
    - create_device_info() is called here, not in the orchestrator.
      The mapping layer knows the Dirigera field names and how they
      map to HA DeviceInfo fields. The orchestrator does not.
    - DeviceInfo uses serial_number as the identifier (not relation_id
      or logical_id).  Dirigera's raw discovery data has three
      candidate fields per device - id, relationId, and serialNumber
      - but relationId is not present on every device, so it can´t be
      relied on. id is derived from serialNumber with a "_1"/"_2"/...
      suffix appended for sibling logical devices grouped under one
      physical device (e.q. a multi-button remote). serialNumber is
      therefore the only field guaranteed present and stable across
      all sibling deviceTypes of the same physical device, so it is
      what groups them under one device entry in HA.
    - The DEVICE_TYPE_REGISTRY is imported from domains/__init__.py.
      Adding a new device type requires only adding a domain file
      and a DEVICE_TYPES entry — this file never changes.
    - map_device() returns [] for unknown device types (logged as
      warning + metric) so the orchestrator can continue processing
      remaining devices.
"""

from __future__ import annotations

import logging
from typing import List

from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo, create_device_info

from ..core.errors import DirigeraBridgeError, ErrorCode
from ..core.metrics import MetricName, MetricsStore
from .device_registry import DeviceContext
from .domains import DEVICE_TYPE_REGISTRY

__all__ = [
    "DeviceMapper",
    "build_device_info",
]

logger = logging.getLogger(__name__)


class DeviceMapper:
    """
    Routes DeviceContext objects to domain mappers and builds
    the DeviceInfo block for HA device grouping.

    Args:
        metrics (MetricsStore): Metrics store for tracking mapping
                                counters. Must not be None.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if metrics is
                             not a MetricsStore instance.
    """

    def __init__(self, metrics: MetricsStore) -> None:

        if not isinstance(metrics, MetricsStore):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DeviceMapper: metrics must be MetricsStore, "
                f"got {type(metrics).__name__}",
            )

        self._metrics = metrics

        logger.debug(
            "DeviceMapper initialised with %d registered device type(s)",
            len(DEVICE_TYPE_REGISTRY),
        )

    # ── Public API ────────────────────────────────────────────────────────

    def map_device(self, context: DeviceContext) -> List[Entity]:
        """
        Map a single DeviceContext to a list of HA Entity objects.

        Looks up the domain mapper for context.device_type in the
        plugin registry, builds DeviceInfo, and delegates to the mapper.

        Returns an empty list if the device type is unknown or if the
        mapper raises an unexpected error. One bad device never prevents
        other devices from loading.

        Args:
            context (DeviceContext): Normalised device context from
                                     device_registry.build_device_contexts().

        Returns:
            List[Entity]: Zero or more HA entities for this device.
                          Empty list if device type is unsupported or
                          mapping fails.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if context
                                 is not a DeviceContext instance.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(context, DeviceContext):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"map_device: context must be DeviceContext, "
                f"got {type(context).__name__}",
            )

        # ── Look up domain mapper ─────────────────────────────────────────
        mapper_fn = DEVICE_TYPE_REGISTRY.get(context.device_type)

        if mapper_fn is None:
            self._metrics.increment(MetricName.MAPPING_UNKNOWN_DEVICE_TYPE)
            self._metrics.increment(MetricName.MAPPING_ERRORS)
            logger.warning(
                "map_device: no mapper registered for deviceType '%s' "
                "(logical_id=%s, name='%s') — skipping",
                context.device_type,
                context.logical_id,
                context.device_name,
            )
            return []

        # ── Build DeviceInfo ──────────────────────────────────────────────
        try:
            device_info = build_device_info(context)
        except Exception as exc:
            self._metrics.increment(MetricName.MAPPING_ERRORS)
            logger.error(
                "map_device: build_device_info raised for '%s' (logical_id=%s): %s",
                context.device_name,
                context.logical_id,
                exc,
            )
            return []

        if device_info is None:
            self._metrics.increment(MetricName.MAPPING_ERRORS)
            logger.warning(
                "map_device: failed to build DeviceInfo for '%s' "
                "(logical_id=%s) — skipping",
                context.device_name,
                context.logical_id,
            )
            return []

        # ── Delegate to domain mapper ─────────────────────────────────────
        try:
            entities = mapper_fn(context, device_info)

        except Exception as exc:
            self._metrics.increment(MetricName.MAPPING_ERRORS)
            logger.error(
                "map_device: mapper for deviceType '%s' raised an "
                "error for '%s' (logical_id=%s): %s",
                context.device_type,
                context.device_name,
                context.logical_id,
                exc,
            )
            return []

        # ── Update metrics ────────────────────────────────────────────────
        self._metrics.increment(MetricName.MAPPING_DEVICES_PROCESSED)
        self._metrics.increment(
            MetricName.MAPPING_ENTITIES_CREATED,
            amount=max(len(entities), 1),
        )

        logger.debug(
            "map_device: '%s' (logical_id=%s, device_type=%s) → %d entity(ies)",
            context.device_name,
            context.logical_id,
            context.device_type,
            len(entities),
        )

        return entities

    def map_devices(
        self,
        contexts: List[DeviceContext],
    ) -> List[Entity]:
        """
        Map a list of DeviceContext objects to a flat list of HA
        entities.

        Calls map_device() for each context and concatenates the
        results. Errors in individual mappers are caught and logged
        by map_device() — this method always returns a complete list
        of all successfully mapped entities.

        Args:
            contexts (List[DeviceContext]): List of normalized device
                                            contexts to map.

        Returns:
            List[Entity]: Flat list of all HA entities from all
                          successfully mapped devices.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if contexts
                                 is not a list.
        """

        if not isinstance(contexts, list):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"map_devices: contexts must be a list, got {type(contexts).__name__}",
            )

        all_entities: List[Entity] = []

        for context in contexts:
            entities = self.map_device(context)
            all_entities.extend(entities)

        logger.info(
            "map_devices: mapped %d context(s) → %d total entity(ies)",
            len(contexts),
            len(all_entities),
        )

        return all_entities

    @staticmethod
    def supported_device_types() -> List[str]:
        """
        Return a sorted list of all registered deviceType strings.

        Used for logging and diagnostics — the orchestrator logs this
        at startup so it is clear which device types are supported.

        Returns:
            List[str]: Sorted list of supported Dirigera deviceType
                       strings.
        """

        return sorted(DEVICE_TYPE_REGISTRY.keys())


# ── Module-level helpers ───────────────────────────────────────────────────────
#
# build_device_info() is a standalone function, not a DeviceMapper method,
# matching HA-MQTT-SDK's own convention (create_device_info, create_entity,
# build_registration are all free functions in the SDK, called by thin
# class methods rather than implemented as the methods themselves). This
# also makes it independently patchable in tests via
# patch("app.mapping.device_mapper.build_device_info", ...) — a bound
# staticmethod cannot be intercepted that way since callers reach it
# through self, not through the module namespace.


def build_device_info(context: DeviceContext) -> DeviceInfo | None:
    """
    Build an HASDK DeviceInfo instance for a DeviceContext.

    Uses context.serial_number as the physical device identifier so
    all sibling logical devices of the same physical device are
    grouped under one device entry in the HA device registry.

    create_device_info() is keyword-only and does no implicit field
    renaming, so the mapping from DeviceContext fields to DeviceInfo
    fields is done explicitly here:
        serial_number    → identifiers=[("dirigera", value)]
                           (also passed through as serial_number)
        device_name      → name
        manufacturer     → manufacturer
        model            → model
        firmware_version → sw_version (omitted if None/empty)
        room_name        → suggested_area (omitted if None)

    Args:
        context (DeviceContext): Device context to build info for.

    Returns:
        DeviceInfo | None: Built DeviceInfo, or None if building
                           fails (caller handles the None case).
    """

    try:
        device_info = create_device_info(
            identifiers=[("dirigera", context.serial_number)],
            name=context.device_name,
            manufacturer=context.manufacturer,
            model=context.model,
            sw_version=context.firmware_version or None,
            suggested_area=context.room_name,
            serial_number=context.serial_number,
        )

        logger.debug(
            "build_device_info: built DeviceInfo for '%s' (relation_id=%s, serial=%s)",
            context.device_name,
            context.relation_id,
            context.serial_number,
        )

        return device_info

    except Exception as exc:
        logger.error(
            "build_device_info: failed to build DeviceInfo for "
            "'%s' (logical_id=%s): %s",
            context.device_name,
            context.logical_id,
            exc,
        )
        return None
