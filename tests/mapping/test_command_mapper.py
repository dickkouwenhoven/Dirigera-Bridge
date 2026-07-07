"""
tests/mapping/test_command_mapper.py

Tests for app/mapping/command_mapper.py

Covers:
    - CommandPayload NamedTuple fields
    - map_command() — validation (empty logical_id, device_type, non-str payload)
    - map_command() — read-only device types return None
    - map_command() — unknown device type returns None
    - map_command() — light: ON/OFF, brightness, color_temp, HS colour, combined
    - map_command() — switch/outlet: ON/OFF
    - map_command() — blind: OPEN/CLOSE/STOP, position inversion
    - map_command() — airPurifier: ON/OFF, auto preset, percentages
    - map_command() — speaker: ON/OFF, playback, volume
    - _pct_to_fan_mode() boundaries
    - _try_parse_json() safe parsing
"""

import json

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.mapping.command_mapper import CommandMapper, CommandPayload


@pytest.fixture
def mapper():
    return CommandMapper()


# ── CommandPayload ─────────────────────────────────────────────────────────────


class TestCommandPayload:
    @pytest.mark.unit
    def test_is_named_tuple(self):
        """CommandPayload is a NamedTuple with logical_id and attributes."""
        cp = CommandPayload(
            logical_id="dev_1",
            attributes={"isOn": True},
        )
        assert cp.logical_id == "dev_1"
        assert cp.attributes == {"isOn": True}

    @pytest.mark.unit
    def test_unpacking(self):
        """CommandPayload supports tuple unpacking."""
        lid, attrs = CommandPayload("dev_1", {"isOn": False})
        assert lid == "dev_1"
        assert attrs == {"isOn": False}


# ── Validation ────────────────────────────────────────────────────────────────


