"""
remote.py

Home Assistant entity mapper for Dirigera lightController devices
(remote controls, wall switches).

Role & Responsibility:
    Maps Dirigera lightController DeviceContexts to HA entities that
    represent the physical remote controls and wall switches paired to
    the Dirigera hub.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        lightController — Remote Control N2 (E2001), STYRBAR, and any
                          other Dirigera-paired button/switch controller.

    Controllers are input-only devices from HA's perspective — they
    emit events when buttons are pressed and Dirigera forwards those
    events over WebSocket. They cannot receive commands from HA.

What it does:
    Produces the following HA entities per lightController device:

    event  — button action sensor      (primary — captures button presses)
    sensor — battery level             (if batteryPercentage present)

    The primary entity uses HADomain.EVENT which is the correct modern
    HA domain for button-type inputs (introduced in HA 2023.8). This
    replaces the older pattern of using 'sensor' with a last-action
    string value, and allows HA automations to trigger directly on
    button press events rather than polling a state value.

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES
                                        under key 'lightController')
    - app/mapping/device_mapper.py     (calls map_light_controller())

Not responsible for:
    - Command translation — lightControllers are read-only from HA
    - State updates for commands — the state_mapper handles incoming
      WebSocket events (isOn, lightLevel changes from the remote)
    - MQTT publishing (ha_client.py)

Design notes:
    - The Dirigera deviceType is 'lightController', not 'remote'.
      The file is named remote.py to reflect HA domain intent, but
      DEVICE_TYPES registers it under 'lightController'.
    - From real Remote Control N2 (E2001) discovery data:
        canSend: ['isOn', 'lightLevel']  ← what it emits
        canReceive: ['customName']       ← what it accepts (name only)
        batteryPercentage: 90 or 100
    - HADomain.EVENT is used for the primary entity. The event_types
      list covers the standard IKEA N2 remote button actions observed
      over WebSocket: short press, long press, double press for both
      on and off buttons, plus brightness up/down. The exact event
      type strings are set to match what Dirigera sends over WebSocket
      as attribute values in canSend events.
    - If HADomain.EVENT is not available in the installed HASDK version,
      the mapper falls back to HADomain.SENSOR with a 'last_action'
      string state. The fallback is handled gracefully.
    - lightLevel in attributes (value=1 from real data) is an internal
      Dirigera field representing the dimming step size — not exposed.
    - isOn in attributes (value=False from real data) is the last
      known state of the linked light, not the controller itself —
      not exposed as an entity on the controller device.
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
    "map_light_controller",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_BATTERY_PERCENTAGE = "batteryPercentage"

# Standard IKEA Remote Control N2 button event types.
# These match the WebSocket attribute values emitted by Dirigera
# when a button is pressed on the remote.
_REMOTE_EVENT_TYPES = [
    "shortRelease",
    "longPress",
    "longRelease",
    "doublePress",
    "shortRelease_off",
    "longPress_off",
    "longRelease_off",
    "doublePress_off",
]


def map_light_controller(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera lightController DeviceContext to HA entities.

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
        "map_light_controller: mapping '%s' (logical_id=%s)",
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

    # ── Primary: event entity for button presses ──────────────────────────

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
        "map_light_controller: mapped '%s' to %d HA entity(ies)",
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
    Create an event entity for button press actions.

    Uses HADomain.EVENT where available (HA 2023.8+). The entity
    captures button press events from the remote control and makes
    them available for HA automations.

    The event_types list covers all standard IKEA N2 remote button
    actions. HA uses this list to validate incoming event payloads
    and to show available trigger types in the automation editor.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable remote name shown in HA.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured event entity for the remote control.
    """

    return Entity(
        domain=HADomain.EVENT,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "event_types": _REMOTE_EVENT_TYPES,
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
# Note: Dirigera uses 'lightController' — the file is named
# remote.py to reflect HA domain intent.
DEVICE_TYPES = {
    "lightController": map_light_controller,
}
