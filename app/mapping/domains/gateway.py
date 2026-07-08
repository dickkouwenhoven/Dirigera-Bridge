"""
gateway.py

Home Assistant entity mapper for the Dirigera gateway (hub) device.

Role & Responsibility:
    Maps a Dirigera gateway DeviceContext to a list of HA entities
    that expose hub health, connectivity, firmware, location, and
    sun schedule information to Home Assistant.

    The gateway is special — it is the hub itself, not a peripheral
    device. Its entities are useful for:
    - Monitoring hub connectivity and health from HA dashboards
    - Driving automations based on sunrise/sunset times reported
      directly by the hub (consistent with Dirigera own scheduling)
    - Tracking when firmware updates are available
    - Providing GPS coordinates to the HA map

What it does:
    Produces the following HA entities from a single gateway device:

    binary_sensor — isReachable       (device_class: connectivity)
    binary_sensor — backendConnected  (device_class: connectivity)
    sensor        — otaStatus         firmware update status string
    sensor        — otaState          firmware update state string
    sensor        — firmwareVersion   current firmware version string
    sensor        — homeState         home/away presence string
    sensor        — timezone          configured timezone string
    sensor        — nextSunrise       (device_class: timestamp)
    sensor        — nextSunset        (device_class: timestamp)
    device_tracker — location         GPS coordinates for HA map

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_gateway())

Not responsible for:
    - State updates (that is state_mapper.py)
    - Command handling (gateway has no controllable attributes)
    - MQTT publishing (that is ha_client.py)

Design notes:
    - The gateway has type='gateway' and deviceType='gateway' in the
      Dirigera discovery output. It has a relationId present even
      though it is a single physical device.
    - isReachable is on the DirigeraDevice model directly, not in
      attributes — it must be read from context.device.is_reachable.
    - coordinates (latitude/longitude) come from the nested
      'coordinates' dict inside attributes. accuracy=-1 is normalized
      to 0 for HA compatibility.
    - nextSunrise / nextSunset are ISO 8601 strings — HA device_class
      'timestamp' handles them natively.
    - backendConnected / homeState are in the attributes block.
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
    "map_gateway",
]

logger = logging.getLogger(__name__)

# Attribute key constants — camelCase as they appear in raw Dirigera JSON
_ATTR_BACKEND_CONNECTED = "backendConnected"
_ATTR_OTA_STATUS = "otaStatus"
_ATTR_OTA_STATE = "otaState"
_ATTR_FIRMWARE_VERSION = "firmwareVersion"
_ATTR_HOME_STATE = "homeState"
_ATTR_TIMEZONE = "timezone"
_ATTR_NEXT_SUNRISE = "nextSunRise"
_ATTR_NEXT_SUNSET = "nextSunSet"
_ATTR_COORDINATES = "coordinates"
_ATTR_IS_ON = "isOn"


def map_gateway(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera gateway DeviceContext to a list of HA entities.

    Args:
        context (DeviceContext):  Normalised device context built by
                                  device_registry.py. Provides typed
                                  access to device fields and raw
                                  attributes.
        device_info (DeviceInfo): HASDK DeviceInfo for the physical
                                  gateway device. Used to group all
                                  entities under one device in HA.

    Returns:
        List[Entity]: All HA entities for the gateway. Between 8 and
                      10 entities depending on which optional attributes
                      are present in the discovery payload.
    """

    entities: List[Entity] = []
    lid = context.logical_id

    logger.debug(
        "map_gateway: mapping gateway device (logical_id=%s)",
        lid,
    )

    # ── Binary sensors ────────────────────────────────────────────────────

    entities.append(
        _make_connectivity_sensor(
            logical_id=lid,
            suffix="reachable",
            name=f"{context.device_name} Reachable",
            device_info=device_info,
        )
    )

    backend = context.attributes.get(_ATTR_BACKEND_CONNECTED)
    if backend is not None:
        entities.append(
            _make_connectivity_sensor(
                logical_id=lid,
                suffix="backend_connected",
                name=f"{context.device_name} Backend Connected",
                device_info=device_info,
            )
        )

    # ── String sensors ────────────────────────────────────────────────────

    ota_status = context.attributes.get(_ATTR_OTA_STATUS)
    if ota_status is not None:
        entities.append(
            _make_string_sensor(
                logical_id=lid,
                suffix="ota_status",
                name=f"{context.device_name} Firmware Update Status",
                device_info=device_info,
                entity_category="diagnostic",
            )
        )

    ota_state = context.attributes.get(_ATTR_OTA_STATE)
    if ota_state is not None:
        entities.append(
            _make_string_sensor(
                logical_id=lid,
                suffix="ota_state",
                name=f"{context.device_name} Firmware Update State",
                device_info=device_info,
                entity_category="diagnostic",
            )
        )

    fw_version = context.attributes.get(_ATTR_FIRMWARE_VERSION)
    if fw_version is not None:
        entities.append(
            _make_string_sensor(
                logical_id=lid,
                suffix="firmware_version",
                name=f"{context.device_name} Firmware Version",
                device_info=device_info,
                entity_category="diagnostic",
            )
        )

    home_state = context.attributes.get(_ATTR_HOME_STATE)
    if home_state is not None:
        entities.append(
            _make_string_sensor(
                logical_id=lid,
                suffix="home_state",
                name=f"{context.device_name} Home State",
                device_info=device_info,
            )
        )

    timezone = context.attributes.get(_ATTR_TIMEZONE)
    if timezone is not None:
        entities.append(
            _make_string_sensor(
                logical_id=lid,
                suffix="timezone",
                name=f"{context.device_name} Timezone",
                device_info=device_info,
                entity_category="diagnostic",
            )
        )

    # ── Timestamp sensors ─────────────────────────────────────────────────

    sunrise = context.attributes.get(_ATTR_NEXT_SUNRISE)
    if sunrise is not None:
        entities.append(
            _make_timestamp_sensor(
                logical_id=lid,
                suffix="next_sunrise",
                name=f"{context.device_name} Next Sunrise",
                device_info=device_info,
            )
        )

    sunset = context.attributes.get(_ATTR_NEXT_SUNSET)
    if sunset is not None:
        entities.append(
            _make_timestamp_sensor(
                logical_id=lid,
                suffix="next_sunset",
                name=f"{context.device_name} Next Sunset",
                device_info=device_info,
            )
        )

    # ── Device tracker (GPS location) ────────────────────────────────────

    coords = context.attributes.get(_ATTR_COORDINATES)
    if isinstance(coords, dict):
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is not None and lon is not None:
            entities.append(
                _make_location_tracker(
                    logical_id=lid,
                    name=f"{context.device_name} Location",
                    device_info=device_info,
                )
            )

    logger.info(
        "map_gateway: mapped gateway '%s' to %d HA entity(ies)",
        context.device_name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_connectivity_sensor(
    logical_id: str,
    suffix: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a binary_sensor entity with device_class: connectivity.

    Used for isReachable and backendConnected gateway attributes.

    Args:
        logical_id (str):    Dirigera logical device id.
        suffix (str):        Unique id suffix (e.g. 'reachable').
        name (str):          Human-readable entity name.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured connectivity binary sensor.
    """

    return Entity(
        domain=HADomain.BINARY_SENSOR,
        name=name,
        unique_id=make_unique_id(logical_id, suffix),
        device_info=device_info,
        extra={
            "device_class": "connectivity",
            "payload_on": "ON",
            "payload_off": "OFF",
        },
    )


def _make_string_sensor(
    logical_id: str,
    suffix: str,
    name: str,
    device_info: DeviceInfo,
    entity_category: str = "",
) -> Entity:
    """
    Create a plain string sensor entity with no device_class.

    Used for otaStatus, otaState, firmwareVersion, homeState, timezone.

    Args:
        logical_id (str):      Dirigera logical device id.
        suffix (str):          Unique id suffix.
        name (str):            Human-readable entity name.
        device_info (DeviceInfo): Physical device grouping info.
        entity_category (str): Optional HA entity category
                               ('diagnostic' or ''). Default: ''.

    Returns:
        Entity: Configured string sensor.
    """

    extra = {}
    if entity_category:
        extra["entity_category"] = entity_category

    return Entity(
        domain=HADomain.SENSOR,
        name=name,
        unique_id=make_unique_id(logical_id, suffix),
        device_info=device_info,
        extra=extra if extra else None,
    )


def _make_timestamp_sensor(
    logical_id: str,
    suffix: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a sensor entity with device_class: timestamp.

    Used for nextSunrise and nextSunset — both are ISO 8601 strings
    that HA displays as local times when device_class is timestamp.

    Args:
        logical_id (str):    Dirigera logical device id.
        suffix (str):        Unique id suffix.
        name (str):          Human-readable entity name.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured timestamp sensor.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=name,
        unique_id=make_unique_id(logical_id, suffix),
        device_info=device_info,
        extra={
            "device_class": "timestamp",
        },
    )


def _make_location_tracker(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a device_tracker entity for the hub's GPS coordinates.

    HA displays device_tracker entities on its map view. The
    coordinates come from the gateway's attributes.coordinates dict.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable entity name.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured device tracker.
    """

    return Entity(
        domain=HADomain.DEVICE_TRACKER,
        name=name,
        unique_id=make_unique_id(logical_id, "location"),
        device_info=device_info,
        extra={
            "source_type": "gps",
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to this module's mapper function.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "gateway": map_gateway,
}
