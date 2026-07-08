"""
switch.py

Home Assistant entity mapper for Dirigera switch devices.

Role & Responsibility:
    Maps Dirigera switch DeviceContexts to HA switch entities.
    Handles IKEA smart switches that provide simple on/off control
    without the energy monitoring capabilities of the outlet.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        switch — Generic on/off switch devices

    The distinction between 'switch' and 'outlet' in Dirigera:
        outlet — INSPELNING smart plug (has energy monitoring:
                 currentActivePower, currentVoltage, currentAmps,
                 totalEnergyConsumed)
        switch — Simple on/off switch (no energy monitoring)

    Both produce a HADomain.SWITCH entity as their primary entity,
    but outlet.py also produces energy sensor entities. This file
    handles only the simple switch case.

What it does:
    Produces the following HA entities per switch device:

    switch — on/off control             (always created)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_switch())

Not responsible for:
    - State updates (state_mapper.py reads isOn)
    - Command translation (command_mapper.py translates ON/OFF
      to Dirigera isOn boolean)
    - MQTT publishing (ha_client.py)
    - Energy monitoring — use outlet.py for that

Design notes:
    - The 'switch' deviceType in Dirigera is distinct from 'outlet'.
      If a future device has both on/off and energy monitoring but
      uses deviceType 'switch', it should be handled by extending
      this file rather than outlet.py to maintain the correct
      deviceType routing.
    - No battery entity is created — Dirigera switch devices are
      typically mains-powered. If a future battery-powered switch
      variant appears, the battery check can be added here following
      the same pattern as binary_sensor.py and remote.py.
    - payload_on / payload_off are 'ON' / 'OFF' to match the
      bridge's MQTT state payload convention throughout.
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
    "map_switch",
]

logger = logging.getLogger(__name__)


def map_switch(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera switch DeviceContext to a list containing one HA
    switch entity.

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: A single-element list containing the switch entity.
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_switch: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entity = _make_switch(
        logical_id=lid,
        name=name,
        device_info=device_info,
    )

    logger.info(
        "map_switch: mapped switch '%s' to 1 HA entity",
        name,
    )

    return [entity]


# ── Private entity factories ──────────────────────────────────────────────────


def _make_switch(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Create a switch entity for on/off control.

    Args:
        logical_id (str):    Dirigera logical device id.
        name (str):          Human-readable switch name shown in HA.
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


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "switch": map_switch,
}
