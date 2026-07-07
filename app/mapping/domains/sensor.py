"""
sensor.py

Home Assistant entity mapper for Dirigera single-value sensor devices.

Role & Responsibility:
    Maps Dirigera sensor DeviceContexts whose primary output is a
    single numeric measurement to the appropriate HA sensor entity.

    This module handles device types that produce one primary sensor
    reading plus an optional battery entity. Multi-reading sensors
    (like VINDSTYRKA with temperature + humidity + PM2.5 + VOC) are
    handled by environment_sensor.py instead.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        lightSensor — VALLHORN light sensor sibling
                      Primary: sensor (device_class: illuminance, lx)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_light_sensor())

Not responsible for:
    - State updates (state_mapper.py reads the illuminance value)
    - Command translation (read-only sensor)
    - MQTT publishing (ha_client.py)
    - Multi-attribute sensors (environment_sensor.py handles those)

Design notes:
    - The VALLHORN lightSensor (_3 sibling) has deviceType 'lightSensor'
      and attribute 'illuminance' (integer, unit lux).
    - The lightSensor sibling has type='unknown' in Dirigera and no
      room or customName — these are inherited from the primary sibling
      (_1 motionSensor) by device_registry.py before the context
      reaches this mapper.
    - state_class 'measurement' is correct for illuminance — it is an
      instantaneous reading, not a cumulative counter.
    - The lightSensor sibling typically has no batteryPercentage in its
      own attributes block (battery is reported on the _1 sibling).
      The battery entity is therefore created by map_motion_sensor()
      in binary_sensor.py — not here. The battery check is still
      included defensively in case future firmware changes this.
    - This module is designed to be extended: additional single-value
      sensor deviceTypes can be added as new mapper functions and
      registered in DEVICE_TYPES without touching any other file.
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
    "map_light_sensor",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_ILLUMINANCE = "illuminance"
_ATTR_BATTERY_PERCENTAGE = "batteryPercentage"


def map_light_sensor(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera lightSensor DeviceContext to HA entities.

    Produces:
        - sensor with device_class: illuminance, unit: lx
        - sensor with device_class: battery  (if batteryPercentage
                                               present — defensive,
                                               normally on _1 sibling)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py. device_name and
                                  room_name are inherited from the
                                  primary motionSensor sibling by
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA. Uses the shared
                                  relation_id so this entity appears
                                  under the same physical device as
                                  the motionSensor sibling.

    Returns:
        List[Entity]: 1 entity (illuminance) or 2 entities
                      (illuminance + battery).
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_light_sensor: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_illuminance_sensor(
            logical_id=lid,
            name=name,
            device_info=device_info,
        )
    ]

    # ── Primary: illuminance sensor ───────────────────────────────────────

    # ── Secondary: battery sensor (defensive — normally on _1 sibling) ────
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
        "map_light_sensor: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_illuminance_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for ambient light level (illuminance).

    Maps to the 'illuminance' attribute in Dirigera.
    HA device_class 'illuminance', unit 'lx', state_class 'measurement'.

    The entity name uses the device_name inherited from the primary
    sibling (e.g. 'Bewegingssensor Gang') with 'Illuminance' appended
    so it is clearly distinguishable from the motion entity in the HA
    UI while still being grouped under the same physical device.

    Args:
        logical_id (str):    Dirigera logical device id (the _3 sibling).
        name (str):          Inherited device name from primary sibling.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured illuminance sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Illuminance",
        unique_id=make_unique_id(logical_id, "illuminance"),
        device_info=device_info,
        extra={
            "device_class": "illuminance",
            "unit_of_measurement": "lx",
            "state_class": "measurement",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "lightSensor": map_light_sensor,
}