class TestMapCommandValidation:
    @pytest.mark.unit
    def test_empty_logical_id_raises(self, mapper):
        """Empty logical_id raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_command("", "light", "ON")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_whitespace_logical_id_raises(self, mapper):
        """Whitespace-only logical_id raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_command("   ", "light", "ON")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_device_type_raises(self, mapper):
        """Empty device_type raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_command("dev_1", "", "ON")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_non_string_payload_raises(self, mapper):
        """Non-string command_payload raises MAPPING_INVALID_COMMAND."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_command("dev_1", "light", 123)
        assert exc_info.value.code == ErrorCode.MAPPING_INVALID_COMMAND

    @pytest.mark.unit
    def test_none_payload_raises(self, mapper):
        """None command_payload raises MAPPING_INVALID_COMMAND."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_command("dev_1", "light", None)
        assert exc_info.value.code == ErrorCode.MAPPING_INVALID_COMMAND

    @pytest.mark.unit
    def test_unknown_device_type_returns_none(self, mapper):
        """Unknown deviceType returns None."""
        result = mapper.map_command("dev_1", "unknownType", "ON")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "device_type",
        [
            "motionSensor",
            "waterSensor",
            "lightSensor",
            "environmentSensor",
            "lightController",
            "button",
            "shortcutController",
            "gateway",
        ],
    )
    def test_read_only_device_types_return_none(self, mapper, device_type):
        """Read-only device types return None for any command."""
        result = mapper.map_command("dev_1", device_type, "ON")
        assert result is None, f"{device_type} should return None (read-only)"


# ── light ─────────────────────────────────────────────────────────────────────


class TestMapCommandLight:
    LID = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

    @pytest.mark.unit
    def test_on_command(self, mapper):
        """'ON' maps to isOn: True."""
        result = mapper.map_command(self.LID, "light", "ON")
        assert result is not None
        assert result.logical_id == self.LID
        assert result.attributes == {"isOn": True}

    @pytest.mark.unit
    def test_off_command(self, mapper):
        """'OFF' maps to isOn: False."""
        result = mapper.map_command(self.LID, "light", "OFF")
        assert result.attributes == {"isOn": False}

    @pytest.mark.unit
    def test_on_case_insensitive(self, mapper):
        """'on' (lowercase) is accepted."""
        result = mapper.map_command(self.LID, "light", "on")
        assert result.attributes == {"isOn": True}

    @pytest.mark.unit
    def test_json_brightness(self, mapper):
        """JSON brightness 128 → lightLevel 50 (128/255*100)."""
        payload = json.dumps({"brightness": 128})
        result = mapper.map_command(self.LID, "light", payload)
        assert result is not None
        assert result.attributes["lightLevel"] == 50

    @pytest.mark.unit
    def test_json_brightness_max(self, mapper):
        """JSON brightness 255 → lightLevel 100."""
        payload = json.dumps({"brightness": 255})
        result = mapper.map_command(self.LID, "light", payload)
        assert result.attributes["lightLevel"] == 100

    @pytest.mark.unit
    def test_json_brightness_min(self, mapper):
        """JSON brightness 0 → lightLevel 1 (clamped to minimum)."""
        payload = json.dumps({"brightness": 0})
        result = mapper.map_command(self.LID, "light", payload)
        assert result.attributes["lightLevel"] == 1

    @pytest.mark.unit
    def test_json_color_temp_mireds_to_kelvin(self, mapper):
        """JSON color_temp 250 mireds → colorTemperature 4000K."""
        payload = json.dumps({"color_temp": 250})
        result = mapper.map_command(self.LID, "light", payload)
        assert result is not None
        assert result.attributes["colorTemperature"] == 4000

    @pytest.mark.unit
    def test_json_color_temp_454_mireds(self, mapper):
        """JSON color_temp 454 mireds → colorTemperature ~2203K."""
        payload = json.dumps({"color_temp": 454})
        result = mapper.map_command(self.LID, "light", payload)
        kelvin = result.attributes["colorTemperature"]
        # 1_000_000 / 454 ≈ 2203
        assert 2200 <= kelvin <= 2210

    @pytest.mark.unit
    def test_json_hs_colour(self, mapper):
        """JSON HS colour maps to colorHue and colorSaturation."""
        payload = json.dumps({"color": {"h": 120.0, "s": 80.0}})
        result = mapper.map_command(self.LID, "light", payload)
        assert result is not None
        assert result.attributes["colorHue"] == 120.0
        assert abs(result.attributes["colorSaturation"] - 0.8) < 0.001

    @pytest.mark.unit
    def test_json_combined_state_and_brightness(self, mapper):
        """JSON with state + brightness produces combined attributes."""
        payload = json.dumps({"state": "ON", "brightness": 255})
        result = mapper.map_command(self.LID, "light", payload)
        assert result.attributes["isOn"] is True
        assert result.attributes["lightLevel"] == 100

    @pytest.mark.unit
    def test_json_state_off(self, mapper):
        """JSON state OFF maps to isOn: False."""
        payload = json.dumps({"state": "OFF"})
        result = mapper.map_command(self.LID, "light", payload)
        assert result.attributes["isOn"] is False

    @pytest.mark.unit
    def test_plain_brightness_integer(self, mapper):
        """Plain integer string (1-100) maps to lightLevel."""
        result = mapper.map_command(self.LID, "light", "75")
        assert result is not None
        assert result.attributes["lightLevel"] == 75

    @pytest.mark.unit
    def test_unrecognised_payload_returns_none(self, mapper):
        """Unrecognised payload returns None."""
        result = mapper.map_command(self.LID, "light", "TOGGLE")
        assert result is None


# ── switch / outlet ───────────────────────────────────────────────────────────


class TestMapCommandSwitch:
    LID = "switch_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["switch", "outlet"])
    def test_on_command(self, mapper, device_type):
        """'ON' maps to isOn: True for switch and outlet."""
        result = mapper.map_command(self.LID, device_type, "ON")
        assert result is not None
        assert result.attributes == {"isOn": True}

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["switch", "outlet"])
    def test_off_command(self, mapper, device_type):
        """'OFF' maps to isOn: False."""
        result = mapper.map_command(self.LID, device_type, "OFF")
        assert result.attributes == {"isOn": False}

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["switch", "outlet"])
    def test_case_insensitive(self, mapper, device_type):
        """'on' (lowercase) is accepted."""
        result = mapper.map_command(self.LID, device_type, "on")
        assert result.attributes == {"isOn": True}

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["switch", "outlet"])
    def test_unrecognised_returns_none(self, mapper, device_type):
        """Unrecognised payload returns None."""
        result = mapper.map_command(self.LID, device_type, "TOGGLE")
        assert result is None


# ── blind ─────────────────────────────────────────────────────────────────────


class TestMapCommandBlind:
    LID = "blind_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["blind", "blinds"])
    def test_open_command(self, mapper, device_type):
        """'OPEN' maps to currentLevel: 0 (Dirigera fully open)."""
        result = mapper.map_command(self.LID, device_type, "OPEN")
        assert result is not None
        assert result.attributes == {"currentLevel": 0}

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["blind", "blinds"])
    def test_close_command(self, mapper, device_type):
        """'CLOSE' maps to currentLevel: 100 (Dirigera fully closed)."""
        result = mapper.map_command(self.LID, device_type, "CLOSE")
        assert result.attributes == {"currentLevel": 100}

    @pytest.mark.unit
    def test_stop_returns_none(self, mapper):
        """'STOP' returns None (no Dirigera equivalent)."""
        result = mapper.map_command(self.LID, "blind", "STOP")
        assert result is None

    @pytest.mark.unit
    def test_position_inversion_75(self, mapper):
        """HA 75 (open) → Dirigera 25."""
        result = mapper.map_command(self.LID, "blind", "75")
        assert result.attributes == {"currentLevel": 25}

    @pytest.mark.unit
    def test_position_inversion_0(self, mapper):
        """HA 0 (closed) → Dirigera 100."""
        result = mapper.map_command(self.LID, "blind", "0")
        assert result.attributes == {"currentLevel": 100}

    @pytest.mark.unit
    def test_position_inversion_100(self, mapper):
        """HA 100 (open) → Dirigera 0."""
        result = mapper.map_command(self.LID, "blind", "100")
        assert result.attributes == {"currentLevel": 0}

    @pytest.mark.unit
    def test_position_inversion_midpoint(self, mapper):
        """HA 50 → Dirigera 50 (midpoint is symmetric)."""
        result = mapper.map_command(self.LID, "blind", "50")
        assert result.attributes == {"currentLevel": 50}

    @pytest.mark.unit
    def test_case_insensitive_open(self, mapper):
        """'open' (lowercase) is accepted."""
        result = mapper.map_command(self.LID, "blind", "open")
        assert result.attributes == {"currentLevel": 0}


# ── airPurifier ───────────────────────────────────────────────────────────────


class TestMapCommandAirPurifier:
    LID = "air_purifier_abc_1"

    @pytest.mark.unit
    def test_on_command(self, mapper):
        """'ON' maps to fanMode: 'low'."""
        result = mapper.map_command(self.LID, "airPurifier", "ON")
        assert result is not None
        assert result.attributes == {"fanMode": "low"}

    @pytest.mark.unit
    def test_off_command(self, mapper):
        """'OFF' maps to fanMode: 'off'."""
        result = mapper.map_command(self.LID, "airPurifier", "OFF")
        assert result.attributes == {"fanMode": "off"}

    @pytest.mark.unit
    def test_auto_preset(self, mapper):
        """'auto' maps to fanMode: 'auto'."""
        result = mapper.map_command(self.LID, "airPurifier", "auto")
        assert result.attributes == {"fanMode": "auto"}

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "pct,expected_mode",
        [
            ("0", "off"),
            ("1", "low"),
            ("33", "low"),
            ("34", "medium"),
            ("66", "medium"),
            ("67", "high"),
            ("100", "high"),
        ],
    )
    def test_percentage_to_fan_mode(self, mapper, pct, expected_mode):
        """Fan percentages map to correct fanMode strings."""
        result = mapper.map_command(self.LID, "airPurifier", pct)
        assert result is not None
        assert result.attributes == {"fanMode": expected_mode}, (
            f"pct={pct} expected {expected_mode}"
        )

    @pytest.mark.unit
    def test_case_insensitive_off(self, mapper):
        """'off' (lowercase) is accepted."""
        result = mapper.map_command(self.LID, "airPurifier", "off")
        assert result.attributes == {"fanMode": "off"}

    @pytest.mark.unit
    def test_unrecognised_returns_none(self, mapper):
        """Unrecognised payload returns None."""
        result = mapper.map_command(self.LID, "airPurifier", "turbo")
        assert result is None


# ── speaker ───────────────────────────────────────────────────────────────────


class TestMapCommandSpeaker:
    """
    Tests the payload routing for speaker.py's composed entities
    (switch, number, two buttons) — see command_mapper.py's
    _map_speaker_command() docstring for which parts are CONFIRMED
    against real sources vs ASSUMED/UNVERIFIED.
    """

    LID = "speaker_abc_1"

    @pytest.mark.unit
    def test_on_command(self, mapper):
        """'ON' (from the power switch) maps to isOn: True."""
        result = mapper.map_command(self.LID, "speaker", "ON")
        assert result.attributes == {"isOn": True}

    @pytest.mark.unit
    def test_off_command(self, mapper):
        """'OFF' (from the power switch) maps to isOn: False."""
        result = mapper.map_command(self.LID, "speaker", "OFF")
        assert result.attributes == {"isOn": False}

    @pytest.mark.unit
    def test_next_command(self, mapper):
        """'NEXT' (from the next-track button) maps to playbackNext."""
        result = mapper.map_command(self.LID, "speaker", "NEXT")
        assert result.attributes == {"playback": "playbackNext"}

    @pytest.mark.unit
    def test_previous_command(self, mapper):
        """'PREVIOUS' (from the previous-track button) maps to playbackPrevious."""
        result = mapper.map_command(self.LID, "speaker", "PREVIOUS")
        assert result.attributes == {"playback": "playbackPrevious"}

    @pytest.mark.unit
    def test_next_previous_case_insensitive(self, mapper):
        """Button payloads are matched case-insensitively."""
        result = mapper.map_command(self.LID, "speaker", "next")
        assert result.attributes == {"playback": "playbackNext"}

    @pytest.mark.unit
    def test_volume_mid(self, mapper):
        """Volume 45 (from the number entity, raw HA value) → 45 (Dirigera)."""
        result = mapper.map_command(self.LID, "speaker", "45")
        assert result is not None
        assert result.attributes == {"volume": 45}

    @pytest.mark.unit
    def test_volume_zero(self, mapper):
        """Volume 0 → 0."""
        result = mapper.map_command(self.LID, "speaker", "0")
        assert result.attributes == {"volume": 0}

    @pytest.mark.unit
    def test_volume_full(self, mapper):
        """Volume 100 → 100."""
        result = mapper.map_command(self.LID, "speaker", "100")
        assert result.attributes == {"volume": 100}

    @pytest.mark.unit
    def test_volume_out_of_range_rejected(self, mapper):
        """Volume outside 0-100 is not a valid number-entity value."""
        result = mapper.map_command(self.LID, "speaker", "150")
        assert result is None

    @pytest.mark.unit
    def test_unrecognised_returns_none(self, mapper):
        """Unrecognised payload returns None."""
        result = mapper.map_command(self.LID, "speaker", "shuffle")
        assert result is None


# ── logical_id passthrough ────────────────────────────────────────────────────


class TestCommandPayloadLogicalId:
    @pytest.mark.unit
    def test_logical_id_passed_through(self, mapper):
        """The returned CommandPayload carries the original logical_id."""
        lid = "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1"
        result = mapper.map_command(lid, "switch", "ON")
        assert result.logical_id == lid

    @pytest.mark.unit
    def test_logical_id_not_modified(self, mapper):
        """logical_id is not modified (hyphens kept — not our job here)."""
        lid = "abc-123_1"
        result = mapper.map_command(lid, "switch", "ON")
        assert result.logical_id == "abc-123_1"
