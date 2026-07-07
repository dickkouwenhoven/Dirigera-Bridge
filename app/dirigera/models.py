"""
models.py

Pydantic models for Dirigera device and WebSocket event payloads.

Role & Responsibility:
    Defines the data structures that represent the Dirigera world as
    seen by this bridge. Every piece of raw JSON that arrives from the
    Dirigera REST API (device list) or WebSocket (real-time events) is
    parsed and validated into one of these models before any other
    layer touches it.

    This is the boundary between "raw bytes from the network" and
    "trusted, typed Python objects". If Dirigera sends unexpected data,
    validation errors are raised here — never silently propagated as
    dicts or Nones into the mapping layer.

What it does:
    - DirigeraAttributes: flexible model for the per-device attributes
    block, which varies widely across device types. Uses extra='allow'
    so unknown attributes (from new firmware) are preserved rather
    than silently dropped.
    - DirigeraRoom: the room block present on most devices.
    - DirigeraCapabilities: the canSend / canReceive capability lists.
    - DirigeraDevice: the full device object as returned by the REST
    API discovery endpoint. Handles both single-deviceType devices
    (no relationId) and multi-deviceType devices (with relationId).
    - DirigeraEventAttributes: the attributes sub-block inside a
    WebSocket event, carrying the changed attribute name and value.
    - DirigeraWebSocketEvent: the top-level WebSocket message structure.

Arguments / Configuration:
    No runtime configuration. Models are instantiated by
    websocket_client.py and rest_client.py when parsing raw JSON.

Used by:
    - app/dirigera/rest_client.py       (parses device list response)
    - app/dirigera/websocket_client.py  (parses incoming WS messages)
    - app/mapping/device_registry.py    (reads DirigeraDevice fields)
    - app/mapping/device_mapper.py      (reads DirigeraDevice fields)
    - app/mapping/state_mapper.py       (reads DirigeraWebSocketEvent)

Not responsible for:
    - Making any network calls (that is rest_client / websocket_client)
    - Mapping to HA entities (that is the mapping layer)
    - Storing or caching data (that is state_cache / discovery_cache)

Design notes:
    - All models use model_config with populate_by_name=True so both
    camelCase (from JSON) and snake_case (from Python) field names
    work when constructing models in tests.
    - Fields that are absent in some device types are Optional with
    None defaults — never use ... (required) for fields that only
    some devices carry.
    - The raw attributes dict is preserved on DirigeraDevice as
    raw_attributes so domain mappers can access any attribute,
    including ones not modeled as typed fields.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict

__all__ = [
    "DirigeraAttributes",
    "DirigeraRoom",
    "DirigeraCapabilities",
    "DirigeraDevice",
    "DirigeraEventAttributes",
    "DirigeraWebSocketEvent",
    "DirigeraWebSocketEventData",
]

logger = logging.getLogger(__name__)


# ── Shared config ─────────────────────────────────────────────────────────────

# Applied to all models: allows population by field name OR alias,
# and ignores extra fields at the top level of each model (extra
# fields inside attributes are handled by DirigeraAttributes).
_MODEL_CONFIG = ConfigDict(
    populate_by_name=True,
    extra="ignore",
    frozen=False,
)


# ── Sub-models ────────────────────────────────────────────────────────────────


class DirigeraRoom(BaseModel):
    """
    Room assignment block present on most Dirigera devices.

    Not present on multi-deviceType siblings that are not the primary
    (e.g. the VALLHORN lightSensor _3 has no room block).

    Fields:
        id (str):    Dirigera room identifier.
        name (str):  Human-readable room name (e.g. 'LivingRoom').
        color (str): Room color identifier string.
        icon (str):  Room icon identifier string.
    """

    model_config = _MODEL_CONFIG

    id: str
    name: str
    color: str = ""
    icon: str = ""


class DirigeraCapabilities(BaseModel):
    """
    Device capability declaration from Dirigera.

    Indicates which attribute names the device can send (emit as
    events) and which it can receive (accept as commands).

    Fields:
        can_send (list[str]):    Attribute names this device emits.
        can_receive (list[str]): Attribute names this device accepts.
    """

    model_config = _MODEL_CONFIG

    can_send: List[str] = Field(default_factory=list, alias="canSend")
    can_receive: List[str] = Field(default_factory=list, alias="canReceive")


class DirigeraAttributes(BaseModel):
    """
    Per-device attributes block from Dirigera.

    This block varies significantly across device types — a light has
    colorHue and lightLevel, a motion sensor has isDetected, an outlet
    has currentActivePower, etc. Rather than attempting to model every
    possible attribute as a typed field, only the universal attributes
    that all devices share are typed here. All remaining attributes are
    preserved in the raw dict accessible via model.model_extra or via
    the parent DirigeraDevice.raw_attributes.

    extra='allow' ensures that unknown/new attributes from firmware
    updates are preserved rather than silently dropped.

    Universal typed fields (present on all or nearly all devices):
    custom_name (str): User-assigned device name.
    model (str): Hardware model string.
    manufacturer (str): Manufacturer name.
    firmware_version (str): Current firmware version.
    hardware_version (str): Hardware revision string.
    serial_number (str): Unique hardware serial number.
    product_code (str): Product SKU / order code.
    is_on (bool | None): Power state. None if not applicable.
    battery_percentage (int | None): Battery level 0-100. None if device is mains-powered.
    ota_status (str | None): OTA firmware update status string.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",  # preserve unknown attributes
        frozen=False,
    )

    custom_name: str = Field(default="", alias="customName")
    model: str = Field(default="")
    manufacturer: str = Field(default="")
    firmware_version: str = Field(default="", alias="firmwareVersion")
    hardware_version: str = Field(default="", alias="hardwareVersion")
    serial_number: str = Field(default="", alias="serialNumber")
    product_code: Optional[str] = Field(default=None, alias="productCode")
    is_on: Optional[bool] = Field(default=None, alias="isOn")
    battery_percentage: Optional[int] = Field(default=None, alias="batteryPercentage")
    ota_status: Optional[str] = Field(default=None, alias="otaStatus")

    def get_extra(self, key: str, default: Any = None) -> Any:
        """
        Retrieve an attribute that is not a typed field.

        Use this in domain mappers to access device-specific attributes
        such as 'lightLevel', 'isDetected', 'illuminance', etc. that
        are not universally present across all devices.

        Args:
        key (str): The camelCase attribute name as it appears
        in the Dirigera JSON payload.
        default (Any):Value to return if the key is absent.

        Returns:
        Any: Attribute value or default.
        """

        if self.model_extra:
            return self.model_extra.get(key, default)
        return default

    def all_attributes(self) -> Dict[str, Any]:
        """
        Return a merged dict of all attributes — both typed fields
        and any extra attributes preserved by extra='allow'.

        Keyed by camelCase names matching the Dirigera JSON payload
        wherever an alias is defined; snake_case for fields without
        an alias.

        Returns:
        Dict[str, Any]: Complete attribute mapping.
        """

        # Start with extra (untyped) attributes
        result: Dict[str, Any] = dict(self.model_extra or {})

        # Overlay typed fields using their alias (camelCase) names
        # so the result is consistent with the raw JSON keys
        cls = type(self)
        for field_name, field_info in cls.model_fields.items():
            alias = field_info.alias or field_name
            value = getattr(self, field_name)
            result[alias] = value

        return result


