"""
outlet.py

Home Assistant entity mapper for Dirigera smart plug / outlet devices.

Role & Responsibility:
    Maps a Dirigera outlet DeviceContext to a list of HA entities
    that expose the full feature set of IKEA smart plugs (INSPELNING).

    The outlet is a multi-entity device — one Dirigera logical device
    produces multiple HA entities covering power control and energy
    monitoring. Each entity is optional: it is only created if the
    corresponding attribute is present in the device's attributes,
    making the mapper forward-compatible with future plug variants
    that may add or remove measurements.

What it does:
    Produces the following HA entities from a single outlet device:

    switch  — on/off control               (always created)
    sensor  — currentActivePower  (W)      (if present in attributes)
    sensor  — currentVoltage      (V)      (if present in attributes)
    sensor  — currentAmps         (A)      (if present in attributes)
    sensor  — totalEnergyConsumed (kWh)    (if present in attributes)

    From real INSPELNING (E2206) discovery data:
        isOn, currentActivePower, currentVoltage, currentAmps,
        totalEnergyConsumed are all present.

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_outlet())

Not responsible for:
    - State updates (state_mapper.py)
    - Command translation (command_mapper.py)
    - MQTT publishing (ha_client.py)

Design notes:
    - The primary entity is a HADomain.SWITCH for on/off control.
    - Energy sensor entities use HADomain.SENSOR with appropriate
      device_class, unit_of_measurement, and state_class fields.
    - state_class 'measurement' is used for instantaneous values
      (power, voltage, current). state_class 'total_increasing' is
      used for totalEnergyConsumed because it is a monotonically
      increasing cumulative counter that HA uses for energy dashboard
      integration. This is important — using 'measurement' for energy
      would break the HA energy dashboard.
    - The switch entity carries payload_on/payload_off as 'ON'/'OFF'
      to match the MQTT bridge's state payload convention.
    - lightLevel in the outlet attributes is an internal Dirigera field
      (brightness of the status LED) — it is not exposed to HA as it
      has no meaningful user value for a plug.
    - childLock and statusLight are receivable commands but are
      not currently mapped as entities — they can be added as
      switch entities in a future iteration if needed.
"""

from __future__ import annotations

import logging
from typing import List

from ..device_registry import DeviceContext
from ha_mqtt_sdk import HADomain
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo

from . import make_unique_id

__all__ = [
    "DEVICE_TYPES",
    "map_outlet",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_IS_ON = "isOn"
_ATTR_CURRENT_ACTIVE_POWER = "currentActivePower"
_ATTR_CURRENT_VOLTAGE = "currentVoltage"
_ATTR_CURRENT_AMPS = "currentAmps"
_ATTR_TOTAL_ENERGY_CONSUMED = "totalEnergyConsumed"


def map_outlet(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera outlet DeviceContext to a list of HA entities.

    Always produces one switch entity for on/off control.
    Conditionally produces up to four sensor entities for power,
    voltage, current, and energy consumption based on the attributes
    present in the device payload.

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: Between 1 and 5 entities depending on which
                      energy monitoring attributes are present.
    """

    lid = context.logical_id
    attrs = context.attributes
    name = context.device_name

    logger.debug(
        "map_outlet: mapping outlet '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [_make_switch(lid, name, device_info)]

    # ── Switch entity (always present) ────────────────────────────────────

    # ── Energy monitoring sensors (conditional) ───────────────────────────
    if attrs.get(_ATTR_CURRENT_ACTIVE_POWER) is not None:
        entities.append(_make_power_sensor(lid, name, device_info))

    if attrs.get(_ATTR_CURRENT_VOLTAGE) is not None:
        entities.append(_make_voltage_sensor(lid, name, device_info))

    if attrs.get(_ATTR_CURRENT_AMPS) is not None:
        entities.append(_make_current_sensor(lid, name, device_info))

    if attrs.get(_ATTR_TOTAL_ENERGY_CONSUMED) is not None:
        entities.append(_make_energy_sensor(lid, name, device_info))

    logger.info(
        "map_outlet: mapped outlet '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_switch(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create the primary switch entity for on/off control.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name (used as entity name).
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured switch entity.
    """

    return Entity(
        domain=HADomain.SWITCH,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "payload_on": "ON",
            "payload_off": "OFF",
        },
    )


def _make_power_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for current active power consumption.

    Maps to currentActivePower in Dirigera attributes.
    HA device_class 'power', unit 'W', state_class 'measurement'.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured power sensor.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Power",
        unique_id=make_unique_id(logical_id, "power"),
        device_info=device_info,
        extra={
            "device_class": "power",
            "unit_of_measurement": "W",
            "state_class": "measurement",
        },
    )


def _make_voltage_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for current voltage.

    Maps to currentVoltage in Dirigera attributes.
    HA device_class 'voltage', unit 'V', state_class 'measurement'.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured voltage sensor.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Voltage",
        unique_id=make_unique_id(logical_id, "voltage"),
        device_info=device_info,
        extra={
            "device_class": "voltage",
            "unit_of_measurement": "V",
            "state_class": "measurement",
        },
    )


def _make_current_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for current amperage draw.

    Maps to currentAmps in Dirigera attributes.
    HA device_class 'current', unit 'A', state_class 'measurement'.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured current sensor.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Current",
        unique_id=make_unique_id(logical_id, "current"),
        device_info=device_info,
        extra={
            "device_class": "current",
            "unit_of_measurement": "A",
            "state_class": "measurement",
        },
    )


def _make_energy_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for total energy consumed.

    Maps to totalEnergyConsumed in Dirigera attributes.
    HA device_class 'energy', unit 'kWh'.

    Uses state_class 'total_increasing' — NOT 'measurement' — because
    totalEnergyConsumed is a monotonically increasing cumulative counter.
    Home Assistant uses state_class 'total_increasing' to correctly
    integrate this value into the HA Energy dashboard. Using
    'measurement' would break energy tracking.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured energy sensor.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Energy",
        unique_id=make_unique_id(logical_id, "energy"),
        device_info=device_info,
        extra={
            "device_class": "energy",
            "unit_of_measurement": "kWh",
            "state_class": "total_increasing",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to this module's mapper function.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "outlet": map_outlet,
}
