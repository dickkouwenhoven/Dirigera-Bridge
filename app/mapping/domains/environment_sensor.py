"""
environment_sensor.py

Home Assistant entity mapper for the Dirigera environment sensor
(VINDSTYRKA air quality sensor, deviceType: environmentSensor).

Role & Responsibility:
    Maps a Dirigera environmentSensor DeviceContext to a list of four
    HA sensor entities — one for each measurement the VINDSTYRKA
    reports. This is a Challenge-A multi-entity device: one Dirigera
    logical id produces multiple HA entities.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        environmentSensor — VINDSTYRKA Air Quality Sensor (E2112)
                            Produces 4 sensor entities:
                              temperature  (°C)
                              humidity     (%)
                              PM2.5        (µg/m³)
                              VOC index    (index)

What it does:
    - Creates a temperature sensor entity  (device_class: temperature)
    - Creates a humidity sensor entity     (device_class: humidity)
    - Creates a PM2.5 sensor entity        (device_class: pm25)
    - Creates a VOC index sensor entity    (device_class: volatile_organic_compounds_parts)
    - Each entity is conditional — only created if its corresponding
      attribute is present in the device payload

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_environment_sensor())

Not responsible for:
    - State updates (state_mapper.py reads each measurement attribute)
    - Command translation (read-only sensor)
    - MQTT publishing (ha_client.py)

Design notes:
    - VINDSTYRKA has no relationId — it is a single logical device
      (Challenge A, not Challenge B). All four entities share the same
      logical_id and therefore the same device_info.
    - Attribute names from real VINDSTYRKA discovery data:
        currentTemperature  → float, degrees Celsius
        currentRH           → float, relative humidity %
        currentPM25         → float, µg/m³
        vocIndex            → int, VOC index (dimensionless 1–500)
    - HA device_class for VOC index is
      'volatile_organic_compounds_parts' (added in HA 2023.x).
      This uses the unitless VOC index (1–500 Sensirion scale),
      not a concentration in ppb. The unit_of_measurement is left
      empty for index values.
    - state_class 'measurement' is correct for all four — they are
      instantaneous readings, not cumulative counters.
    - The VINDSTYRKA has no battery (mains powered) so no battery
      entity is created.
    - min/max PM2.5 range from real data: minMeasuredPM25=0,
      maxMeasuredPM25=999. These are not used in HA entity config
      but are available in raw_attributes for reference.
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
    "map_environment_sensor",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_TEMPERATURE = "currentTemperature"
_ATTR_HUMIDITY = "currentRH"
_ATTR_PM25 = "currentPM25"
_ATTR_VOC_INDEX = "vocIndex"


def map_environment_sensor(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera environmentSensor DeviceContext to HA entities.

    Produces up to four sensor entities — one per measurement.
    Each entity is only created if its corresponding attribute is
    present in the device attributes, making the mapper forward
    compatible with future VINDSTYRKA variants.

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: Between 0 and 4 sensor entities depending on
                      which attributes are present. For the current
                      VINDSTYRKA (E2112) all four are always produced.
    """

    lid = context.logical_id
    name = context.device_name
    attrs = context.attributes

    logger.debug(
        "map_environment_sensor: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = []

    # ── Temperature ───────────────────────────────────────────────────────
    if attrs.get(_ATTR_TEMPERATURE) is not None:
        entities.append(_make_temperature_sensor(lid, name, device_info))

    # ── Humidity ──────────────────────────────────────────────────────────
    if attrs.get(_ATTR_HUMIDITY) is not None:
        entities.append(_make_humidity_sensor(lid, name, device_info))

    # ── PM2.5 ─────────────────────────────────────────────────────────────
    if attrs.get(_ATTR_PM25) is not None:
        entities.append(_make_pm25_sensor(lid, name, device_info))

    # ── VOC index ─────────────────────────────────────────────────────────
    if attrs.get(_ATTR_VOC_INDEX) is not None:
        entities.append(_make_voc_sensor(lid, name, device_info))

    logger.info(
        "map_environment_sensor: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_temperature_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for ambient temperature.

    Maps to 'currentTemperature' in Dirigera attributes.
    HA device_class: temperature, unit: °C, state_class: measurement.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured temperature sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Temperature",
        unique_id=make_unique_id(logical_id, "temperature"),
        device_info=device_info,
        extra={
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "state_class": "measurement",
        },
    )


def _make_humidity_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for relative humidity.

    Maps to 'currentRH' in Dirigera attributes.
    HA device_class: humidity, unit: %, state_class: measurement.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured humidity sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Humidity",
        unique_id=make_unique_id(logical_id, "humidity"),
        device_info=device_info,
        extra={
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "state_class": "measurement",
        },
    )


def _make_pm25_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for PM2.5 particulate matter concentration.

    Maps to 'currentPM25' in Dirigera attributes.
    HA device_class: pm25, unit: µg/m³, state_class: measurement.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured PM2.5 sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} PM2.5",
        unique_id=make_unique_id(logical_id, "pm25"),
        device_info=device_info,
        extra={
            "device_class": "pm25",
            "unit_of_measurement": "µg/m³",
            "state_class": "measurement",
        },
    )


def _make_voc_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for VOC (volatile organic compounds) index.

    Maps to 'vocIndex' in Dirigera attributes.

    HA device_class: volatile_organic_compounds_parts.
    The VOC index from the VINDSTYRKA is a dimensionless index value
    on the Sensirion 1–500 scale, not a concentration in ppb. The
    unit_of_measurement is intentionally omitted — HA accepts an
    empty string for dimensionless index values, which renders
    correctly on the dashboard without a misleading unit suffix.

    state_class: measurement (instantaneous index reading).

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured VOC index sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} VOC Index",
        unique_id=make_unique_id(logical_id, "voc"),
        device_info=device_info,
        extra={
            "device_class": "volatile_organic_compounds_parts",
            "state_class": "measurement",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "environmentSensor": map_environment_sensor,
}