# ── Primary device model ──────────────────────────────────────────────────────


class DirigeraDevice(BaseModel):
    """
    Full Dirigera device object as returned by the REST API discovery
    endpoint (GET /devices) and as referenced in WebSocket events.

    Covers both single-deviceType devices (no relationId field) and
    multi-deviceType devices (with relationId, e.g. VALLHORN).

    Fields:
    id (str):
        Logical device identifier. Always present. Ends in a suffix
        like '_1' or '_3'. Example: 'fff75d00-607c-4f23-a0e7-3dbed0e18b12_1'

    relation_id (str | None):
        Physical device relation identifier. Present only on
        multi-deviceType devices. All logical devices that share physical hardware carry
        the same relation_id. Example: 'fff75d00-607c-4f23-a0e7-3dbed0e18b12'
        None for single-deviceType devices.

    type (str):
        Dirigera device category string. Examples: 'light',
        'sensor', 'controller', 'outlet', 'gateway', 'unknown'.
        Not used for routing — use device_type instead.

    device_type (str):
        Dirigera device type string. This is the routing key used
        by the mapping layer. Examples: 'light', 'motionSensor',
        'lightSensor', 'environmentSensor', 'outlet', 'gateway'.

    is_reachable (bool):
        Whether the hub can currently communicate with the device.

    attributes (DirigeraAttributes):
        Parsed attribute block. Typed universal fields plus any
        device-specific extras preserved by extra='allow'.

    capabilities (DirigeraCapabilities):
        canSend / canReceive capability lists.

    room (DirigeraRoom | None):
        Room assignment. None for gateway and for secondary
        deviceType siblings that inherit room from their primary.

    is_hidden (bool):
        Whether the device is hidden in the Dirigera app.
        Hidden devices are still processed by the bridge.

    created_at (str | None):
        ISO 8601 creation timestamp. None for gateway.

    last_seen (str | None):
        ISO 8601 timestamp of last communication. None for gateway.

    raw_attributes (dict):
        The original unmodified attributes dict from the JSON
        payload, preserved for domain mappers that need direct
        dict access rather than going through the pydantic model.
        Populated by the model_validator after parsing.
    """

    model_config = _MODEL_CONFIG

    id: str
    relation_id: Optional[str] = Field(default=None, alias="relationId")
    type: str = Field(default="")
    device_type: str = Field(default="", alias="deviceType")
    is_reachable: bool = Field(default=False, alias="isReachable")
    attributes: DirigeraAttributes
    capabilities: DirigeraCapabilities = Field(default_factory=DirigeraCapabilities)
    room: Optional[DirigeraRoom] = Field(default=None)
    is_hidden: bool = Field(default=False, alias="isHidden")
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    last_seen: Optional[str] = Field(default=None, alias="lastSeen")

    # Populated by model_validator — not present in raw JSON
    raw_attributes: Dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def _populate_raw_attributes(self) -> "DirigeraDevice":
        """
        Populate raw_attributes from the parsed attributes model.

        Called automatically after model construction. Gives domain
        mappers a plain dict of all attributes (typed + extras) without
        requiring them to know the pydantic model structure.
        """

        self.raw_attributes = self.attributes.all_attributes()
        return self

    @property
    def physical_id(self) -> str:
        """
        The physical device identifier used as the key for device_info
        construction and physical device grouping.

        Returns relation_id if present (multi-deviceType device),
        otherwise returns id (single-deviceType device).

        Returns:
            str: Physical device identifier.
        """

        return self.relation_id if self.relation_id is not None else self.id

    @property
    def is_grouped(self) -> bool:
        """
        Return True if this device is part of a multi-deviceType group
        (i.e. has a relationId in the raw payload).

        Returns:
            bool: True if relation_id is set.
        """

        return self.relation_id is not None

    @property
    def has_battery(self) -> bool:
        """
        Return True if the device reports a battery level.

        Used by domain mappers to decide whether to create a battery
        sensor entity alongside the primary entity.

        Returns:
            bool: True if battery_percentage is present.
        """

        return self.attributes.battery_percentage is not None

    @property
    def device_name(self) -> str:
        """
        Return the user-assigned device name from attributes.

        Returns:
            str: customName or empty string if not set.
        """

        return self.attributes.custom_name

    @property
    def room_name(self) -> Optional[str]:
        """
        Return the room name if a room is assigned, else None.

        Returns:
            str | None: Room name or None.
        """

        return self.room.name if self.room is not None else None

    def __repr__(self) -> str:
        return (
            f"DirigeraDevice("
            f"id={self.id!r}, "
            f"device_type={self.device_type!r}, "
            f"name={self.device_name!r}, "
            f"is_reachable={self.is_reachable}, "
            f"relation_id={self.relation_id!r}"
            f")"
        )


