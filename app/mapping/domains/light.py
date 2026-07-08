"""
light.py

Home Assistant entity mapper for Dirigera light devices.

Role & Responsibility:
    Maps a Dirigera light DeviceContext to one HA light entity.
    Inspects the device's capabilities (canReceive) and attributes
    to determine the exact feature set of the light and configures
    the HA entity accordingly.

    IKEA Dirigera lights come in three capability tiers:
        1. On/Off only         — basic switch-like bulbs
        2. Dimmable            — canReceive: isOn + lightLevel
        3. Color Temperature  — canReceive: isOn + lightLevel +
                                  colorTemperature
        4. Full Color (RGB)   — canReceive: isOn + lightLevel +
                                  colorTemperature + colorHue +
                                  colorSaturation

    The mapper auto-detects the tier from canReceive and sets the
    appropriate HA MQTT light schema fields. This means adding a new
    color-capable bulb to the hub does not require any code change —
    the mapper reads the capabilities from the discovery payload.

What it does:
    - Produces one HADomain.LIGHT entity per Dirigera light device
    - Detects on/off, brightness, color temperature, and RGB
      capability from canReceive
    - Sets the correct MQTT light schema (JSON schema for full-color
      lights, basic schema for simpler lights)
    - Applies colorTemperatureMin / colorTemperatureMax from
      attributes as mireds for HA color temperature range

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_light())

Not responsible for:
    - State updates (state_mapper.py handles light state)
    - Command translation (command_mapper.py handles light commands)
    - MQTT publishing (ha_client.py)

Design notes:
    - Dirigera reports color temperature in Kelvin
      (colorTemperatureMin / colorTemperatureMax), HA expects Mireds.
      Conversion: mireds = 1,000,000 / kelvin.
      Higher Kelvin = cooler/bluer light = lower mireds.
      Lower Kelvin = warmer/more orange light = higher mireds.
      Confirmed against a real TRADFRI CWS hub payload: Dirigera
      reports colorTemperatureMin=4000 (the cooler end, higher K)
      and colorTemperatureMax=2202 (the warmer end, lower K) — i.e.
      Dirigera's "min"/"max" here track a warmth axis, not the raw
      Kelvin number's own magnitude. HA's min_mireds/max_mireds must
      still satisfy min_mireds <= max_mireds (a valid slider range),
      so:
        HA min_mireds = kelvin_to_mireds(colorTemperatureMin)  (cooler K → smaller mireds)
        HA max_mireds = kelvin_to_mireds(colorTemperatureMax)  (warmer K → larger mireds)
    - The MQTT light JSON schema is used for RGB lights because it
      supports color mode switching in a single payload. The basic
      schema is used for simpler lights.
    - colorMode from Dirigera ('color', 'temperature') maps to HA
      color_mode values ('hs', 'color_temp').
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..device_registry import DeviceContext
from ha_mqtt_sdk import HADomain
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo

from . import make_unique_id

__all__ = [
    "DEVICE_TYPES",
    "map_light",
]

logger = logging.getLogger(__name__)

# Capability keys as they appear in Dirigera canReceive lists
_CAP_IS_ON = "isOn"
_CAP_LIGHT_LEVEL = "lightLevel"
_CAP_COLOR_TEMP = "colorTemperature"
_CAP_COLOR_HUE = "colorHue"
_CAP_COLOR_SATURATION = "colorSaturation"

# Attribute keys as they appear in Dirigera raw attributes
_ATTR_COLOR_TEMP_MIN = "colorTemperatureMin"
_ATTR_COLOR_TEMP_MAX = "colorTemperatureMax"
_ATTR_COLOR_MODE = "colorMode"
_ATTR_LIGHT_LEVEL = "lightLevel"
_ATTR_IS_ON = "isOn"


def map_light(
    context: DeviceContext,
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera light DeviceContext to a list containing one HA
    light entity.

    Detects the capability tier from the device's canReceive list and
    configures the entity with the appropriate MQTT light schema fields.

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: A single-element list containing the light entity.
    """

    lid = context.logical_id
    capabilities = context.capabilities
    attrs = context.attributes

    logger.debug(
        "map_light: mapping light '%s' (logical_id=%s, can_receive=%s)",
        context.device_name,
        lid,
        capabilities,
    )

    # ── Detect capability tier ────────────────────────────────────────────
    can_receive = set(capabilities) if capabilities else set()
    has_dimming = _CAP_LIGHT_LEVEL in can_receive
    has_color_temp = _CAP_COLOR_TEMP in can_receive
    has_color_rgb = (
        _CAP_COLOR_HUE in can_receive and _CAP_COLOR_SATURATION in can_receive
    )

    logger.debug(
        "map_light: capabilities — dimming=%s, color_temp=%s, rgb=%s",
        has_dimming,
        has_color_temp,
        has_color_rgb,
    )

    # ── Build extra fields based on capability tier ───────────────────────
    extra = _build_light_extra(
        attrs=attrs,
        has_dimming=has_dimming,
        has_color_temp=has_color_temp,
        has_color_rgb=has_color_rgb,
    )

    entity = Entity(
        domain=HADomain.LIGHT,
        name=context.device_name,
        unique_id=make_unique_id(lid),
        device_info=device_info,
        extra=extra,
    )

    logger.info(
        "map_light: mapped light '%s' (dimming=%s, color_temp=%s, rgb=%s)",
        context.device_name,
        has_dimming,
        has_color_temp,
        has_color_rgb,
    )

    return [entity]


