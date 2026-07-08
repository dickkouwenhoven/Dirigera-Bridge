"""
button.py

Home Assistant entity mapper for Dirigera button and shortcut
controller devices.

Role & Responsibility:
    Maps Dirigera button and shortcut controller DeviceContexts to HA
    event entities. Handles single-button and multi-button devices
    that are distinct from full remote controls (lightController) —
    these are simpler, typically single-purpose input devices.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        button            — SOMRIG Shortcut Button (E2213)
                            Single or double button shortcut device
        shortcutController — Alternative deviceType for shortcut
                             button devices in some firmware versions

    The distinction from lightController (remote.py):
        lightController — Multi-button remote with light-specific
                          actions (isOn, lightLevel). Always paired
                          to lights and emits lighting state changes.
        button          — General-purpose shortcut button. Can be
                          assigned to any action in Dirigera app.
                          Emits generic press events.

What it does:
    Produces the following HA entities per button device:

    event  — button press actions       (primary — captures presses)
    sensor — battery level              (if batteryPercentage present)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES
                                        under 'button' and
                                        'shortcutController')
    - app/mapping/device_mapper.py     (calls map_button())

Not responsible for:
    - Command translation — buttons are read-only from HA
    - MQTT publishing (ha_client.py)

Design notes:
    - The SOMRIG Shortcut Button (E2213) supports up to 2 buttons
      (one or two physical buttons on the device). The event_types
      list covers both single and double press on each button.
    - Event type strings are based on observed Dirigera WebSocket
      payloads for shortcut buttons:
        'shortRelease'  — quick press and release
        'longRelease'   — press held then released
        'doublePress'   — two quick presses
      These are the same base event types as the lightController
      but without the directional '_off' variants since shortcut
      buttons do not have separate on/off sides.
    - Both 'button' and 'shortcutController' are registered to the
      same mapper function — they are functionally identical from
      an HA perspective.
    - Some shortcut button variants may be battery powered
      (batteryPercentage present) or USB powered (absent).
      The battery entity is conditional.
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
    "map_button",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_BATTERY_PERCENTAGE = "batteryPercentage"

# Shortcut button event types observed from Dirigera WebSocket events.
# Covers single-button and dual-button SOMRIG variants.
_BUTTON_EVENT_TYPES = [
    "shortRelease",
    "longRelease",
    "doublePress",
]


def map_button(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera button / shortcutController DeviceContext to HA
    entities.

    Produces:
        - event entity  for button press actions
        - sensor entity for battery level (if batteryPercentage present)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: 1 entity (event only) or 2 entities
                      (event + battery).
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_button: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_event_entity(
            logical_id=lid,
            name=name,
            device_info=device_info,
        )
    ]

    # ── Primary: event entity for button press actions ────────────────────

    # ── Secondary: battery sensor (conditional) ───────────────────────────
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
        "map_button: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_event_entity(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create an event entity for shortcut button press actions.

    Uses HADomain.EVENT for the HA event domain (HA 2023.8+).
    The event_types list covers all observed SOMRIG button actions.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable button name shown in HA.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured event entity for the button device.
    """

    return Entity(
        domain=HADomain.EVENT,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "event_types": _BUTTON_EVENT_TYPES,
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Both 'button' and 'shortcutController' map to the same function.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "button": map_button,
    "shortcutController": map_button,
}
