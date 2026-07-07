"""
command_mapper.py

Translates Home Assistant MQTT command payloads into Dirigera REST
API attribute update payloads.

Role & Responsibility:
    Owns the translation from an incoming HA MQTT command (received
    on a command topic subscribed to by ha_client.py) to the exact
    dict payload that must be sent to the Dirigera REST API via
    rest_client.send_command().

    This is the reverse of state_mapper.py — where state_mapper
    translates Dirigera → HA, command_mapper translates HA → Dirigera.

What it does:
    - Receives a (unique_id, device_type, command_payload) triple
    - Determines which attribute to update and what value to send
    - Returns a (logical_id, attributes_dict) pair for rest_client
    - Returns None if the command cannot be translated
    - Handles all type conversions in the HA → Dirigera direction:
        'ON'/'OFF'   → True/False   for isOn
        percentage   → fanMode str  for air purifier
        HA position  → Dirigera pos for blind (position inversion)
        JSON payload → dict         for light color commands
        (speaker volume is passed straight through as an int 0-100 —
         no conversion needed since it uses HA's 'number' domain)

Arguments / Configuration:
    No runtime configuration. All methods are pure functions —
    no state, no I/O, no async.

Used by:
    - app/orchestrator.py  (calls map_command() on every
                            COMMAND_RECEIVED event from MQTT)

Not responsible for:
    - Receiving MQTT commands (ha_client.py subscribes and routes)
    - Sending REST commands (rest_client.send_command())
    - State caching (state_cache.py)

Design notes:
    - map_command() receives the unique_id (not the logical_id directly)
      because HA sends commands to entity topics keyed by unique_id.
      The orchestrator must resolve unique_id → logical_id using the
      discovery_cache before calling map_command().
    - Command payload from HA is always a string (MQTT payloads are
      strings). JSON payloads are parsed here if needed.
    - Blind position inversion: HA 0=closed/100=open,
      Dirigera 0=open/100=closed. Formula: dirigera_pos = 100 - ha_pos
    - Fan percentage → fanMode:
        0      → 'off'
        1-33   → 'low'
        34-66  → 'medium'
        67-99  → 'high'
        100    → 'high'
        'auto' → 'auto' (preset mode command)
    - Light commands may arrive as JSON (for color) or plain strings
      (for on/off, brightness). Both cases are handled.
    - The returned attributes dict is passed directly to
      rest_client.send_command() as the body of the PATCH request.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, NamedTuple, Optional

from ..core.errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "CommandPayload",
    "CommandMapper",
]

logger = logging.getLogger(__name__)


# ── CommandPayload ────────────────────────────────────────────────────────────


class CommandPayload(NamedTuple):
    """
    Result of a successful command mapping operation.

    Fields:
        logical_id (str):        The Dirigera logical device id to
                                 send the command to.
        attributes (dict):       The attribute dict to PATCH to Dirigera.
                                 e.g. {'isOn': True, 'lightLevel': 80}
    """

    logical_id: str
    attributes: Dict[str, Any]


# ── Fan percentage → fanMode mapping ─────────────────────────────────────────


def _pct_to_fan_mode(pct: int) -> str:
    """
    Convert an HA fan percentage (0-100) to a Dirigera fanMode string.

    Args:
        pct (int): Fan speed percentage from HA. 0 = off.

    Returns:
        str: Dirigera fanMode string.
    """
    if pct == 0:
        return "off"
    if pct <= 33:
        return "low"
    if pct <= 66:
        return "medium"
    return "high"


# ── CommandMapper ─────────────────────────────────────────────────────────────


class CommandMapper:
    """
    Translates HA MQTT command payloads to Dirigera REST attribute dicts.

    All methods are pure — no state, no I/O, no async. Instantiate
    once and inject into the orchestrator.
    """

    # ── Public API ────────────────────────────────────────────────────────

    def map_command(
        self,
        logical_id: str,
        device_type: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate an HA MQTT command payload to a Dirigera attribute
        update dict.

        Args:
            logical_id (str):      Dirigera logical device id (resolved
                                   from unique_id by the orchestrator).
            device_type (str):     Dirigera deviceType string for routing.
            command_payload (str): Raw MQTT command payload string from HA.

        Returns:
            CommandPayload | None: (logical_id, attributes_dict) if the
                                    command can be translated, or None if
                                    not applicable.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                                 or device_type are not non-empty strings.
            DirigeraBridgeError: MAPPING_INVALID_COMMAND if command_payload
                                 is not a string.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "map_command: logical_id must be a non-empty string",
            )

        if not isinstance(device_type, str) or not device_type.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "map_command: device_type must be a non-empty string",
            )

        if not isinstance(command_payload, str):
            raise DirigeraBridgeError(
                ErrorCode.MAPPING_INVALID_COMMAND,
                f"map_command: command_payload must be a string, "
                f"got {type(command_payload).__name__}",
            )

        logger.debug(
            "map_command: routing command for device_type='%s' "
            "(logical_id=%s, payload=%r)",
            device_type,
            logical_id,
            command_payload[:100],  # truncate for log safety
        )

        # ── Route by device type ──────────────────────────────────────────
        try:
            if device_type == "light":
                return self._map_light_command(logical_id, command_payload)

            if device_type in ("outlet", "switch"):
                return self._map_switch_command(logical_id, command_payload)

            if device_type in ("blind", "blinds"):
                return self._map_blind_command(logical_id, command_payload)

            if device_type == "airPurifier":
                return self._map_air_purifier_command(logical_id, command_payload)

            if device_type == "speaker":
                return self._map_speaker_command(logical_id, command_payload)

        except DirigeraBridgeError:
            raise

        except Exception as exc:
            logger.error(
                "map_command: unexpected error mapping command for "
                "device_type='%s' (logical_id=%s): %s",
                device_type,
                logical_id,
                exc,
            )
            return None

        # Read-only device types — no commands supported
        if device_type in (
            "motionSensor",
            "waterSensor",
            "lightSensor",
            "environmentSensor",
            "lightController",
            "button",
            "shortcutController",
            "gateway",
        ):
            logger.debug(
                "map_command: device_type='%s' is read-only — "
                "no command sent (logical_id=%s)",
                device_type,
                logical_id,
            )
            return None

        logger.warning(
            "map_command: no handler for device_type='%s' (logical_id=%s)",
            device_type,
            logical_id,
        )
        return None

    # ── Internal translators — per device type ────────────────────────────

    @staticmethod
    def _map_light_command(
        logical_id: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate HA light commands to Dirigera attribute updates.

        HA sends light commands as plain strings or JSON depending
        on the command type:
            'ON'                   → isOn: True
            'OFF'                  → isOn: False
            str(int)               → lightLevel: int  (brightness)
            JSON with colorTemp    → colorTemperature: int (Kelvin)
            JSON with hue/sat      → colorHue: float, colorSaturation: float
            JSON with brightness   → lightLevel: int
            JSON with color_mode   → colorMode: str (ignored — Dirigera sets it)

        Args:
            logical_id (str):      Dirigera logical device id.
            command_payload (str): Raw HA MQTT command payload.

        Returns:
            CommandPayload | None
        """

        payload = command_payload.strip()

        # ── Plain ON/OFF ──────────────────────────────────────────────────
        if payload.upper() == "ON":
            return CommandPayload(logical_id, {"isOn": True})

        if payload.upper() == "OFF":
            return CommandPayload(logical_id, {"isOn": False})

        # ── Try JSON payload ──────────────────────────────────────────────
        parsed = _try_parse_json(payload)
        if parsed is not None and isinstance(parsed, dict):
            attributes: Dict[str, Any] = {}

            # ── On/off from JSON ──────────────────────────────────────────
            if "state" in parsed:
                attributes["isOn"] = parsed["state"].upper() == "ON"

            # ── Brightness ────────────────────────────────────────────────
            if "brightness" in parsed:
                # HA brightness: 0-255, Dirigera lightLevel: 1-100
                ha_brightness = int(parsed["brightness"])
                dirigera_level = max(1, min(100, round(ha_brightness * 100 / 255)))
                attributes["lightLevel"] = dirigera_level

            # ── Colour temperature (mireds → Kelvin) ──────────────────────
            if "color_temp" in parsed:
                mireds = int(parsed["color_temp"])
                if mireds > 0:
                    kelvin = round(1_000_000 / mireds)
                    attributes["colorTemperature"] = kelvin

            # ── Colour (HS) ───────────────────────────────────────────────
            if "color" in parsed and isinstance(parsed["color"], dict):
                color = parsed["color"]
                if "h" in color:
                    attributes["colorHue"] = float(color["h"])
                if "s" in color:
                    # HA saturation: 0-100, Dirigera: 0.0-1.0
                    attributes["colorSaturation"] = float(color["s"]) / 100.0

            if attributes:
                return CommandPayload(logical_id, attributes)

        # ── Plain brightness integer ──────────────────────────────────────
        try:
            level = int(payload)
            if 1 <= level <= 100:
                return CommandPayload(logical_id, {"lightLevel": level})
        except ValueError:
            pass

        logger.debug(
            "_map_light_command: unrecognised payload %r for %s",
            payload,
            logical_id,
        )
        return None

    @staticmethod
    def _map_switch_command(
        logical_id: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate HA switch/outlet ON/OFF commands to Dirigera isOn.

        Args:
            logical_id (str):      Dirigera logical device id.
            command_payload (str): 'ON' or 'OFF'.

        Returns:
            CommandPayload | None
        """

        payload = command_payload.strip().upper()

        if payload == "ON":
            return CommandPayload(logical_id, {"isOn": True})

        if payload == "OFF":
            return CommandPayload(logical_id, {"isOn": False})

        logger.debug(
            "_map_switch_command: unrecognised payload %r for %s",
            command_payload,
            logical_id,
        )
        return None

    @staticmethod
    def _map_blind_command(
        logical_id: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate HA cover commands to Dirigera blind attribute updates.

        Position inversion: HA 0=closed/100=open,
        Dirigera 0=open/100=closed.
        Formula: dirigera_pos = 100 - ha_pos

        Handled commands:
            'OPEN'       → currentLevel: 0   (fully open in Dirigera)
            'CLOSE'      → currentLevel: 100 (fully closed in Dirigera)
            'STOP'       → stopLevel command (if supported)
            str(int)     → currentLevel: 100 - int (position)

        Args:
            logical_id (str):      Dirigera logical device id.
            command_payload (str): HA cover command string.

        Returns:
            CommandPayload | None
        """

        payload = command_payload.strip().upper()

        if payload == "OPEN":
            return CommandPayload(logical_id, {"currentLevel": 0})

        if payload == "CLOSE":
            return CommandPayload(logical_id, {"currentLevel": 100})

        if payload == "STOP":
            # Dirigera does not have an explicit stop — send current
            # position to halt movement. Orchestrator reads from cache.
            logger.debug(
                "_map_blind_command: STOP received for %s — "
                "no Dirigera equivalent; command skipped",
                logical_id,
            )
            return None

        # ── Position (0-100 from HA) ──────────────────────────────────────
        try:
            ha_pos = int(command_payload.strip())
            if 0 <= ha_pos <= 100:
                dirigera_pos = 100 - ha_pos
                return CommandPayload(
                    logical_id,
                    {"currentLevel": dirigera_pos},
                )
        except ValueError:
            pass

        logger.debug(
            "_map_blind_command: unrecognised payload %r for %s",
            command_payload,
            logical_id,
        )
        return None

    @staticmethod
    def _map_air_purifier_command(
        logical_id: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate HA fan commands to Dirigera airPurifier attribute
        updates.

        Handled commands:
            'ON'         → fanMode: 'low'  (turn on at low speed)
            'OFF'        → fanMode: 'off'
            'auto'       → fanMode: 'auto' (preset mode)
            str(int 0-100) → fanMode via _pct_to_fan_mode()

        Args:
            logical_id (str):      Dirigera logical device id.
            command_payload (str): HA fan command string.

        Returns:
            CommandPayload | None
        """

        payload = command_payload.strip()

        if payload.upper() == "ON":
            return CommandPayload(logical_id, {"fanMode": "low"})

        if payload.upper() == "OFF":
            return CommandPayload(logical_id, {"fanMode": "off"})

        if payload.lower() == "auto":
            return CommandPayload(logical_id, {"fanMode": "auto"})

        # ── Percentage speed ──────────────────────────────────────────────
        try:
            pct = int(payload)
            if 0 <= pct <= 100:
                fan_mode = _pct_to_fan_mode(pct)
                return CommandPayload(logical_id, {"fanMode": fan_mode})
        except ValueError:
            pass

        logger.debug(
            "_map_air_purifier_command: unrecognised payload %r for %s",
            command_payload,
            logical_id,
        )
        return None

    @staticmethod
    def _map_speaker_command(
        logical_id: str,
        command_payload: str,
    ) -> Optional[CommandPayload]:
        """
        Translate HA commands for the composed speaker entities
        (see app/mapping/domains/speaker.py) to Dirigera attribute
        updates.

        speaker.py composes several small HA entities (switch, number,
        two buttons) instead of a single media_player, since HA's
        MQTT discovery has no media_player domain. Each entity's
        default/custom payload is distinct enough to route on payload
        content alone, without needing to know which specific entity
        or topic a command arrived on:

            'ON' / 'OFF' → isOn: True / False
                UNVERIFIED — not confirmed that Dirigera speakers
                expose their own isOn attribute. See speaker.py's
                _make_power_switch() docstring.
            'NEXT'       → playback: 'playbackNext'
            'PREVIOUS'   → playback: 'playbackPrevious'
                CONFIRMED against a real, maintained Dirigera client
                library's working command API — these are the exact
                wire values, including the 'playback' prefix.
            integer string 0-100 → volume: int(value)
                CONFIRMED — Dirigera's own setVolume() API takes an
                integer 0-100 directly. This matches HA's 'number'
                domain, which publishes the raw selected value (not
                the old 0.0-1.0 float media_player convention), so no
                conversion is needed in either direction anymore.

        Args:
            logical_id (str):      Dirigera logical device id.
            command_payload (str): HA command payload string.

        Returns:
            CommandPayload | None
        """

        payload = command_payload.strip()

        if payload.upper() == "ON":
            return CommandPayload(logical_id, {"isOn": True})

        if payload.upper() == "OFF":
            return CommandPayload(logical_id, {"isOn": False})

        if payload.upper() == "NEXT":
            return CommandPayload(logical_id, {"playback": "playbackNext"})

        if payload.upper() == "PREVIOUS":
            return CommandPayload(logical_id, {"playback": "playbackPrevious"})

        # ── Volume (HA 'number' domain publishes the raw 0-100 value) ────
        try:
            vol = int(float(payload))
            if 0 <= vol <= 100:
                return CommandPayload(logical_id, {"volume": vol})
        except ValueError:
            pass

        logger.debug(
            "_map_speaker_command: unrecognised payload %r for %s",
            command_payload,
            logical_id,
        )
        return None


# ── Module-level pure helpers ─────────────────────────────────────────────────


def _try_parse_json(payload: str) -> Optional[Any]:
    """
    Attempt to parse a string as JSON.

    Returns the parsed value on success, None on failure.
    Never raises — used defensively when a payload may or may not
    be JSON.

    Args:
        payload (str): String to attempt JSON parsing on.

    Returns:
        Any | None: Parsed JSON value or None.
    """

    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
