"""
tests/mapping/test_state_mapper.py

Tests for app/mapping/state_mapper.py

Covers:
    - StatePayload NamedTuple fields
    - map_state() — validation (empty logical_id, empty device_type)
    - map_state() — unknown device type returns None
    - map_state() — internal attributes return None (suppress forwarding)
    - map_state() — light: isOn, lightLevel, colorTemperature, colorHue/Sat
    - map_state() — outlet: isOn, all four energy sensors
    - map_state() — motionSensor: isDetected, batteryPercentage
    - map_state() — waterSensor: waterLeakDetected, batteryPercentage
    - map_state() — lightSensor: illuminance
    - map_state() — environmentSensor: all four measurements
    - map_state() — lightController: isOn → event payload
    - map_state() — button/shortcutController: isOn → event payload
    - map_state() — blind: position inversion (Dirigera 0=open → HA 100)
    - map_state() — airPurifier: fanMode → percentage, pm25, filter
    - map_state() — speaker: playback, volume normalization, isOn
    - map_state() — gateway: all ten entity types
    - map_state() — switch: isOn
    - _bool_to_onoff() helper
    - _format_float() helper
"""

import json
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.mapping.state_mapper import StateMapper, StatePayload


@pytest.fixture
def mapper() -> StateMapper:
    return StateMapper()


# ── StatePayload ──────────────────────────────────────────────────────────────


class TestStatePayload:
    @pytest.mark.unit
    def test_is_named_tuple(self) -> None:
        """StatePayload is a NamedTuple with unique_id and payload."""
        sp = StatePayload(unique_id="dirigera_abc_1", payload="ON")
        assert sp.unique_id == "dirigera_abc_1"
        assert sp.payload == "ON"

    @pytest.mark.unit
    def test_unpacking(self) -> None:
        """StatePayload supports tuple unpacking."""
        uid, payload = StatePayload("uid_1", "OFF")
        assert uid == "uid_1"
        assert payload == "OFF"


# ── Validation ────────────────────────────────────────────────────────────────


