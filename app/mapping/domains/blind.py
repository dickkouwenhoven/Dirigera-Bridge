"""
blind.py

Home Assistant entity mapper for Dirigera blind / cover devices.

Role & Responsibility:
    Maps Dirigera blind DeviceContexts to HA cover entities. Handles
    IKEA motorized blinds, roller blinds, and curtains paired to the
    Dirigera hub.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        blind  — IKEA motorized blinds (PRAKTLYSING, KADRILJ,
                 FYRTUR, SANDFJÄRD, etc.)
        blinds — Alternate deviceType string used by some blind models

    The HA 'cover' domain is the correct mapping for motorized window
    coverings — it provides open/close/stop commands and a position
    state (0–100%).

What it does:
    Produces the following HA entities per blind device:

    cover  — primary blind control     (always created)
    sensor — battery level             (if batteryPercentage present
                                        — battery-powered blinds only)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES
                                        under keys 'blind' and 'blinds')
    - app/mapping/device_mapper.py     (calls map_blind())

Not responsible for:
    - State updates (state_mapper.py reads currentLevel / blindsCurrentLevel)
    - Command translation (command_mapper.py translates open/close/stop
      and position commands to Dirigera REST payloads)
    - MQTT publishing (ha_client.py)

Design notes:
    - Dirigera blind position attribute names vary by model:
        'currentLevel'       — used by most IKEA blinds (0=open, 100=closed)
        'blindsCurrentLevel' — used by some variants
      The mapper does not need to know which attribute is used for
      state — that is handled by state_mapper.py. The entity
      configuration only needs device_class and command support.
    - IMPORTANT: Dirigera position 0 = fully open, 100 = fully closed.
      HA position 0 = fully closed, 100 = fully open. This inversion
      must be handled in state_mapper.py and command_mapper.py —
      NOT here. The entity config is position-agnostic.
    - device_class 'blind' is the most common HA cover subtype for
      IKEA roller blinds. It renders with appropriate icons and
      position slider in the HA UI.
    - The cover entity is configured with position support (0–100)
      so HA renders a position slider. Open/close/stop commands are
      all supported.
    - Some IKEA blinds are battery powered (PRAKTLYSING), others are
      hardwired (KADRILJ with power cable). The battery entity is
      conditional on batteryPercentage being present.
    - 'tilt' commands are not supported — IKEA blinds do not support
      tilt/angle adjustment.
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
    "map_blind",
]

logger = logging.getLogger(__name__)

# Attribute keys as they appear in Dirigera raw attributes (camelCase)
_ATTR_BATTERY_PERCENTAGE = "batteryPercentage"
_ATTR_CURRENT_LEVEL = "currentLevel"
_ATTR_BLINDS_CURRENT_LEVEL = "blindsCurrentLevel"


def map_blind(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera blind DeviceContext to HA entities.

    Produces:
        - cover entity  for blind position control
        - sensor entity for battery level (if batteryPercentage present)

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: 1 entity (cover only) or 2 entities
                      (cover + battery).
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_blind: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities: List[Entity] = [
        _make_cover(
            logical_id=lid,
            name=name,
            device_info=device_info,
        )
    ]

    # ── Primary: cover entity ─────────────────────────────────────────────

    # ── Secondary: battery sensor (battery-powered blinds only) ───────────
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
        "map_blind: mapped '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_cover(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a cover entity for blind position control.

    Configured for position support (0–100 slider in HA UI) and
    open/close/stop commands. device_class 'blind' renders with the
    appropriate roller blind icon in HA.

    Position inversion (Dirigera 0=open ↔ HA 100=open) is handled
    downstream in state_mapper.py and command_mapper.py, not here.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable blind name shown in HA.
        device_info (DeviceInfo): Physical device grouping info.

    Returns:
        Entity: Configured cover entity.
    """

    return Entity(
        domain=HADomain.COVER,
        name=name,
        unique_id=make_unique_id(logical_id),
        device_info=device_info,
        extra={
            "device_class": "blind",
            "payload_open": "OPEN",
            "payload_close": "CLOSE",
            "payload_stop": "STOP",
            "position_open": 100,
            "position_closed": 0,
            "set_position_topic": None,  # set by ha_client via SDK
            "optimistic": False,
        },
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Two keys registered because Dirigera uses both 'blind' and 'blinds'
# depending on the firmware / product variant.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "blind": map_blind,
    "blinds": map_blind,
}
