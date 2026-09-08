"""
electrical_sensor.py

Home Assistant entity mapper for Dirigera electricalSensor devices.

The IKEA GRILLPLATS plug exposes its electrical measurements through a
separate Dirigera deviceType named "electricalSensor". This mapper
creates the corresponding Home Assistant sensor entities.
"""

from __future__ import annotations

import logging

from ha_mqtt_sdk import DeviceInfo, Entity, HADomain

from ..device_registry import DeviceContext
from . import make_unique_id

__all__ = [
    "DEVICE_TYPES",
    "map_electrical_sensor",
]

logger = logging.getLogger(__name__)

_ATTR_CURRENT_ACTIVE_POWER = "currentActivePower"
_ATTR_CURRENT_VOLTAGE = "currentVoltage"
_ATTR_CURRENT_AMPS = "currentAmps"
_ATTR_TOTAL_ENERGY_CONSUMED = "totalEnergyConsumed"


def map_electrical_sensor(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> list[Entity]:
    """
    Map a Dirigera electricalSensor to Home Assistant sensors.

    Creates up to four sensors:
      - Power
      - Voltage
      - Current
      - Energy
    """

    lid = context.logical_id
    attrs = context.attributes
    name = context.device_name

    logger.debug(
        "map_electrical_sensor: mapping electricalSensor '%s' "
        "(logical_id=%s)",
        name,
        lid,
    )

    entities: list[Entity] = []

    if attrs.get(_ATTR_CURRENT_ACTIVE_POWER) is not None:
        entities.append(_make_power_sensor(lid, name, device_info))

    if attrs.get(_ATTR_CURRENT_VOLTAGE) is not None:
        entities.append(_make_voltage_sensor(lid, name, device_info))

    if attrs.get(_ATTR_CURRENT_AMPS) is not None:
        entities.append(_make_current_sensor(lid, name, device_info))

    if attrs.get(_ATTR_TOTAL_ENERGY_CONSUMED) is not None:
        entities.append(_make_energy_sensor(lid, name, device_info))

    logger.debug(
        "map_electrical_sensor: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


def _make_power_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
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


DEVICE_TYPES = {
    "electricalSensor": map_electrical_sensor,
}