# ── WebSocket event models ────────────────────────────────────────────────────


class DirigeraEventAttributes(BaseModel):
    """
    The attributes sub-block inside a Dirigera WebSocket event.

    WebSocket events carry only the changed attribute(s), not the full
    device state. Each changed attribute appears as a key-value pair
    inside the attributes dict.

    extra='allow' is essential here because the key names vary by
    device type, and we must not drop any of them.

    Fields:
        raw (dict): All changed attribute key-value pairs from the
            event. Accessed via get_changed() helper.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        frozen=False,
    )

    def get_changed(self) -> Dict[str, Any]:
        """
        Return all changed attributes from this event as a plain dict.

        Returns:
            Dict[str, Any]: Changed attribute name → new value mapping.
        """

        return dict(self.model_extra or {})


class DirigeraWebSocketEvent(BaseModel):
    """
    Top-level Dirigera WebSocket message structure.

    Dirigera sends WebSocket messages as JSON objects with a type field
    and a nested data block containing the device id, device type, and
    the changed attributes.

    Fields:
        type (str):
            Event type string from Dirigera. Known values:
            'deviceStateChanged' — a device attribute changed
            'deviceAdded'        — a new device was paired
            'deviceRemoved'      — a device was removed

        data (DirigeraWebSocketEventData | None):
            The nested data block. None if the event type does not
            carry device data (e.g. hub-level notifications).
    """

    model_config = _MODEL_CONFIG

    type: str = Field(default="")
    data: Optional["DirigeraWebSocketEventData"] = Field(default=None)

    @property
    def is_state_change(self) -> bool:
        """Return True if this event represents a device state change."""
        return self.type == "deviceStateChanged"

    @property
    def is_device_added(self) -> bool:
        """Return True if this event represents a newly paired device."""
        return self.type == "deviceAdded"

    @property
    def is_device_removed(self) -> bool:
        """Return True if this event represents a removed device."""
        return self.type == "deviceRemoved"


class DirigeraWebSocketEventData(BaseModel):
    """
    The data block inside a Dirigera WebSocket event.

    Fields:
        id (str):
            Logical device id of the device that triggered the event.
            Same format as DirigeraDevice.id.

        relation_id (str | None):
            Physical device relation id. Present on multi-deviceType
            devices, absent on single-deviceType devices.

        type (str):
            Dirigera device category (e.g. 'light', 'sensor').
            Not used for routing — use device_type.

        device_type (str):
            Dirigera device type string. Routing key for domain mappers.

        attributes (DirigeraEventAttributes):
            Changed attribute(s) for this event. May contain one or
            multiple changed attributes depending on what changed.
    """

    model_config = _MODEL_CONFIG

    id: str
    relation_id: Optional[str] = Field(default=None, alias="relationId")
    type: str = Field(default="")
    device_type: str = Field(default="", alias="deviceType")
    attributes: DirigeraEventAttributes = Field(default_factory=DirigeraEventAttributes)

    @property
    def physical_id(self) -> str:
        """
        The physical device identifier for this event.

        Returns relation_id if present, otherwise id.

        Returns:
            str: Physical device identifier.
        """

        return self.relation_id if self.relation_id is not None else self.id

    @property
    def changed_attributes(self) -> Dict[str, Any]:
        """
        Return the changed attribute(s) from this event as a plain dict.

        Returns:
            Dict[str, Any]: Changed attribute name → new value.
        """

        return self.attributes.get_changed()


# Required for forward reference in DirigeraWebSocketEvent
DirigeraWebSocketEvent.model_rebuild()