class TestMapStateValidation:
    @pytest.mark.unit
    def test_empty_logical_id_raises(self, mapper: StateMapper) -> None:
        """Empty logical_id raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_state("", "light", "isOn", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_whitespace_logical_id_raises(self, mapper: StateMapper) -> None:
        """Whitespace-only logical_id raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_state("   ", "light", "isOn", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_device_type_raises(self, mapper: StateMapper) -> None:
        """Empty device_type raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_state("dev_1", "", "isOn", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_unknown_device_type_returns_none(self, mapper: StateMapper) -> None:
        """Unknown deviceType returns None (silently ignored)."""
        result = mapper.map_state("dev_1", "unknownType", "isOn", True)
        assert result is None

    @pytest.mark.unit
    def test_internal_attribute_returns_none(self, mapper: StateMapper) -> None:
        """Internal Dirigera attributes return None (not forwarded)."""
        for attr in [
            "identifyStarted",
            "identifyPeriod",
            "permittingJoin",
            "otaProgress",
            "otaPolicy",
        ]:
            result = mapper.map_state("dev_1", "light", attr, True)
            assert result is None, f"{attr} should be suppressed"


# ── light ─────────────────────────────────────────────────────────────────────


class TestMapStateLight:
    LID = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

    @pytest.mark.unit
    def test_is_on_true(self, mapper: StateMapper) -> None:
        """isOn=True maps to payload 'ON'."""
        result = mapper.map_state(
            self.LID,
            "light",
            "isOn",
            True,
            device_attributes={"isOn": True},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert data["state"] == "ON"

    @pytest.mark.unit
    def test_is_on_false(self, mapper: StateMapper) -> None:
        """isOn=False maps to payload 'OFF'."""
        result = mapper.map_state(
            self.LID,
            "light",
            "isOn",
            False,
            device_attributes={"isOn": False},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert data["state"] == "OFF"

    @pytest.mark.unit
    def test_is_on_unique_id(self, mapper: StateMapper) -> None:
        """isOn unique_id is the primary entity (no suffix)."""
        result = mapper.map_state(
            self.LID,
            "light",
            "isOn",
            True,
            device_attributes={"isOn": True},
        )
        assert result is not None
        assert result.unique_id == "dirigera_" + self.LID.replace("-", "_")

    @pytest.mark.unit
    def test_light_level(self, mapper: StateMapper) -> None:
        """lightLevel maps to string integer payload."""
        result = mapper.map_state(
            self.LID,
            "light",
            "lightLevel",
            84,
            device_attributes={"isOn": True, "lightLevel": 84},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert data["brightness"] == 84

    @pytest.mark.unit
    def test_light_level_zero(self, mapper: StateMapper) -> None:
        """lightLevel=0 maps to '0' (not suppressed as falsy)."""
        result = mapper.map_state(
            self.LID,
            "light",
            "lightLevel",
            0,
            device_attributes={"isOn": True, "lightLevel": 0},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert data["brightness"] == 0

    @pytest.mark.unit
    def test_color_temperature(self, mapper: StateMapper) -> None:
        """colorTemperature maps to string integer Kelvin."""
        result = mapper.map_state(
            self.LID,
            "light",
            "colorTemperature",
            2967,
            device_attributes={"colorTemperature": 2967},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert "color_temp" in data
        assert data["color_temp"] == 337

    @pytest.mark.unit
    def test_color_hue_json_payload(self, mapper: StateMapper) -> None:
        result = mapper.map_state(
            self.LID,
            "light",
            "colorHue",
            29.999,
            device_attributes={
                "colorHue": 29.999,
                "colorSaturation": 0.641,
            },
        )
        assert result is not None
        data = json.loads(result.payload)
        assert "color" in data
        assert data["color"]["h"] == 29.999
        assert data["color"]["s"] == 64.1

    @pytest.mark.unit
    def test_color_saturation_json_payload(self, mapper: StateMapper) -> None:
        result = mapper.map_state(
            self.LID,
            "light",
            "colorSaturation",
            0.641,
            device_attributes={
                "colorHue": 29.999,
                "colorSaturation": 0.641,
            },
        )
        assert result is not None
        data = json.loads(result.payload)
        assert "color" in data
        assert data["color"]["h"] == 29.999
        assert data["color"]["s"] == 64.1

    @pytest.mark.unit
    def test_color_hue_without_saturation_is_ignored(self, mapper: StateMapper) -> None:
        """colorHue is not published until colorSaturation is known."""
        result = mapper.map_state(
            self.LID,
            "light",
            "colorHue",
            29.999,
            device_attributes={"colorHue": 29.999},
        )
        assert result is None

    @pytest.mark.unit
    def test_color_mode_suppressed(self, mapper: StateMapper) -> None:
        """colorMode is suppressed (internal Dirigera field)."""
        result = mapper.map_state(self.LID, "light", "colorMode", "color")
        assert result is None

    @pytest.mark.unit
    def test_ota_status_suppressed(self, mapper: StateMapper) -> None:
        """otaStatus is suppressed for light."""
        result = mapper.map_state(self.LID, "light", "otaStatus", "upToDate")
        assert result is None

    @pytest.mark.unit
    def test_non_positive_kelvin_is_omitted(self, mapper: StateMapper) -> None:
        """colorTemperature <= 0 skips color_temp entirely rather than
        dividing by a non-positive number."""
        result = mapper.map_state(
            self.LID,
            "light",
            "colorTemperature",
            0,
            device_attributes={"isOn": True, "colorTemperature": 0},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert "color_temp" not in data
        assert data["state"] == "ON"

    @pytest.mark.unit
    def test_non_numeric_kelvin_is_ignored(self, mapper: StateMapper) -> None:
        """A colorTemperature value that can't convert to float is
        caught and simply omitted, not raised."""
        result = mapper.map_state(
            self.LID,
            "light",
            "colorTemperature",
            "not-a-number",
            device_attributes={"isOn": True, "colorTemperature": "not-a-number"},
        )
        assert result is not None
        data = json.loads(result.payload)
        assert "color_temp" not in data


# ── outlet ────────────────────────────────────────────────────────────────────


class TestMapStateOutlet:
    LID = "0acd598b-6bcb-46ba-8aa0-0fd035b678f6_1"

    @pytest.mark.unit
    def test_is_on_true(self, mapper: StateMapper) -> None:
        """isOn=True maps to 'ON' on the switch entity."""
        result = mapper.map_state(self.LID, "outlet", "isOn", True)
        assert result is not None
        assert result.payload == "ON"
        # Primary entity — no suffix
        assert result.unique_id.endswith(self.LID.replace("-", "_"))

    @pytest.mark.unit
    def test_current_active_power(self, mapper: StateMapper) -> None:
        """currentActivePower maps to power sensor with float payload."""
        result = mapper.map_state(self.LID, "outlet", "currentActivePower", 9.1)
        assert result is not None
        assert result.unique_id.endswith("power")
        assert result.payload == "9.10"

    @pytest.mark.unit
    def test_current_voltage(self, mapper: StateMapper) -> None:
        """currentVoltage maps to voltage sensor."""
        result = mapper.map_state(self.LID, "outlet", "currentVoltage", 226.6)
        assert result is not None
        assert result.unique_id.endswith("voltage")
        assert result.payload == "226.60"

    @pytest.mark.unit
    def test_current_amps(self, mapper: StateMapper) -> None:
        """currentAmps maps to current sensor."""
        result = mapper.map_state(self.LID, "outlet", "currentAmps", 0.003)
        assert result is not None
        assert result.unique_id.endswith("current")
        assert result.payload == "0.00"

    @pytest.mark.unit
    def test_total_energy_consumed(self, mapper: StateMapper) -> None:
        """totalEnergyConsumed maps to energy sensor."""
        result = mapper.map_state(self.LID, "outlet", "totalEnergyConsumed", 2.97)
        assert result is not None
        assert result.unique_id.endswith("energy")
        assert result.payload == "2.97"

    @pytest.mark.unit
    def test_child_lock_suppressed(self, mapper: StateMapper) -> None:
        """childLock is suppressed for outlet."""
        result = mapper.map_state(self.LID, "outlet", "childLock", True)
        assert result is None


# ── motionSensor ──────────────────────────────────────────────────────────────


class TestMapStateMotionSensor:
    LID = "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1"

    @pytest.mark.unit
    def test_is_detected_true(self, mapper: StateMapper) -> None:
        """isDetected=True maps to 'ON'."""
        result = mapper.map_state(self.LID, "motionSensor", "isDetected", True)
        assert result is not None
        assert result.payload == "ON"

    @pytest.mark.unit
    def test_is_detected_false(self, mapper: StateMapper) -> None:
        """isDetected=False maps to 'OFF'."""
        result = mapper.map_state(self.LID, "motionSensor", "isDetected", False)
        assert result is not None
        assert result.payload == "OFF"

    @pytest.mark.unit
    def test_battery_percentage(self, mapper: StateMapper) -> None:
        """batteryPercentage maps to battery sensor."""
        result = mapper.map_state(self.LID, "motionSensor", "batteryPercentage", 70)
        assert result is not None
        assert result.unique_id.endswith("battery")
        assert result.payload == "70"

    @pytest.mark.unit
    def test_is_on_suppressed(self, mapper: StateMapper) -> None:
        """isOn is suppressed for motionSensor (internal)."""
        result = mapper.map_state(self.LID, "motionSensor", "isOn", False)
        assert result is None


# ── waterSensor ───────────────────────────────────────────────────────────────


class TestMapStateWaterSensor:
    LID = "967f65f3-81f2-4b1b-94c9-98fed7effe7c_1"

    @pytest.mark.unit
    def test_water_leak_detected_true(self, mapper: StateMapper) -> None:
        """waterLeakDetected=True maps to 'ON'."""
        result = mapper.map_state(self.LID, "waterSensor", "waterLeakDetected", True)
        assert result is not None
        assert result.payload == "ON"

    @pytest.mark.unit
    def test_water_leak_detected_false(self, mapper: StateMapper) -> None:
        """waterLeakDetected=False maps to 'OFF'."""
        result = mapper.map_state(self.LID, "waterSensor", "waterLeakDetected", False)
        assert result is not None
        assert result.payload == "OFF"

    @pytest.mark.unit
    def test_battery_percentage(self, mapper: StateMapper) -> None:
        """batteryPercentage maps to battery sensor."""
        result = mapper.map_state(self.LID, "waterSensor", "batteryPercentage", 70)
        assert result is not None
        assert result.unique_id.endswith("battery")
        assert result.payload == "70"

    @pytest.mark.unit
    def test_ota_status_suppressed(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "waterSensor", "otaStatus", "upToDate")
        assert result is None


# ── lightSensor ───────────────────────────────────────────────────────────────


class TestMapStateLightSensor:
    LID = "fff75d00-607c-4f23-a0e7-3dbed0e18b12_3"

    @pytest.mark.unit
    def test_illuminance(self, mapper: StateMapper) -> None:
        """illuminance maps to illuminance sensor."""
        result = mapper.map_state(self.LID, "lightSensor", "illuminance", 500)
        assert result is not None
        assert result.unique_id.endswith("illuminance")
        assert result.payload == "500"

    @pytest.mark.unit
    def test_illuminance_zero(self, mapper: StateMapper) -> None:
        """illuminance=0 maps to '0' (not suppressed)."""
        result = mapper.map_state(self.LID, "lightSensor", "illuminance", 0)
        assert result is not None
        assert result.payload == "0"

    @pytest.mark.unit
    def test_identify_started_suppressed(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "lightSensor", "identifyStarted", True)
        assert result is None


# ── environmentSensor (VINDSTYRKA) ────────────────────────────────────────────


class TestMapStateEnvironmentSensor:
    LID = "85fe4485-7c1e-4e86-9eb1-f1aa856a1e66_1"

    @pytest.mark.unit
    def test_temperature(self, mapper: StateMapper) -> None:
        """currentTemperature maps to temperature sensor."""
        result = mapper.map_state(self.LID, "environmentSensor", "currentTemperature", 20.5)
        assert result is not None
        assert result.unique_id.endswith("temperature")
        assert result.payload == "20.50"

    @pytest.mark.unit
    def test_humidity(self, mapper: StateMapper) -> None:
        """currentRH maps to humidity sensor."""
        result = mapper.map_state(self.LID, "environmentSensor", "currentRH", 50.0)
        assert result is not None
        assert result.unique_id.endswith("humidity")
        assert result.payload == "50.00"

    @pytest.mark.unit
    def test_pm25(self, mapper: StateMapper) -> None:
        """currentPM25 maps to pm25 sensor."""
        result = mapper.map_state(self.LID, "environmentSensor", "currentPM25", 3.0)
        assert result is not None
        assert result.unique_id.endswith("pm25")
        assert result.payload == "3.00"

    @pytest.mark.unit
    def test_voc_index(self, mapper: StateMapper) -> None:
        """vocIndex maps to voc sensor as integer string."""
        result = mapper.map_state(self.LID, "environmentSensor", "vocIndex", 158)
        assert result is not None
        assert result.unique_id.endswith("voc")
        assert result.payload == "158"

    @pytest.mark.unit
    def test_max_measured_pm25_suppressed(self, mapper: StateMapper) -> None:
        """maxMeasuredPM25 is suppressed."""
        result = mapper.map_state(self.LID, "environmentSensor", "maxMeasuredPM25", 999)
        assert result is None


# ── lightController ───────────────────────────────────────────────────────────


class TestMapStateLightController:
    LID = "315cebe3-06b1-4fe0-95c5-e8a8e086497c_1"

    @pytest.mark.unit
    def test_is_on_true_maps_to_short_release(self, mapper: StateMapper) -> None:
        """isOn=True maps to shortRelease event payload."""
        result = mapper.map_state(self.LID, "lightController", "isOn", True)
        assert result is not None
        data = json.loads(result.payload)
        assert data["event_type"] == "shortRelease"

    @pytest.mark.unit
    def test_is_on_false_maps_to_short_release_off(self, mapper: StateMapper) -> None:
        """isOn=False maps to shortRelease_off event payload."""
        result = mapper.map_state(self.LID, "lightController", "isOn", False)
        assert result is not None
        data = json.loads(result.payload)
        assert data["event_type"] == "shortRelease_off"

    @pytest.mark.unit
    def test_battery_percentage(self, mapper: StateMapper) -> None:
        """batteryPercentage maps to battery sensor."""
        result = mapper.map_state(self.LID, "lightController", "batteryPercentage", 90)
        assert result is not None
        assert result.unique_id.endswith("battery")
        assert result.payload == "90"

    @pytest.mark.unit
    def test_light_level_suppressed(self, mapper: StateMapper) -> None:
        """lightLevel is suppressed for lightController."""
        result = mapper.map_state(self.LID, "lightController", "lightLevel", 1)
        assert result is None


# ── button / shortcutController ───────────────────────────────────────────────


class TestMapStateButton:
    LID = "button_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["button", "shortcutController"])
    def test_is_on_true_event(self, mapper: StateMapper, device_type: str) -> None:
        """isOn=True maps to shortRelease event for button types."""
        result = mapper.map_state(self.LID, device_type, "isOn", True)
        assert result is not None
        data = json.loads(result.payload)
        assert data["event_type"] == "shortRelease"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["button", "shortcutController"])
    def test_battery(self, mapper: StateMapper, device_type: str) -> None:
        """batteryPercentage maps to battery sensor."""
        result = mapper.map_state(self.LID, device_type, "batteryPercentage", 85)
        assert result is not None
        assert result.unique_id.endswith("battery")

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["button", "shortcutController"])
    def test_identify_started_suppressed(self, mapper: StateMapper, device_type: str) -> None:
        result = mapper.map_state(self.LID, device_type, "identifyStarted", True)
        assert result is None


# ── blind ─────────────────────────────────────────────────────────────────────


class TestMapStateBlind:
    LID = "blind_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["blind", "blinds"])
    def test_position_inversion_open(self, mapper: StateMapper, device_type: str) -> None:
        """Dirigera 0 (open) → HA 100 (open)."""
        result = mapper.map_state(self.LID, device_type, "currentLevel", 0)
        assert result is not None
        assert result.payload == "100"

    @pytest.mark.unit
    @pytest.mark.parametrize("device_type", ["blind", "blinds"])
    def test_position_inversion_closed(self, mapper: StateMapper, device_type: str) -> None:
        """Dirigera 100 (closed) → HA 0 (closed)."""
        result = mapper.map_state(self.LID, device_type, "currentLevel", 100)
        assert result is not None
        assert result.payload == "0"

    @pytest.mark.unit
    def test_position_inversion_midpoint(self, mapper: StateMapper) -> None:
        """Dirigera 75 → HA 25."""
        result = mapper.map_state(self.LID, "blind", "currentLevel", 75)
        assert result is not None
        assert result.payload == "25"

    @pytest.mark.unit
    def test_blinds_current_level_alternate_attr(self, mapper: StateMapper) -> None:
        """blindsCurrentLevel attribute is also handled."""
        result = mapper.map_state(self.LID, "blind", "blindsCurrentLevel", 50)
        assert result is not None
        assert result.payload == "50"

    @pytest.mark.unit
    def test_battery_percentage(self, mapper: StateMapper) -> None:
        """batteryPercentage maps to battery sensor for blind."""
        result = mapper.map_state(self.LID, "blind", "batteryPercentage", 80)
        assert result is not None
        assert result.unique_id.endswith("battery")

    @pytest.mark.unit
    def test_invalid_current_level_is_ignored(self, mapper: StateMapper) -> None:
        """A non-numeric currentLevel is caught by the except block and
        returns None rather than raising."""
        result = mapper.map_state(self.LID, "blind", "currentLevel", "not-a-number")
        assert result is None

    @pytest.mark.unit
    def test_none_current_level_is_ignored(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "blind", "currentLevel", None)
        assert result is None

    @pytest.mark.unit
    def test_ota_status_suppressed(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "blind", "otaStatus", "upToDate")
        assert result is None


# ── airPurifier ───────────────────────────────────────────────────────────────


class TestMapStateAirPurifier:
    LID = "air_purifier_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "fan_mode,expected_pct",
        [
            ("off", "0"),
            ("low", "25"),
            ("medium", "50"),
            ("high", "75"),
        ],
    )
    def test_fan_mode_to_percentage(
        self,
        mapper: StateMapper,
        fan_mode: str,
        expected_pct: str,
    ) -> None:
        """fanMode strings map to correct percentage strings."""
        result = mapper.map_state(self.LID, "airPurifier", "fanMode", fan_mode)
        assert result is not None
        assert result.payload == expected_pct

    @pytest.mark.unit
    def test_fan_mode_auto_passthrough(self, mapper: StateMapper) -> None:
        """fanMode 'auto' is forwarded as-is (preset mode)."""
        result = mapper.map_state(self.LID, "airPurifier", "fanMode", "auto")
        assert result is not None
        assert result.payload == "auto"

    @pytest.mark.unit
    def test_fan_sensor_pm25(self, mapper: StateMapper) -> None:
        """fanSensorPM25 maps to pm25 sensor."""
        result = mapper.map_state(self.LID, "airPurifier", "fanSensorPM25", 12.0)
        assert result is not None
        assert result.unique_id.endswith("pm25")

    @pytest.mark.unit
    def test_filter_lifetime(self, mapper: StateMapper) -> None:
        """filterLifetime maps to filter sensor."""
        result = mapper.map_state(self.LID, "airPurifier", "filterLifetime", 95)
        assert result is not None
        assert result.unique_id.endswith("filter")
        assert result.payload == "95"

    @pytest.mark.unit
    def test_custom_speed_suppressed(self, mapper: StateMapper) -> None:
        """customSpeed fanMode returns None (handled via motorSpeed)."""
        result = mapper.map_state(self.LID, "airPurifier", "fanMode", "customSpeed")
        assert result is None

    @pytest.mark.unit
    def test_motor_speed_maps_to_percentage(self, mapper: StateMapper) -> None:
        """motorSpeed maps to a clamped 1-100 percentage."""
        result = mapper.map_state(self.LID, "airPurifier", "motorSpeed", 1500)
        assert result is not None
        pct = int(result.payload)
        assert 1 <= pct <= 100

    @pytest.mark.unit
    def test_motor_speed_invalid_value_ignored(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "airPurifier", "motorSpeed", "not-a-number")
        assert result is None

    @pytest.mark.unit
    def test_ota_status_suppressed(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "airPurifier", "otaStatus", "upToDate")
        assert result is None


# ── speaker ───────────────────────────────────────────────────────────────────


class TestMapStateSpeaker:
    """
    Tests state routing to speaker.py's composed entities — see
    state_mapper.py's _map_speaker_state() docstring for which parts
    are CONFIRMED against real sources vs ASSUMED/UNVERIFIED.
    """

    LID = "speaker_abc_1"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "playback",
        ["playbackPlaying", "playbackPaused", "playing", "paused", "anything"],
    )
    def test_playback_passed_through_raw(self, mapper: StateMapper, playback: str) -> None:
        """
        playback is forwarded as-is to the 'playback' sensor, with no
        translation into a fixed vocabulary — the exact real string
        values are unconfirmed, so this must work regardless of which
        vocabulary the real device actually sends.
        """
        result = mapper.map_state(self.LID, "speaker", "playback", playback)
        assert result is not None
        assert result.payload == playback
        assert result.unique_id.endswith("_playback")

    @pytest.mark.unit
    def test_volume_passed_through_raw(self, mapper: StateMapper) -> None:
        """volume 45 → '45' (no /100.0 conversion — number entity uses 0-100 directly)."""
        result = mapper.map_state(self.LID, "speaker", "volume", 45)
        assert result is not None
        assert result.payload == "45"
        assert result.unique_id.endswith("_volume")

    @pytest.mark.unit
    def test_volume_zero(self, mapper: StateMapper) -> None:
        """volume 0 → '0'."""
        result = mapper.map_state(self.LID, "speaker", "volume", 0)
        assert result is not None
        assert result.payload == "0"

    @pytest.mark.unit
    def test_volume_100(self, mapper: StateMapper) -> None:
        """volume 100 → '100'."""
        result = mapper.map_state(self.LID, "speaker", "volume", 100)
        assert result is not None
        assert result.payload == "100"

    @pytest.mark.unit
    def test_is_on_routes_to_power_switch(self, mapper: StateMapper) -> None:
        """isOn maps to ON/OFF on the 'power' switch entity."""
        r_on = mapper.map_state(self.LID, "speaker", "isOn", True)
        r_off = mapper.map_state(self.LID, "speaker", "isOn", False)
        assert r_on is not None
        assert r_off is not None
        assert r_on.payload == "ON"
        assert r_on.unique_id.endswith("_power")
        assert r_off.payload == "OFF"

    @pytest.mark.unit
    def test_is_reachable_routes_to_reachable_sensor(self, mapper: StateMapper) -> None:
        """isReachable maps to ON/OFF on the 'reachable' binary_sensor."""
        r_on = mapper.map_state(self.LID, "speaker", "isReachable", True)
        r_off = mapper.map_state(self.LID, "speaker", "isReachable", False)
        assert r_on is not None
        assert r_off is not None
        assert r_on.payload == "ON"
        assert r_on.unique_id.endswith("_reachable")
        assert r_off.payload == "OFF"

    @pytest.mark.unit
    def test_playback_audio_suppressed(self, mapper: StateMapper) -> None:
        """playbackAudio is suppressed — no persisted track metadata entity."""
        result = mapper.map_state(self.LID, "speaker", "playbackAudio", {"title": "Song"})
        assert result is None

    @pytest.mark.unit
    def test_volume_invalid_value_ignored(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "speaker", "volume", "not-a-number")
        assert result is None


# ── gateway ───────────────────────────────────────────────────────────────────


class TestMapStateGateway:
    LID = "9d3b17d8-73c0-4f33-9637-e8ee2437acd3_1"

    @pytest.mark.unit
    def test_is_reachable(self, mapper: StateMapper) -> None:
        """isReachable maps to reachable binary sensor."""
        result = mapper.map_state(self.LID, "gateway", "isReachable", True)
        assert result is not None
        assert result.unique_id.endswith("reachable")
        assert result.payload == "ON"

    @pytest.mark.unit
    def test_backend_connected(self, mapper: StateMapper) -> None:
        """backendConnected maps to backend_connected sensor."""
        result = mapper.map_state(self.LID, "gateway", "backendConnected", True)
        assert result is not None
        assert result.unique_id.endswith("backend_connected")
        assert result.payload == "ON"

    @pytest.mark.unit
    def test_ota_status(self, mapper: StateMapper) -> None:
        # noinspection GrazieInspection
        """otaStatus maps to ota_status sensor."""
        result = mapper.map_state(self.LID, "gateway", "otaStatus", "updateAvailable")
        assert result is not None
        assert result.unique_id.endswith("ota_status")
        assert result.payload == "updateAvailable"

    @pytest.mark.unit
    def test_firmware_version(self, mapper: StateMapper) -> None:
        """firmwareVersion maps to firmware_version sensor."""
        result = mapper.map_state(self.LID, "gateway", "firmwareVersion", "2.815.2")
        assert result is not None
        assert result.unique_id.endswith("firmware_version")
        assert result.payload == "2.815.2"

    @pytest.mark.unit
    def test_next_sunrise(self, mapper: StateMapper) -> None:
        """nextSunRise maps to next_sunrise sensor."""
        ts = "2026-01-31T07:17:00.000Z"
        result = mapper.map_state(self.LID, "gateway", "nextSunRise", ts)
        assert result is not None
        assert result.unique_id.endswith("next_sunrise")
        assert result.payload == ts

    @pytest.mark.unit
    def test_coordinates_json(self, mapper: StateMapper) -> None:
        """coordinates dict maps to location JSON payload."""
        coords = {
            "latitude": 51.87,
            "longitude": 6.24,
            "accuracy": -1,
        }
        result = mapper.map_state(self.LID, "gateway", "coordinates", coords)
        assert result is not None
        assert result.unique_id.endswith("location")
        data = json.loads(result.payload)
        assert data["latitude"] == 51.87
        assert data["longitude"] == 6.24
        assert data["gps_accuracy"] == 0  # -1 normalized to 0

    @pytest.mark.unit
    def test_coordinates_missing_lat_returns_none(self, mapper: StateMapper) -> None:
        """Coordinates dict with missing lat/lon returns None."""
        result = mapper.map_state(self.LID, "gateway", "coordinates", {"accuracy": -1})
        assert result is None

    @pytest.mark.unit
    def test_timezone(self, mapper: StateMapper) -> None:
        """timezone maps to timezone sensor."""
        result = mapper.map_state(self.LID, "gateway", "timezone", "Europe/Amsterdam")
        assert result is not None
        assert result.unique_id.endswith("timezone")
        assert result.payload == "Europe/Amsterdam"

    @pytest.mark.unit
    def test_ota_state(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "gateway", "otaState", "downloading")
        assert result is not None
        assert result.unique_id.endswith("ota_state")
        assert result.payload == "downloading"

    @pytest.mark.unit
    def test_homestate(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "gateway", "homestate", "away")
        assert result is not None
        assert result.unique_id.endswith("home_state")
        assert result.payload == "away"

    @pytest.mark.unit
    def test_next_sunset(self, mapper: StateMapper) -> None:
        ts = "2026-01-30T16:19:00.000Z"
        result = mapper.map_state(self.LID, "gateway", "nextSunSet", ts)
        assert result is not None
        assert result.unique_id.endswith("next_sunset")
        assert result.payload == ts

    @pytest.mark.unit
    def test_coordinates_non_dict_returns_none(self, mapper: StateMapper) -> None:
        """coordinates that isn't even a dict must be ignored — distinct
        from a dict that's merely missing lat/lon (already covered by
        test_coordinates_missing_lat_returns_none)."""
        result = mapper.map_state(self.LID, "gateway", "coordinates", "not-a-dict")
        assert result is None

    @pytest.mark.unit
    def test_is_on_suppressed(self, mapper: StateMapper) -> None:
        result = mapper.map_state(self.LID, "gateway", "isOn", True)
        assert result is None


# ── switch ────────────────────────────────────────────────────────────────────


class TestMapStateSwitch:
    LID = "switch_abc_1"

    @pytest.mark.unit
    def test_is_on_true(self, mapper: StateMapper) -> None:
        """isOn=True maps to 'ON' for switch."""
        result = mapper.map_state(self.LID, "switch", "isOn", True)
        assert result is not None
        assert result.payload == "ON"

    @pytest.mark.unit
    def test_is_on_false(self, mapper: StateMapper) -> None:
        """isOn=False maps to 'OFF' for switch."""
        result = mapper.map_state(self.LID, "switch", "isOn", False)
        assert result is not None
        assert result.payload == "OFF"

    @pytest.mark.unit
    def test_ota_status_suppressed(self, mapper: StateMapper) -> None:
        """otaStatus is suppressed for switch."""
        result = mapper.map_state(self.LID, "switch", "otaStatus", "upToDate")
        assert result is None


# ── unique_id format ──────────────────────────────────────────────────────────


class TestUniqueIdFormat:
    @pytest.mark.unit
    def test_hyphens_replaced_with_underscores(self, mapper: StateMapper) -> None:
        """Hyphens in logical_id are replaced with underscores."""
        result = mapper.map_state(
            "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1",
            "motionSensor",
            "isDetected",
            True,
        )
        assert result is not None
        assert "-" not in result.unique_id
        assert "fff75d00_607c_4f23_a0e7_3dbed0e18b12_1" in result.unique_id

    @pytest.mark.unit
    def test_unique_id_has_dirigera_prefix(self, mapper: StateMapper) -> None:
        """All unique_ids start with 'dirigera_'."""
        result = mapper.map_state("dev_1", "switch", "isOn", True)
        assert result is not None
        assert result.unique_id.startswith("dirigera_")


class TestStateMapperRemainingBranches:
    @pytest.mark.unit
    def test_unexpected_handler_error_is_contained(
        self,
        mapper: StateMapper,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """A mapper failure is logged and does not break event processing."""

        def fail(*_args: tuple[Any]) -> None:
            raise RuntimeError("bad device value")

        monkeypatch.setattr(mapper, "_map_light_state", fail)
        assert mapper.map_state("light_1", "light", "isOn", True) is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("device_type", "attribute"),
        [
            ("outlet", "unknown"),
            ("motionSensor", "unknown"),
            ("waterSensor", "unknown"),
            ("lightSensor", "unknown"),
            ("environmentSensor", "unknown"),
            ("lightController", "unknown"),
            ("button", "unknown"),
            ("blind", "unknown"),
            ("airPurifier", "unknown"),
            ("speaker", "unknown"),
            ("gateway", "unknown"),
            ("switch", "unknown"),
        ],
    )
    def test_unhandled_domain_attribute_is_ignored(
        self,
        mapper: StateMapper,
        device_type: Any,
        attribute: Any,
    ) -> None:
        assert mapper.map_state("device_1", device_type, attribute, "value") is None

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["invalid", None])
    def test_invalid_blind_position_is_ignored(
        self,
        mapper: StateMapper,
        value: str | None,
    ) -> None:
        assert mapper.map_state("blind_1", "blind", "currentLevel", value) is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value, expected", [("4.567", "4.57"), ("not-a-number", "not-a-number")]
    )
    def test_format_float_numeric_and_invalid_values(self, value: str, expected: str) -> None:
        # noinspection protected-member
        from app.mapping.state_mapper import _format_float

        assert _format_float(value) == expected
