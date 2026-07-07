"""
air_purifier.py

Home Assistant entity mapper for Dirigera air purifier devices.

Role & Responsibility:
    Maps Dirigera airPurifier DeviceContexts to HA entities that
    expose the fan control and air quality monitoring capabilities
    of IKEA STARKVIND air purifiers.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        airPurifier — IKEA STARKVIND Air Purifier (E2007)

What it does:
    Produces the following HA entities per air purifier device:

    fan    — fan speed control          (primary — on/off + speed %)
    sensor — PM2.5 concentration (µg/m³) (if fanSensorPM25 present)
    sensor — filter life remaining (%)   (if filterLifetime present)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_air_purifier())

Not responsible for:
    - State updates (state_mapper.py reads fanMode / currentPM25)
    - Command translation (command_mapper.py maps speed % to
      Dirigera fanMode strings: 'off', 'auto', 'low', 'medium',
      'high', 'customSpeed')
    - MQTT publishing (ha_client.py)

Design notes:
    - HADomain.FAN is the correct HA domain for air purifiers —
      HA does not have a dedicated air_purifier domain in MQTT
      discovery as of 2024. FAN with percentage speed is the
      standard approach used by other integrations.
    - Dirigera fan speed attribute: 'fanMode' (string enum):
        'off', 'auto', 'low', 'medium', 'high', 'customSpeed'
      HA FAN uses percentage (0-100). The translation between
      fanMode strings and percentages is handled in state_mapper.py
      and command_mapper.py — NOT here.
    - The fan entity is configured with speed_range_min=1 and
      speed_range_max=100 to enable percentage-based speed control
      in HA. The 'auto' mode is handled separately via a preset.
    - fan_modes / preset_modes for the STARKVIND:
        'auto'   — automatic mode based on air quality
        'sleep'  — quiet sleep mode (some models)
      These are exposed as preset_modes in the HA FAN entity.
    - PM2.5 from the STARKVIND uses attribute 'fanSensorPM25'
      (different from VINDSTYRKA's 'currentPM25').
    - filterLifetime is reported as remaining percentage (0-100).
      HA renders it as a sensor with unit '%'.
    - The STARKVIND is mains powered — no battery entity.
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
    "map_air_purifier",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_FAN_MODE = "fanMode"
_ATTR_FAN_SENSOR_PM25 = "fanSensorPM25"
_ATTR_FILTER_LIFETIME = "filterLifetime"
_ATTR_MOTOR_SPEED = "motorSpeed"

# STARKVIND preset modes (mapped from fanMode string values)
_FAN_PRESET_MODES = ["auto"]


def map_air_purifier(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera airPurifier DeviceContext to HA entities.

    Produces:
        - fan entity          for speed control and on/off
        - sensor entity       for PM2.5 (if fanSensorPM25 present)
        - sensor entity       for filter life (if filterLifetime present)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: Between 1 and 3 entities depending on which
                      monitoring attributes are present.
    """

    lid = context.logical_id
    name = context.device_name
    attrs = context.attributes

    logger.debug(
        "map_air_purifier: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_fan(
            logical_id=lid,
            name=name,
            device_info=device_info,
        )
    ]

    # ── Primary: fan entity ───────────────────────────────────────────────

    # ── PM2.5 sensor (STARKVIND built-in air quality sensor) ──────────────
    if attrs.get(_ATTR_FAN_SENSOR_PM25) is not None:
        entities.append(
            _make_pm25_sensor(
                logical_id=lid,
                name=name,
                device_info=device_info,
            )
        )

    # ── Filter lifetime sensor ────────────────────────────────────────────
    if attrs.get(_ATTR_FILTER_LIFETIME) is not None:
        entities.append(
            _make_filter_sensor(
                logical_id=lid,
                name=name,
                device_info=device_info,
            )
        )

    logger.info(
        "map_air_purifier: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_fan(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a fan entity for air purifier speed control.

    Configured with percentage-based speed control (1-100%) and
    preset_modes for special fan modes (auto, sleep).

    The translation between Dirigera fanMode strings and HA percentage
    values is performed in state_mapper.py and command_mapper.py.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable device name shown in HA.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured fan entity.
    """

    return Entity(
        domain=HADomain.FAN,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "payload_on": "ON",
            "payload_off": "OFF",
            "speed_range_min": 1,
            "speed_range_max": 100,
            "preset_modes": _FAN_PRESET_MODES,
            "optimistic": False,
        },
    )


def _make_pm25_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a PM2.5 sensor entity for the STARKVIND built-in sensor.

    Maps to 'fanSensorPM25' in Dirigera attributes (different from
    VINDSTYRKA's 'currentPM25').
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


def _make_filter_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity for the filter remaining lifetime.

    Maps to 'filterLifetime' in Dirigera attributes.
    Reported as a remaining percentage (100% = new, 0% = replace).
    Uses entity_category 'diagnostic' — relevant for maintenance
    but not a primary operational state.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Device name used as entity name prefix.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured filter lifetime sensor entity.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Filter Life",
        unique_id=make_unique_id(logical_id, "filter"),
        device_info=device_info,
        extra={
            "unit_of_measurement": "%",
            "state_class": "measurement",
            "entity_category": "diagnostic",
            "icon": "mdi:air-filter",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "airPurifier": map_air_purifier,
}
