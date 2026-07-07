"""
device_registry.py

Groups Dirigera logical devices by physical device and produces
normalised DeviceContext objects for the mapping layer.

Role & Responsibility:
    This module is the bridge between raw Dirigera device payloads
    (DirigeraDevice models from the REST API) and the domain mappers.
    It owns the physical grouping logic that handles both single-
    deviceType and multi-deviceType devices.

    It produces a flat list of DeviceContext objects — one per
    Dirigera logical device — each carrying:
        - Its own logical id and deviceType (for routing and caching)
        - The physical device identifier (relation_id) for device_info
          construction and HA device grouping
        - Elected device-level properties (name, room, model etc.)
          inherited from the primary logical device in each group

    DeviceContext is the single data type that flows from this module
    into device_mapper.py and all domain mappers. No downstream module
    ever touches a raw DirigeraDevice dict directly.

What it does:
    - Accepts a list of DirigeraDevice models from rest_client.py
    - Groups them by physical_id (relation_id if present, else id)
    - For multi-deviceType groups: elects a primary device (the one
      with a non-empty customName; falls back to first in group)
    - Elects shared device-level properties from the primary:
      device_name, room_name, model, manufacturer, serial_number,
      product_code, firmware_version
    - Filters gateway devices into a separate list so the orchestrator
      can process them independently if needed
    - Returns a list of DeviceContext objects ready for device_mapper.py

    Grouping rules (confirmed from real discovery data):
        - relation_id present  → multi-deviceType group; use relation_id
                                  as physical_id
        - relation_id absent   → single-deviceType device; use id as
                                  physical_id
        - suffix (_1, _3 etc.) → never stripped, never used for grouping
        - remoteLinks          → ignored for grouping (cross-references only)

Arguments / Configuration:
    No runtime configuration. Pure transformation functions.

Used by:
    - app/mapping/device_mapper.py  (receives DeviceContext list)
    - app/orchestrator.py           (calls build_device_contexts())

Not responsible for:
    - Fetching devices from Dirigera (rest_client.py)
    - Mapping DeviceContext to HA entities (device_mapper.py)
    - Caching state or discovery (core/state_cache, core/discovery_cache)
    - Any network I/O or async operations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.errors import DirigeraBridgeError, ErrorCode
from ..dirigera.models import DirigeraDevice

__all__ = [
    "DeviceContext",
    "build_device_contexts",
]

logger = logging.getLogger(__name__)


# ── DeviceContext ─────────────────────────────────────────────────────────────


@dataclass
class DeviceContext:
    """
    Normalised representation of one Dirigera logical device, ready
    for consumption by domain mappers.

    One DeviceContext is produced per Dirigera logical id (one per
    deviceType). For multi-deviceType devices (e.g. VALLHORN with
    motionSensor + lightSensor) there are two DeviceContext objects
    sharing the same relation_id and device-level properties.

    Fields:
        logical_id (str):
            The Dirigera logical device id including suffix.
            e.g. 'fff75d00-607c-4f23-a0e7-3dbed0e18b12_1'
            Used as the cache key in state_cache and discovery_cache.

        relation_id (str):
            The physical device identifier.
            = device.relation_id  if relation_id is present (grouped)
            = device.id           if relation_id is absent (single)
            Used as the identifier for build_device_info() in
            device_mapper.py so all siblings appear under one physical
            device in the HA device registry.

        device_type (str):
            Dirigera deviceType string. Routing key for domain mappers.
            e.g. 'light', 'motionSensor', 'lightSensor', 'outlet'

        is_reachable (bool):
            Whether the hub can currently communicate with this device.

        attributes (dict):
            Raw camelCase attributes dict for this logical device.
            All attributes including device-specific ones (lightLevel,
            isDetected, illuminance etc.) are present here.
            Domain mappers call attributes.get(key) to read values.

        capabilities (list[str]):
            The canReceive list for this logical device. Used by
            light.py to detect capability tier.

        device_name (str):
            Elected device name — non-empty customName from the
            primary sibling in the group. Falls back to model string
            if all siblings have empty customName.

        room_name (str | None):
            Room name from the primary sibling's room block.
            None if no room is assigned.

        model (str):
            Hardware model string from attributes.

        manufacturer (str):
            Manufacturer string from attributes.

        serial_number (str):
            Hardware serial number from attributes.
            Used as the identifier in DeviceInfo.identifiers.

        product_code (str | None):
            Product SKU / order code. None if absent.

        firmware_version (str | None):
            Current firmware version string. None if absent.

        is_grouped (bool):
            True if this device is part of a multi-deviceType group
            (i.e. relation_id was present in the raw payload).
    """

    logical_id: str
    relation_id: str
    device_type: str
    is_reachable: bool
    attributes: Dict[str, Any]
    capabilities: List[str]
    device_name: str
    room_name: Optional[str]
    model: str
    manufacturer: str
    serial_number: str
    product_code: Optional[str]
    firmware_version: Optional[str]
    is_grouped: bool = field(default=False)

    def __repr__(self) -> str:
        return (
            f"DeviceContext("
            f"logical_id={self.logical_id!r}, "
            f"device_type={self.device_type!r}, "
            f"device_name={self.device_name!r}, "
            f"is_reachable={self.is_reachable}, "
            f"is_grouped={self.is_grouped}"
            f")"
        )


# ── Public API ────────────────────────────────────────────────────────────────


def build_device_contexts(
    devices: List[DirigeraDevice],
) -> Tuple[List[DeviceContext], List[DeviceContext]]:
    """
    Transform a flat list of DirigeraDevice models into normalised
    DeviceContext objects grouped by physical device.

    Separates gateway devices from regular devices so the orchestrator
    can handle them independently (the gateway is infrastructure, not
    a typical controllable device, but is still registered in HA).

    Args:
        devices (List[DirigeraDevice]): Raw device list from
            rest_client.get_devices(). Must not be None.

    Returns:
        Tuple[List[DeviceContext], List[DeviceContext]]:
            (regular_contexts, gateway_contexts)

            regular_contexts: All non-gateway devices as DeviceContext
                              objects, one per logical id.
            gateway_contexts: Gateway device(s) as DeviceContext objects.
                              Typically, a single-element list.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if devices is
                             not a list.
        DirigeraBridgeError: MAPPING_DEVICE_BUILD_ERROR if a device
                             cannot be processed (logged and skipped,
                             not raised — one bad device never prevents
                             the rest from loading).
    """

    # ── Validation ────────────────────────────────────────────────────────
    if not isinstance(devices, list):
        raise DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            f"build_device_contexts: devices must be a list, "
            f"got {type(devices).__name__}",
        )

    logger.info(
        "build_device_contexts: processing %d raw device(s)",
        len(devices),
    )

    # ── Group by physical_id ──────────────────────────────────────────────
    groups: Dict[str, List[DirigeraDevice]] = {}

    for device in devices:
        physical_id = device.physical_id
        if physical_id not in groups:
            groups[physical_id] = []
        groups[physical_id].append(device)

    logger.debug(
        "build_device_contexts: grouped %d device(s) into %d physical group(s)",
        len(devices),
        len(groups),
    )

    # ── Build DeviceContext per logical device ────────────────────────────
    regular_contexts: List[DeviceContext] = []
    gateway_contexts: List[DeviceContext] = []
    error_count = 0

    for physical_id, group in groups.items():
        # ── Elect primary within the group ────────────────────────────────
        primary = _elect_primary(group)
        is_grouped = len(group) > 1 or group[0].is_grouped

        # ── Shared device-level properties from primary ───────────────────
        device_name = _elect_device_name(primary)
        room_name = primary.room.name if primary.room else None
        model = primary.attributes.model
        manufacturer = primary.attributes.manufacturer
        serial_number = primary.attributes.serial_number
        product_code = primary.attributes.product_code
        firmware_version = primary.attributes.firmware_version

        # ── One DeviceContext per logical id in the group ─────────────────
        for device in group:
            try:
                ctx = DeviceContext(
                    logical_id=device.id,
                    relation_id=physical_id,
                    device_type=device.device_type,
                    is_reachable=device.is_reachable,
                    attributes=device.raw_attributes,
                    capabilities=device.capabilities.can_receive,
                    device_name=device_name,
                    room_name=room_name,
                    model=model,
                    manufacturer=manufacturer,
                    serial_number=serial_number,
                    product_code=product_code,
                    firmware_version=firmware_version,
                    is_grouped=is_grouped,
                )

                # ── Route gateway vs regular ──────────────────────────────
                if device.device_type == "gateway":
                    gateway_contexts.append(ctx)
                    logger.debug(
                        "build_device_contexts: gateway device '%s' (logical_id=%s)",
                        device_name,
                        device.id,
                    )
                else:
                    regular_contexts.append(ctx)
                    logger.debug(
                        "build_device_contexts: device '%s' "
                        "(logical_id=%s, device_type=%s, "
                        "is_grouped=%s)",
                        device_name,
                        device.id,
                        device.device_type,
                        is_grouped,
                    )

            except Exception as exc:
                error_count += 1
                logger.warning(
                    "build_device_contexts: failed to build context "
                    "for logical_id=%s (device_type=%s): %s — skipping",
                    device.id,
                    device.device_type,
                    exc,
                )

    logger.info(
        "build_device_contexts: produced %d regular + %d gateway "
        "context(s) (%d error(s))",
        len(regular_contexts),
        len(gateway_contexts),
        error_count,
    )

    return regular_contexts, gateway_contexts


# ── Internal helpers ──────────────────────────────────────────────────────────


def _elect_primary(group: List[DirigeraDevice]) -> DirigeraDevice:
    """
    Elect the primary device from a physical device group.

    The primary is the logical device with a non-empty customName.
    If all devices in the group have an empty customName, the first
    device in the list is used as the primary (fallback).

    For single-device groups the only member is always the primary.

    Args:
        group (List[DirigeraDevice]): Logical devices sharing a
                                      physical_id. Must not be empty.

    Returns:
        DirigeraDevice: The elected primary device.
    """

    for device in group:
        if device.attributes.custom_name.strip():
            return device

    # Fallback — no device in the group has a non-empty customName
    logger.debug(
        "_elect_primary: all %d device(s) in group have empty "
        "customName — using first as primary (id=%s)",
        len(group),
        group[0].id,
    )
    return group[0]


def _elect_device_name(
    primary: DirigeraDevice,
) -> str:
    """
    Determine the device name to use for all contexts in the group.

    Priority:
        1. primary.attributes.custom_name  (non-empty)
        2. primary.attributes.model        (non-empty)
        3. primary.device_type             (always present)

    This three-level fallback ensures every device has a human-readable
    name even if customName was never set in the Dirigera app.

    Args:
        primary (DirigeraDevice):     Elected primary device.

    Returns:
        str: Non-empty device name string.
    """

    custom_name = primary.attributes.custom_name.strip()
    if custom_name:
        return custom_name

    model = primary.attributes.model.strip()
    if model:
        logger.debug(
            "_elect_device_name: customName empty for group "
            "primary=%s — using model '%s'",
            primary.id,
            model,
        )
        return model

    logger.debug(
        "_elect_device_name: both customName and model empty for "
        "primary=%s — using deviceType '%s'",
        primary.id,
        primary.device_type,
    )
    return primary.device_type
