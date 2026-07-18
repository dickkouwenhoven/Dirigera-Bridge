"""
state_mapper.py

Translates Dirigera WebSocket state change events into Home Assistant
MQTT state payloads.

Role & Responsibility:
    Owns the translation from a raw Dirigera attribute change (received
    from the WebSocket via the EventBus) to the exact string or JSON
    payload that must be published to the entity's HA MQTT state topic.

    Every device type has different attributes and different HA state
    conventions. This module encodes all of those translations in one
    place so the orchestrator and ha_client.py never need to know the
    details of any individual device type.

What it does:
    - Receives a (logical_id, device_type, attribute, value) tuple
    - Looks up the correct state translator for the device_type
    - Returns a (state_payload, entity_unique_id) pair that the
      orchestrator passes directly to ha_client.update_state()
    - Returns None if the attribute is not mapped for that device type
      (e.g. internal Dirigera attributes that HA does not need to know)
    - Handles all type conversions:
        bool    → 'ON' / 'OFF'  for switch/binary_sensor
        int/float → str          for sensor values
        Dirigera position → HA position (blind inversion)
        fanMode string → percentage for air purifier
        (speaker volume is passed straight through as an int 0-100 —
         no conversion needed since it uses HA's 'number' domain)

Arguments / Configuration:
    No runtime configuration. All methods are pure functions —
    no state, no I/O, no async.

Used by:
    - app/orchestrator.py  (calls map_state() on every STATE_CHANGED
                            event from the WebSocket)

Not responsible for:
    - Publishing to MQTT (ha_client.py)
    - Caching state (state_cache.py)
    - Entity registration (device_mapper.py / ha_client.py)
    - Receiving WebSocket events (websocket_client.py)

Design notes:
    - map_state() returns Optional[StatePayload]. None means "this
      attribute change should not be forwarded to HA" — used for
      Dirigera-internal attributes that are not meaningful in HA
      (e.g. identifyStarted, permittingJoin, otaProgress).
    - StatePayload is a NamedTuple: (unique_id, payload).
      unique_id identifies which entity to update.
      payload is always a str (never dict) — MQTT payloads are strings.
    - The unique_id in the payload is built using make_unique_id() from
      domains/__init__.py to ensure consistency with entity registration.
    - Blind position inversion: Dirigera 0=open/100=closed,
      HA 0=closed/100=open. Inversion formula: ha_pos = 100 - dirigera_pos
    - Speaker volume: passed straight through as an int 0-100 — no
      conversion needed, since the composed 'number' entity in
      speaker.py uses Dirigera's native 0-100 range directly (see
      app/mapping/domains/speaker.py for why speaker uses a
      composition of primitive entities rather than a media_player).
    - Fan speed: Dirigera fanMode string → HA percentage.
      Mapping: off=0, low=25, medium=50, high=75, auto=None (preset),
               customSpeed uses motorSpeed attribute directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, NamedTuple, Optional

from ..core.errors import DirigeraBridgeError, ErrorCode
from .domains import make_unique_id

__all__ = [
    "StatePayload",
    "StateMapper",
]

logger = logging.getLogger(__name__)


# ── StatePayload ──────────────────────────────────────────────────────────────


class StatePayload(NamedTuple):
    """
    Result of a successful state mapping operation.

    Fields:
        unique_id (str): The HA entity unique_id to update.
                         Built with make_unique_id() to match the
                         id used at entity registration time.
        payload (str):   The MQTT state payload string to publish.
                         Always a string — never a dict or bytes.
    """

    unique_id: str
    payload: str


# ── Fan mode mapping ──────────────────────────────────────────────────────────

# Dirigera fanMode string → HA fan percentage (0 = off)
_FAN_MODE_TO_PCT: Dict[str, int] = {
    "off": 0,
    "low": 25,
    "medium": 50,
    "high": 75,
    "customSpeed": -1,  # handled separately via motorSpeed
}


# ── StateMapper ───────────────────────────────────────────────────────────────


class StateMapper:
    """
    Translates Dirigera attribute changes to HA MQTT state payloads.

    All methods are pure — no state, no I/O, no async. Instantiate
    once and inject into the orchestrator.
    """

    # ── Public API ────────────────────────────────────────────────────────

    def map_state(
        self,
        logical_id: str,
        device_type: str,
        attribute: str,
        value: Any,
        device_atrributes: Optional[Dict[str, Any]] = None,
    ) -> Optional[StatePayload]:
        """
        Translate a Dirigera attribute change to an HA state payload.

        Args:
            logical_id (str):   Dirigera logical device id.
            device_type (str):  Dirigera deviceType string for routing.
            attribute (str):    camelCase attribute name that changed.
            value (Any):        New attribute value from Dirigera.

        Returns:
            StatePayload | None: (unique_id, payload) if this attribute
                                  should be forwarded to HA, or None if
                                  it should be silently ignored.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                                 or device_type are not non-empty strings.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "map_state: logical_id must be a non-empty string",
            )

        if not isinstance(device_type, str) or not device_type.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "map_state: device_type must be a non-empty string",
            )

        # ── Route by device type ──────────────────────────────────────────
        try:
            if device_type == "light":
                return self._map_light_state(
                    logical_id, attribute, device_attributevalues or {}
                )

            if device_type == "outlet":
                return self._map_outlet_state(logical_id, attribute, value)

            if device_type in ("motionSensor",):
                return self._map_motion_sensor_state(logical_id, attribute, value)

            if device_type == "waterSensor":
                return self._map_water_sensor_state(logical_id, attribute, value)

            if device_type == "lightSensor":
                return self._map_light_sensor_state(logical_id, attribute, value)

            if device_type == "environmentSensor":
                return self._map_environment_sensor_state(logical_id, attribute, value)

            if device_type == "lightController":
                return self._map_controller_state(logical_id, attribute, value)

            if device_type in ("button", "shortcutController"):
                return self._map_button_state(logical_id, attribute, value)

            if device_type in ("blind", "blinds"):
                return self._map_blind_state(logical_id, attribute, value)

            if device_type == "airPurifier":
                return self._map_air_purifier_state(logical_id, attribute, value)

            if device_type == "speaker":
                return self._map_speaker_state(logical_id, attribute, value)

            if device_type == "gateway":
                return self._map_gateway_state(logical_id, attribute, value)

            if device_type in ("switch",):
                return self._map_switch_state(logical_id, attribute, value)

        except Exception as exc:
            logger.error(
                "map_state: unexpected error mapping %s.%s for device_type=%s: %s",
                logical_id,
                attribute,
                device_type,
                exc,
            )
            return None

        # Unknown device type — silently ignore
        logger.debug(
            "map_state: no handler for device_type='%s' (logical_id=%s, attribute=%s)",
            device_type,
            logical_id,
            attribute,
        )
        return None

    # ── Internal translators — per device type ────────────────────────────

    @staticmethod
    def _map_light_state(
        logical_id: str,
        attribute: str,
        device_attributes: Dict[str, Any],
    ) -> Optional[StatePayload]:
        """
        Translate a light attribute change into HA's merged JSON
        light state payload.

        Every light now uses HA's MQTT light JSON schema (see
        light.py's "MQTT light schema" note), which means the ENTIRE
        current light state must be published as one JSON object to
        the single state_topic on every change — HA treats each
        JSON-schema state message as a full snapshot, not an
        incremental patch. Publishing only the single changed
        attribute (e.g. just {"brightness": 128}) would omit "state"
        entirely and could make HA lose track of previously-known
        fields. This replaced an earlier version that published each
        attribute as its own separate payload (plain ON/OFF for isOn,
        a bare number for lightLevel, partial JSON for colorHue/
        colorSaturation) — a real bug found against a live HA
        instance, where the icon never tracked the real device state
        because none of those payloads matched what HA's JSON schema
        actually expects to receive.

        device_attributes is the FULL current known attribute dict
        for this device — map_state()'s caller (orchestrator.py)
        supplies self._state_cache.get_device_state(logical_id),
        which already includes the just-changed attribute (the cache
        is updated before map_state() is called) plus every other
        attribute seen for this device so far. This keeps map_state()
        a pure function: given all currently-known facts about the
        device, it deterministically builds the complete snapshot —
        no hidden state inside state_mapper.py itself.

        Handled attributes (each just triggers a full-snapshot
        rebuild from device_attributes, not a per-attribute payload):
            isOn             → JSON "state": 'ON' / 'OFF'
            lightLevel       → JSON "brightness": int (1-100)
            colorTemperature → JSON "color_temp": int mireds
                               (Kelvin → mireds, matching the
                               min_mireds/max_mireds light.py already
                               declares in discovery)
            colorHue / colorSaturation → JSON "color": {"h", "s"}
                               (Dirigera saturation is 0.0-1.0; HA's
                               JSON schema "s" is 0-100, so *100)
            colorMode        → ignored (HA derives this from which
                               fields are present in "color" vs
                               "color_temp")

        Only fields Dirigera has actually reported are included, so
        a light that has never reported e.g. colorHue/colorSaturation
        simply omits "color" rather than guessing a value.
        """

        uid = make_unique_id(logical_id)

        handled_attrs = {
            "isOn",
            "lightLevel",
            "colorTemperature",
            "colorHue",
            "colorSaturation",
        }

        if attribute not in handled_attrs:
            if attribute in (
                "colorMode",
                "startupOnOff",
                "startUpCurrentLevel",
                "startupTemperature",
                "identifyStarted",
                "identifyPeriod",
                "permittingJoin",
                "otaStatus",
                "otaState",
                "otaProgress",
                "otaPolicy",
                "otaScheduleStart",
                "otaScheduleEnd",
            ):
                return None  # Internal Dirigera fields — do not forward

            logger.debug(
                "_map_light_state: unhandled attribute '%s' for %s",
                attribute,
                logical_id,
            )
            return None

        json_state: Dict[str, Any] = {}

        if "isOn" in device_attributes:
            json_state["state"] = _bool_to_onoff(device_attributes["isOn"])

        if "lightLevel" in device_attributes:
            try:
                json_state["brightness"] = int(device_attributes["lightLevel"])
            except (TypeError, ValueError):
                pass

        if "colorTemperature" in device_attributes:
            try:
                kelvin = float(device_attributes["colorTemperature"])
                if kelvin > 0:
                    json_state["color_temp"] = round(1_000_000 / kelvin)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        hue = device_attributes.get("colorHue")
        sat = device_attributes.get("colorSaturation")
        if hue is not None and sat is not None:
            try:
                json_state["color"] = {
                    "h": float(hue),
                    "s": float(sat) * 100.0,  # Dirigera 0.0-1.0 → HA 0-100
                }
            except (TypeError, ValueError):
                pass

        if not json_state:
            return None

        return StatePayload(uid, json.dumps(json_state))

    @staticmethod
    def _map_outlet_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate outlet attribute changes to HA MQTT payloads.

        Handled attributes:
            isOn                → 'ON'/'OFF' on switch entity
            currentActivePower  → str(value) on power sensor
            currentVoltage      → str(value) on voltage sensor
            currentAmps         → str(value) on current sensor
            totalEnergyConsumed → str(value) on energy sensor
        """

        if attribute == "isOn":
            return StatePayload(
                make_unique_id(logical_id),
                _bool_to_onoff(value),
            )

        if attribute == "currentActivePower":
            return StatePayload(
                make_unique_id(logical_id, "power"),
                _format_float(value),
            )

        if attribute == "currentVoltage":
            return StatePayload(
                make_unique_id(logical_id, "voltage"),
                _format_float(value),
            )

        if attribute == "currentAmps":
            return StatePayload(
                make_unique_id(logical_id, "current"),
                _format_float(value),
            )

        if attribute == "totalEnergyConsumed":
            return StatePayload(
                make_unique_id(logical_id, "energy"),
                _format_float(value),
            )

        if attribute in (
            "lightLevel",
            "startupOnOff",
            "startUpCurrentLevel",
            "childLock",
            "statusLight",
            "totalEnergyConsumedLastUpdated",
            "energyConsumedAtLastReset",
            "timeOfLastEnergyReset",
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_outlet_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_motion_sensor_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate motionSensor attribute changes to HA MQTT payloads.

        Handled attributes:
            isDetected        → 'ON'/'OFF' on binary_sensor entity
            batteryPercentage → str(value) on battery sensor entity
        """

        if attribute == "isDetected":
            return StatePayload(
                make_unique_id(logical_id),
                _bool_to_onoff(value),
            )

        if attribute == "batteryPercentage":
            return StatePayload(
                make_unique_id(logical_id, "battery"),
                str(int(value)),
            )

        if attribute in (
            "isOn",
            "motionDetectedDelay",
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "sensorConfig",
            "circadianPresets",
        ):
            return None

        logger.debug(
            "_map_motion_sensor_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_water_sensor_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate waterSensor attribute changes to HA MQTT payloads.

        Handled attributes:
            waterLeakDetected → 'ON'/'OFF' on binary_sensor entity
            batteryPercentage → str(value) on battery sensor entity
        """

        if attribute == "waterLeakDetected":
            return StatePayload(
                make_unique_id(logical_id),
                _bool_to_onoff(value),
            )

        if attribute == "batteryPercentage":
            return StatePayload(
                make_unique_id(logical_id, "battery"),
                str(int(value)),
            )

        if attribute in (
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_water_sensor_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_light_sensor_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate lightSensor (VALLHORN _3 sibling) attribute changes.

        Handled attributes:
            illuminance → str(value) lux on illuminance sensor entity
        """

        if attribute == "illuminance":
            return StatePayload(
                make_unique_id(logical_id, "illuminance"),
                str(int(value)),
            )

        if attribute in ("identifyStarted", "identifyPeriod", "permittingJoin"):
            return None

        logger.debug(
            "_map_light_sensor_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_environment_sensor_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate environmentSensor (VINDSTYRKA) attribute changes.

        Handled attributes:
            currentTemperature → str(value) °C
            currentRH          → str(value) %
            currentPM25        → str(value) µg/m³
            vocIndex           → str(value) index
        """

        if attribute == "currentTemperature":
            return StatePayload(
                make_unique_id(logical_id, "temperature"),
                _format_float(value),
            )

        if attribute == "currentRH":
            return StatePayload(
                make_unique_id(logical_id, "humidity"),
                _format_float(value),
            )

        if attribute == "currentPM25":
            return StatePayload(
                make_unique_id(logical_id, "pm25"),
                _format_float(value),
            )

        if attribute == "vocIndex":
            return StatePayload(
                make_unique_id(logical_id, "voc"),
                str(int(value)),
            )

        if attribute in (
            "maxMeasuredPM25",
            "minMeasuredPM25",
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_environment_sensor_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_controller_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate lightController (remote) attribute changes.

        Remote controllers emit canSend events (isOn, lightLevel).
        These represent button presses — forwarded as event payloads.

        Handled attributes:
            isOn       → event payload JSON {'event_type': 'shortRelease'
                          or 'shortRelease_off'}
            lightLevel → None (internal dimming step, not forwarded)
            batteryPercentage → str(value) on battery sensor
        """

        if attribute == "isOn":
            event_type = "shortRelease" if value else "shortRelease_off"
            return StatePayload(
                make_unique_id(logical_id),
                json.dumps({"event_type": event_type}),
            )

        if attribute == "batteryPercentage":
            return StatePayload(
                make_unique_id(logical_id, "battery"),
                str(int(value)),
            )

        if attribute in (
            "lightLevel",
            "circadianPresets",
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_controller_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_button_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate button / shortcutController attribute changes.

        Handled attributes:
            isOn              → event payload JSON
            batteryPercentage → str(value) on battery sensor
        """

        if attribute == "isOn":
            event_type = "shortRelease" if value else "shortRelease_off"
            return StatePayload(
                make_unique_id(logical_id),
                json.dumps({"event_type": event_type}),
            )

        if attribute == "batteryPercentage":
            return StatePayload(
                make_unique_id(logical_id, "battery"),
                str(int(value)),
            )

        if attribute in ("identifyStarted", "identifyPeriod", "permittingJoin"):
            return None

        logger.debug(
            "_map_button_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_blind_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate blind attribute changes to HA MQTT payloads.

        Position inversion: Dirigera 0=open/100=closed,
        HA 0=closed/100=open. Formula: ha_pos = 100 - dirigera_pos

        Handled attributes:
            currentLevel       → inverted position str
            blindsCurrentLevel → inverted position str (alternate attr)
            batteryPercentage  → str(value) on battery sensor
        """

        uid = make_unique_id(logical_id)

        if attribute in ("currentLevel", "blindsCurrentLevel"):
            try:
                dirigera_pos = int(value)
                ha_pos = 100 - dirigera_pos
                return StatePayload(uid, str(ha_pos))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "_map_blind_state: invalid position value '%s' for %s: %s",
                    value,
                    logical_id,
                    exc,
                )
                return None

        if attribute == "batteryPercentage":
            return StatePayload(
                make_unique_id(logical_id, "battery"),
                str(int(value)),
            )

        if attribute in (
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_blind_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_air_purifier_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate airPurifier attribute changes to HA MQTT payloads.

        Handled attributes:
            fanMode        → percentage str or 'auto' preset
            fanSensorPM25  → str(value) µg/m³
            filterLifetime → str(value) %
            motorSpeed     → used only when fanMode is 'customSpeed'
        """

        uid = make_unique_id(logical_id)

        if attribute == "fanMode":
            pct = _FAN_MODE_TO_PCT.get(str(value))
            if pct is None:
                # Unknown mode — forward as-is
                return StatePayload(uid, str(value))
            if pct == -1:
                # customSpeed — payload sent separately via motorSpeed
                return None
            return StatePayload(uid, str(pct))

        if attribute == "motorSpeed":
            # customSpeed mode — use motor speed directly as percentage
            # Clamp to 1-100 range
            try:
                pct = max(1, min(100, int(value)))
                return StatePayload(uid, str(pct))
            except (TypeError, ValueError):
                return None

        if attribute == "fanSensorPM25":
            return StatePayload(
                make_unique_id(logical_id, "pm25"),
                _format_float(value),
            )

        if attribute == "filterLifetime":
            return StatePayload(
                make_unique_id(logical_id, "filter"),
                str(int(value)),
            )

        if attribute in (
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_air_purifier_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_speaker_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate speaker (SYMFONISK) attribute changes to the
        composed entities defined in app/mapping/domains/speaker.py.

        speaker.py composes several small HA entities instead of a
        single media_player (HA's MQTT discovery has no media_player
        domain), so each attribute routes to a specific entity's
        unique_id suffix rather than a single bare unique_id:

            playback     → "playback" sensor, raw string passed
                           through unchanged. ASSUMED/UNVERIFIED: the
                           exact string vocabulary Dirigera sends is
                           not confirmed, so no attempt is made to
                           translate it into a fixed set of states —
                           see speaker.py's _make_playback_sensor()
                           docstring for details.
            volume       → "volume" number entity, raw int 0-100
                           passed through unchanged. CONFIRMED —
                           Dirigera's own API uses 0-100 directly,
                           matching HA's 'number' domain exactly (no
                           more /100.0 conversion to a 0.0-1.0 float,
                           since that was only needed for the old
                           media_player-style design).
            isOn         → "power" switch, 'ON'/'OFF'.
                           UNVERIFIED — see speaker.py's
                           _make_power_switch() docstring.
            isReachable  → "reachable" binary_sensor, 'ON'/'OFF'.
                           CONFIRMED present on every Dirigera device.
        """

        if attribute == "playback":
            # Forward the raw Dirigera string as-is — this is a plain
            # diagnostic sensor, not a media_player state machine, so
            # there is no fixed vocabulary to translate into.
            return StatePayload(
                make_unique_id(logical_id, "playback"),
                str(value),
            )

        if attribute == "volume":
            try:
                vol = int(value)
                return StatePayload(
                    make_unique_id(logical_id, "volume"),
                    str(vol),
                )
            except (TypeError, ValueError):
                return None

        if attribute == "isOn":
            return StatePayload(
                make_unique_id(logical_id, "power"),
                _bool_to_onoff(value),
            )

        if attribute == "isReachable":
            return StatePayload(
                make_unique_id(logical_id, "reachable"),
                _bool_to_onoff(value),
            )

        if attribute in (
            "playbackAudio",
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
        ):
            return None

        logger.debug(
            "_map_speaker_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_gateway_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate gateway attribute changes to HA MQTT payloads.

        Handled attributes:
            isReachable      → 'ON'/'OFF' on connectivity binary_sensor
            backendConnected → 'ON'/'OFF' on connectivity binary_sensor
            otaStatus        → str on OTA status sensor
            otaState         → str on OTA state sensor
            firmwareVersion  → str on firmware version sensor
            homeState        → str on home state sensor
            timezone         → str on timezone sensor
            nextSunRise      → ISO timestamp str on sunrise sensor
            nextSunSet       → ISO timestamp str on sunset sensor
            coordinates      → JSON str on device_tracker
        """

        if attribute == "isReachable":
            return StatePayload(
                make_unique_id(logical_id, "reachable"),
                _bool_to_onoff(value),
            )

        if attribute == "backendConnected":
            return StatePayload(
                make_unique_id(logical_id, "backend_connected"),
                _bool_to_onoff(value),
            )

        if attribute == "otaStatus":
            return StatePayload(
                make_unique_id(logical_id, "ota_status"),
                str(value),
            )

        if attribute == "otaState":
            return StatePayload(
                make_unique_id(logical_id, "ota_state"),
                str(value),
            )

        if attribute == "firmwareVersion":
            return StatePayload(
                make_unique_id(logical_id, "firmware_version"),
                str(value),
            )

        if attribute == "homeState":
            return StatePayload(
                make_unique_id(logical_id, "home_state"),
                str(value),
            )

        if attribute == "timezone":
            return StatePayload(
                make_unique_id(logical_id, "timezone"),
                str(value),
            )

        if attribute == "nextSunRise":
            return StatePayload(
                make_unique_id(logical_id, "next_sunrise"),
                str(value),
            )

        if attribute == "nextSunSet":
            return StatePayload(
                make_unique_id(logical_id, "next_sunset"),
                str(value),
            )

        if attribute == "coordinates":
            if isinstance(value, dict):
                lat = value.get("latitude")
                lon = value.get("longitude")
                acc = max(0, value.get("accuracy", 0))  # -1 → 0
                if lat is not None and lon is not None:
                    return StatePayload(
                        make_unique_id(logical_id, "location"),
                        json.dumps(
                            {
                                "latitude": lat,
                                "longitude": lon,
                                "gps_accuracy": acc,
                            }
                        ),
                    )
            return None

        if attribute in (
            "isOn",
            "logLevel",
            "coreDump",
            "backendConnectionPersistent",
            "backendOnboardingComplete",
            "backendRegion",
            "backendCountryCode",
            "userConsents",
            "nextSunRise",
            "permittingJoin",
            "identifyStarted",
            "identifyPeriod",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
            "homeState",
            "countryCode",
        ):
            return None

        logger.debug(
            "_map_gateway_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None

    @staticmethod
    def _map_switch_state(
        logical_id: str,
        attribute: str,
        value: Any,
    ) -> Optional[StatePayload]:
        """
        Translate switch attribute changes to HA MQTT payloads.

        Handled attributes:
            isOn → 'ON'/'OFF' on switch entity
        """

        if attribute == "isOn":
            return StatePayload(
                make_unique_id(logical_id),
                _bool_to_onoff(value),
            )

        if attribute in (
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaStatus",
            "otaState",
            "otaProgress",
            "otaPolicy",
            "otaScheduleStart",
            "otaScheduleEnd",
        ):
            return None

        logger.debug(
            "_map_switch_state: unhandled attribute '%s' for %s",
            attribute,
            logical_id,
        )
        return None


# ── Module-level pure helpers ─────────────────────────────────────────────────


def _bool_to_onoff(value: Any) -> str:
    """
    Convert a Dirigera boolean attribute value to 'ON' or 'OFF'.

    Accepts bool or any truthy/falsy value. Used for isOn,
    isDetected, waterLeakDetected, backendConnected, isReachable.

    Args:
        value: The attribute value to convert.

    Returns:
        str: 'ON' if truthy, 'OFF' if falsy.
    """
    return "ON" if value else "OFF"


def _format_float(value: Any, precision: int = 2) -> str:
    """
    Format a numeric value as a fixed-precision decimal string.

    Used for sensor values where HA expects a numeric string.
    Falls back to str(value) if conversion fails.

    Args:
        value:     The numeric value to format.
        precision: Decimal places to include. Default: 2.

    Returns:
        str: Formatted numeric string, e.g. '9.10', '226.60'.
    """
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return str(value)
