"""
app/mapping/domains/__init__.py

Shared helpers and plugin registry for domain mappers.

Role & Responsibility:
    This package initializer serves two distinct purposes:

    1. Shared helper functions used by every domain mapper:
       - make_battery_entity() produces a standardized HA battery
         sensor entity for any device that reports batteryPercentage.
         No domain mapper reimplements this — they all call this one
         function.
       - make_unique_id() produces a consistent, collision-free
         unique_id string for any entity registered in HA.

    2. Plugin registry: the DEVICE_TYPE_REGISTRY dict maps Dirigera
       deviceType strings to the domain mapper function responsible
       for handling that device type. The device_mapper.py module
       reads this registry — adding support for a new device type
       means adding one file in this package and one entry here.
       Nothing else changes.

What it does:
    - Defines the DomainMapper protocol: the interface every domain
      mapper function must satisfy
    - Defines make_battery_entity() shared helper
    - Defines make_unique_id() shared helper
    - Exports DEVICE_TYPE_REGISTRY mapping deviceType → mapper function

Arguments / Configuration:
    No runtime configuration. All functions are pure — they take a
    DeviceContext and return List[Entity].

Used by:
    - app/mapping/device_mapper.py  (reads DEVICE_TYPE_REGISTRY,
                                     calls make_battery_entity)
    - All domain mapper files        (call make_battery_entity,
                                     make_unique_id)

Not responsible for:
    - Any network I/O
    - State caching or discovery caching
    - MQTT publishing

Design notes:
    - DEVICE_TYPE_REGISTRY is populated at module load time by
      importing each domain module. Import errors in a single domain
      module are caught and logged so one broken mapper cannot prevent
      the entire application from starting.
    - The DomainMapper Protocol is used for type checking only —
      domain mapper functions do not need to inherit from anything.
    - make_unique_id() uses the logical_id (not relation_id) as the
      base so each logical device's entities have unique ids even when
      siblings share a relation_id. The suffix disambiguate multiple
      entities from the same logical device (e.g. outlet has switch +
      4 sensors).
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List

from ..device_registry import DeviceContext
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo
from ha_mqtt_sdk import HADomain

from ...core.errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "DomainMapper",
    "DEVICE_TYPE_REGISTRY",
    "make_battery_entity",
    "make_unique_id",
]

logger = logging.getLogger(__name__)


# ── Type alias ────────────────────────────────────────────────────────────────

# A domain mapper is any callable that accepts a DeviceContext and
# returns a list of Entity objects. We use a Callable type alias
# rather than a Protocol to keep it simple — duck typing is enough.
#
# Import is deferred to avoid a circular import: DeviceContext is
# defined in device_registry.py which imports from this package.
# The type alias is only used for documentation and IDE support.
DomainMapper = Callable[[DeviceContext, DeviceInfo], List[Entity]]


# ── Shared helpers ────────────────────────────────────────────────────────────


def make_unique_id(logical_id: str, suffix: str = "") -> str:
    """
    Produce a consistent, collision-free unique_id string for an HA
    entity.

    The unique_id is used by Home Assistant to identify entities across
    restarts and to deduplicate discovery payloads. It must be:
    - Globally unique within a single HA instance
    - Stable across bridge restarts (same device always gets same id)
    - Human-readable enough to diagnose issues in HA logs

    Format:
        dirigera_{logical_id}               for a single entity
        dirigera_{logical_id}_{suffix}      for multiple entities from
                                            the same logical device

    Args:
        logical_id (str): Dirigera logical device id
                          (e.g. 'fff75d00-607c-4f23-a0e7-3dbed0e18b12_1').
                          Must be a non-empty string.
        suffix (str):     Disambiguating suffix for devices that produce
                          multiple HA entities (e.g. 'battery', 'power',
                          'voltage', 'energy'). Empty string for the
                          primary entity.

    Returns:
        str: Unique id string safe for use in MQTT topics and HA.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                             is not a non-empty string.

    Examples:
        make_unique_id('fff75d00_1')           → 'dirigera_fff75d00_1'
        make_unique_id('fff75d00_1', 'battery') → 'dirigera_fff75d00_1_battery'
        make_unique_id('abc_1', 'power')       → 'dirigera_abc_1_power'
    """

    if not isinstance(logical_id, str) or not logical_id.strip():
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"make_unique_id: logical_id must be a non-empty string, "
            f"got {logical_id!r}",
        )

    # Replace hyphens with underscores for MQTT topic safety
    safe_id = logical_id.replace("-", "_")

    if suffix:
        if not isinstance(suffix, str) or not suffix.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"make_unique_id: suffix must be a non-empty string "
                f"if provided, got {suffix!r}",
            )
        return f"dirigera_{safe_id}_{suffix.strip()}"

    return f"dirigera_{safe_id}"


def make_battery_entity(
    logical_id: str,
    device_name: str,
    device_info: DeviceInfo,
    battery_pct: int,
) -> Entity:
    """
    Create a standardized HA battery sensor entity.

    Called by any domain mapper where the device reports a
    batteryPercentage attribute. All battery entities across all
    device types are created identically by this one function.

    The entity is a sensor with:
        - domain:       HADomain.SENSOR
        - device_class: battery
        - unit:         %
        - entity_category: diagnostic (shown under device diagnostics
                           in HA, not as a primary entity)

    Args:
        logical_id (str):    Dirigera logical device id. Used to build
                             the unique_id with suffix 'battery'.
        device_name (str):   Human-readable device name. Used as the
                             entity name prefix.
        device_info (DeviceInfo): HASDK DeviceInfo instance for the
                             physical device. Groups this entity with
                             its siblings in the HA device registry.
        battery_pct (int):   Current battery percentage (0–100).
                             Used to set the initial state value.

    Returns:
        Entity: Configured battery sensor entity ready for registration.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                             or device_name are not non-empty strings,
                             or if battery_pct is not in 0–100 range.
    """

    # ── Validation ────────────────────────────────────────────────────────
    if not isinstance(logical_id, str) or not logical_id.strip():
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            "make_battery_entity: logical_id must be a non-empty string",
        )

    if not isinstance(device_name, str) or not device_name.strip():
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            "make_battery_entity: device_name must be a non-empty string",
        )

    if not isinstance(device_info, dict):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"make_battery_entity: device_info must be DeviceInfo (dict), "
            f"got {type(device_info).__name__}",
        )

    if not isinstance(battery_pct, int) or not (0 <= battery_pct <= 100):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"make_battery_entity: battery_pct must be int 0–100, got {battery_pct!r}",
        )

    # ── Build entity ──────────────────────────────────────────────────────
    unique_id = make_unique_id(logical_id, suffix="battery")
    entity_name = f"{device_name} Battery"

    entity = Entity(
        domain=HADomain.SENSOR,
        name=entity_name,
        unique_id=unique_id,
        device_info=device_info,
        extra={
            "device_class": "battery",
            "unit_of_measurement": "%",
            "entity_category": "diagnostic",
            "state_class": "measurement",
        },
    )

    logger.debug(
        "make_battery_entity: created battery entity for '%s' (unique_id=%s, pct=%d)",
        device_name,
        unique_id,
        battery_pct,
    )

    return entity


# ── Plugin registry ───────────────────────────────────────────────────────────
#
# Maps Dirigera deviceType strings → domain mapper functions.
# Each mapper function has the signature:
#
#     def map_<type>(context: DeviceContext, device_info: DeviceInfo)
#                   -> List[Entity]
#
# Populated below by importing each domain module. Import errors for
# individual modules are caught so one broken mapper does not prevent
# the application from starting — it logs a warning and that device
# type is skipped during mapping.

DEVICE_TYPE_REGISTRY: Dict[str, DomainMapper] = {}


def _register_mappers() -> None:
    """
    Import each domain module and register its mapper function(s) in
    DEVICE_TYPE_REGISTRY.

    Called once at module load time. Each domain module exposes a
    DEVICE_TYPES dict mapping deviceType strings to mapper callables.
    This function merges all of them into the single DEVICE_TYPE_REGISTRY.

    A module that fails to import is logged as a warning — its device
    types will be unregistered and devices of those types will be
    skipped with a MAPPING_UNKNOWN_DEVICE_TYPE warning during operation.
    """

    # Each entry: (module_path, human_readable_name)
    _DOMAIN_MODULES = [
        (".gateway", "gateway"),
        (".light", "light"),
        (".outlet", "outlet"),
        (".binary_sensor", "binary_sensor"),
        (".sensor", "sensor"),
        (".environment_sensor", "environment_sensor"),
        (".remote", "remote / lightController"),
        (".blind", "blind / cover"),
        (".switch", "switch"),
        (".button", "button"),
        (".air_purifier", "air_purifier"),
        (".speaker", "speaker"),
    ]

    for module_path, name in _DOMAIN_MODULES:
        try:
            import importlib

            module = importlib.import_module(
                module_path,
                package=__name__,
            )

            if not hasattr(module, "DEVICE_TYPES"):
                logger.warning(
                    "Domain module '%s' has no DEVICE_TYPES dict — "
                    "skipping registration",
                    name,
                )
                continue

            device_types: Dict[str, DomainMapper] = module.DEVICE_TYPES

            for device_type, mapper_fn in device_types.items():
                if device_type in DEVICE_TYPE_REGISTRY:
                    logger.warning(
                        "Device type '%s' already registered "
                        "(from a previous module) — "
                        "overwriting with mapper from '%s'",
                        device_type,
                        name,
                    )

                DEVICE_TYPE_REGISTRY[device_type] = mapper_fn

                logger.debug(
                    "Registered mapper for deviceType '%s' from module '%s'",
                    device_type,
                    name,
                )

            logger.debug(
                "Domain module '%s' registered %d device type(s): %s",
                name,
                len(device_types),
                sorted(device_types.keys()),
            )

        except ImportError as exc:
            import importlib

            logger.warning(
                "Failed to import domain module '%s' — "
                "device types from this module will not be mapped: %s",
                name,
                exc,
            )

        except Exception as exc:
            logger.warning(
                "Unexpected error registering domain module '%s': %s",
                name,
                exc,
            )


# Populate the registry when this package is first imported
_register_mappers()

logger.debug(
    "Domain mapper registry loaded: %d device type(s) registered: %s",
    len(DEVICE_TYPE_REGISTRY),
    sorted(DEVICE_TYPE_REGISTRY.keys()),
)
