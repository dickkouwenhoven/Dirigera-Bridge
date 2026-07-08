"""
binary_sensor.py

Home Assistant entity mapper for Dirigera binary sensor devices.

Role & Responsibility:
    Maps Dirigera binary sensor DeviceContexts to lists of HA entities.
    Handles all device types whose primary state is a boolean
    (detected / not detected, wet / dry) and whose HA representation
    is one or more binary_sensor entities.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        motionSensor  — VALLHORN Wireless Motion Sensor
                        Primary: binary_sensor (device_class: motion)
                        Secondary: sensor (device_class: battery)

        waterSensor   — BADRING Water Leakage Sensor
                        Primary: binary_sensor (device_class: moisture)
                        Secondary: sensor (device_class: battery)

What it does:
    For motionSensor:
        - Creates a binary_sensor entity with device_class: motion
        - Creates a battery sensor entity if batteryPercentage present

    For waterSensor:
        - Creates a binary_sensor entity with device_class: moisture
        - Creates a battery sensor entity if batteryPercentage present

    Both device types share the same structural pattern — they differ
    only in the HA device_class and the Dirigera attribute that holds
    the boolean state.

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_motion_sensor()
                                        or map_water_sensor())

Not responsible for:
    - State updates (state_mapper.py reads isDetected / waterLeakDetected)
    - Command translation (these are read-only sensors)
    - MQTT publishing (ha_client.py)

Design notes:
    - motionSensor state attribute: 'isDetected' (bool)
    - waterSensor state attribute:  'waterLeakDetected' (bool)
    - Both use payload_on='ON', payload_off='OFF' to match the
      bridge's binary state payload convention.
    - Battery entities are created by the shared make_battery_entity()
      helper from domains/__init__.py.
    - The motionSensor also has an 'isOn' attribute (whether the sensor
      is active/enabled) but this is not exposed as a separate entity —
      it is an internal Dirigera scheduling concept, not a user-facing
      state. The meaningful state for HA automations is isDetected.
"""

from __future__ import annotations

import logging
from typing import List

from ..device_registry import DeviceContext
from ha_mqtt_sdk import HADomain
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo

from . import make_unique_id, make_battery_entity

__all__ = [
    "DEVICE_TYPES",
    "map_motion_sensor",
    "map_water_sensor",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_IS_DETECTED = "isDetected"
_ATTR_WATER_LEAK_DETECTED = "waterLeakDetected"
_ATTR_BATTERY_PERCENTAGE = "batteryPercentage"


def map_motion_sensor(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera motionSensor DeviceContext to HA entities.

    Produces:
        - binary_sensor with device_class: motion
        - sensor with device_class: battery  (if batteryPercentage
                                               is present)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: 1 entity (motion only) or 2 entities
                      (motion + battery).
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_motion_sensor: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_binary_sensor(
            logical_id=lid,
            name=name,
            device_info=device_info,
            device_class="motion",
        )
    ]

    # ── Primary: motion binary sensor ────────────────────────────────────

    # ── Secondary: battery sensor ─────────────────────────────────────────
    battery_pct = context.attributes.get(_ATTR_BATTERY_PERCENTAGE)
    if battery_pct is not None:
        entities.append(
            make_battery_entity(
                logical_id=lid,
                device_name=name,
                device_info=device_info,
                battery_pct=int(battery_pct),
            )
        )

    logger.info(
        "map_motion_sensor: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


def map_water_sensor(
    context: "DeviceContext",
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera waterSensor DeviceContext to HA entities.

    Produces:
        - binary_sensor with device_class: moisture
        - sensor with device_class: battery  (if batteryPercentage
                                               is present)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: 1 entity (moisture only) or 2 entities
                      (moisture + battery).
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_water_sensor: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_binary_sensor(
            logical_id=lid,
            name=name,
            device_info=device_info,
            device_class="moisture",
        )
    ]

    # ── Primary: moisture binary sensor ──────────────────────────────────

    # ── Secondary: battery sensor ─────────────────────────────────────────
    battery_pct = context.attributes.get(_ATTR_BATTERY_PERCENTAGE)
    if battery_pct is not None:
        entities.append(
            make_battery_entity(
                logical_id=lid,
                device_name=name,
                device_info=device_info,
                battery_pct=int(battery_pct),
            )
        )

    logger.info(
        "map_water_sensor: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_binary_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
    device_class: str,
) -> Entity:
    """
    Create a binary_sensor entity with the given HA device_class.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable entity name.
        device_info (DeviceInfo): Physical device grouping info.
        device_class (str):  HA binary_sensor device_class string.
                             Examples: 'motion', 'moisture'.

    Returns:
        Entity: Configured binary sensor entity.
    """

    return Entity(
        domain=HADomain.BINARY_SENSOR,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "device_class": device_class,
            "payload_on": "ON",
            "payload_off": "OFF",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "motionSensor": map_motion_sensor,
    "waterSensor": map_water_sensor,
}