# ── Private helpers ───────────────────────────────────────────────────────────


def _build_light_extra(
    attrs: Dict[str, Any],
    has_dimming: bool,
    has_color_temp: bool,
    has_color_rgb: bool,
) -> Dict[str, Any]:
    """
    Build the extra fields dict for the HA MQTT light entity based
    on the detected capability tier.

    Args:
        attrs (dict):          Raw device attributes dict.
        has_dimming (bool):    Device supports brightness control.
        has_color_temp (bool): Device supports color temperature.
        has_color_rgb (bool):  Device supports full RGB color.

    Returns:
        Dict[str, Any]: extra fields for the Entity constructor.
    """

    extra: Dict[str, Any] = {}

    # ── Brightness ────────────────────────────────────────────────────────
    if has_dimming:
        # HA brightness scale: 0–255, Dirigera lightLevel: 1–100
        extra["brightness_scale"] = 100

    # ── Colour temperature ────────────────────────────────────────────────
    if has_color_temp:
        min_k = attrs.get(_ATTR_COLOR_TEMP_MIN)
        max_k = attrs.get(_ATTR_COLOR_TEMP_MAX)

        if min_k and max_k:
            # colorTemperatureMin is the cooler end (higher Kelvin),
            # which corresponds to the smaller mireds value — this is
            # HA's min_mireds. colorTemperatureMax is the warmer end
            # (lower Kelvin), corresponding to the larger mireds value
            # — HA's max_mireds. No swap needed; higher Kelvin simply
            # produces lower mireds by the conversion formula itself.
            try:
                min_mireds = _kelvin_to_mireds(min_k)
                max_mireds = _kelvin_to_mireds(max_k)
                extra["min_mireds"] = min_mireds
                extra["max_mireds"] = max_mireds
            except (ZeroDivisionError, TypeError, ValueError) as exc:
                logger.warning(
                    "map_light: could not convert colour temperature "
                    "range (%sK–%sK) to mireds: %s",
                    min_k,
                    max_k,
                    exc,
                )

    # ── Full color (RGB/HS) ──────────────────────────────────────────────
    if has_color_rgb:
        # Use JSON schema for HA MQTT light — supports color mode
        # switching (hs vs color_temp) in a single state payload
        extra["schema"] = "json"

        # Supported color modes for HA
        supported_modes = ["hs"]
        if has_color_temp:
            supported_modes.append("color_temp")
        extra["supported_color_modes"] = supported_modes

    elif has_color_temp:
        # Color temperature only — no RGB
        extra["supported_color_modes"] = ["color_temp"]

    elif has_dimming:
        # Brightness only — no color
        extra["supported_color_modes"] = ["brightness"]

    else:
        # On/off only
        extra["supported_color_modes"] = ["onoff"]

    return extra


def _kelvin_to_mireds(kelvin: Any) -> int:
    """
    Convert a color temperature value from Kelvin to Mireds
    (micro reciprocal degrees).

    Mireds = 1,000,000 / Kelvin

    Args:
        kelvin: Color temperature in Kelvin. Must be a positive
                number. Accepts int or float.

    Returns:
        int: Color temperature in Mireds, rounded to nearest integer.

    Raises:
        ValueError:      If kelvin is not a positive number.
        ZeroDivisionError: If kelvin is zero (propagated to caller).
        TypeError:       If kelvin cannot be used in arithmetic.
    """

    kelvin_float = float(kelvin)

    if kelvin_float <= 0:
        raise ValueError(f"_kelvin_to_mireds: kelvin must be positive, got {kelvin}")

    return round(1_000_000 / kelvin_float)


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to this module's mapper function.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "light": map_light,
}
