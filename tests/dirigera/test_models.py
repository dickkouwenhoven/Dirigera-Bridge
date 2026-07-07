"""
tests/dirigera/test_models.py

Tests for app/dirigera/models.py

Covers:
    - DirigeraDevice parsing from real payloads (light, motionSensor,
      lightSensor, environmentSensor, outlet, gateway, remote)
    - DirigeraDevice — grouping detection (is_grouped, physical_id)
    - DirigeraDevice — attribute access (custom_name, model, battery etc.)
    - DirigeraDevice — capabilities (can_receive)
    - DirigeraDevice — raw_attributes passthrough
    - DirigeraDevice — room access
    - DirigeraDevice — is_reachable
    - DirigeraDevice — extra fields allowed (extra='allow')
    - DirigeraWebSocketEvent — state change event parsing
    - DirigeraWebSocketEvent — device added/removed event parsing
    - DirigeraWebSocketEvent — is_state_change / is_device_added / is_device_removed
    - DirigeraWebSocketEvent — unknown type is tolerated
    - DirigeraWebSocketEvent — data.changed_attributes
    - DirigeraWebSocketEvent — data.physical_id from relationId
"""

import pytest

from app.dirigera.models import DirigeraDevice, DirigeraWebSocketEvent


# ── DirigeraDevice — light ────────────────────────────────────────────────────


class TestDirigeraDeviceLight:
    @pytest.mark.unit
    def test_parse_light_payload(self, light_raw):
        """A real TRADFRI light payload parses without error."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device is not None

    @pytest.mark.unit
    def test_light_id(self, light_raw):
        """light.id matches the payload id field."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.id == "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

    @pytest.mark.unit
    def test_light_device_type(self, light_raw):
        """light.device_type is 'light'."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.device_type == "light"

    @pytest.mark.unit
    def test_light_not_grouped(self, light_raw):
        """Light has no relationId — is_grouped is False."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.is_grouped is False

    @pytest.mark.unit
    def test_light_physical_id_equals_id(self, light_raw):
        """Single device — physical_id equals id."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.physical_id == device.id

    @pytest.mark.unit
    def test_light_custom_name(self, light_raw):
        """custom_name returns the configured name."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.attributes.custom_name == "Raamverlichting"

    @pytest.mark.unit
    def test_light_model(self, light_raw):
        """model string is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        assert "TRADFRI" in device.attributes.model

    @pytest.mark.unit
    def test_light_manufacturer(self, light_raw):
        """manufacturer is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.attributes.manufacturer == "IKEA of Sweden"

    @pytest.mark.unit
    def test_light_firmware_version(self, light_raw):
        """firmware_version is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.attributes.firmware_version == "1.0.44"

    @pytest.mark.unit
    def test_light_serial_number(self, light_raw):
        """serial_number is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.attributes.serial_number == "94A081FFFE049D9C"

    @pytest.mark.unit
    def test_light_is_reachable_false(self, light_raw):
        """isReachable=False is parsed correctly."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.is_reachable is False

    @pytest.mark.unit
    def test_light_capabilities(self, light_raw):
        """canReceive list is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        caps = device.capabilities.can_receive
        assert "isOn" in caps
        assert "lightLevel" in caps
        assert "colorTemperature" in caps
        assert "colorHue" in caps
        assert "colorSaturation" in caps

    @pytest.mark.unit
    def test_light_room(self, light_raw):
        """Room name is accessible."""
        device = DirigeraDevice.model_validate(light_raw)
        assert device.room is not None
        assert device.room.name == "Woonkamer"

    @pytest.mark.unit
    def test_light_raw_attributes_contains_isOn(self, light_raw):
        """raw_attributes dict contains isOn."""
        device = DirigeraDevice.model_validate(light_raw)
        assert "isOn" in device.raw_attributes
        assert device.raw_attributes["isOn"] is True

    @pytest.mark.unit
    def test_light_raw_attributes_contains_color_fields(self, light_raw):
        """raw_attributes dict contains colour fields."""
        device = DirigeraDevice.model_validate(light_raw)
        ra = device.raw_attributes
        assert "colorHue" in ra
        assert "colorSaturation" in ra
        assert "colorTemperature" in ra


# ── DirigeraDevice — VALLHORN motionSensor (_1 sibling) ──────────────────────


class TestDirigeraDeviceMotionSensor:
    @pytest.mark.unit
    def test_parse_motion_sensor(self, vallhorn_motion_raw):
        """VALLHORN motionSensor payload parses correctly."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.device_type == "motionSensor"

    @pytest.mark.unit
    def test_motion_sensor_is_grouped(self, vallhorn_motion_raw):
        """motionSensor has relationId — is_grouped is True."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.is_grouped is True

    @pytest.mark.unit
    def test_motion_sensor_physical_id_equals_relation_id(self, vallhorn_motion_raw):
        """physical_id equals the relationId field."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.physical_id == device.relation_id
        assert device.physical_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    def test_motion_sensor_battery(self, vallhorn_motion_raw):
        """batteryPercentage is accessible in raw_attributes."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.raw_attributes.get("batteryPercentage") == 70

    @pytest.mark.unit
    def test_motion_sensor_is_reachable(self, vallhorn_motion_raw):
        """isReachable=True is parsed correctly."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.is_reachable is True

    @pytest.mark.unit
    def test_motion_sensor_room(self, vallhorn_motion_raw):
        """Room is accessible on motion sensor."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.room is not None
        assert device.room.name == "Gang"

    @pytest.mark.unit
    def test_motion_sensor_custom_name(self, vallhorn_motion_raw):
        """customName is accessible."""
        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        assert device.attributes.custom_name == "Bewegingssensor Gang"


# ── DirigeraDevice — VALLHORN lightSensor (_3 sibling) ───────────────────────


class TestDirigeraDeviceLightSensor:
    @pytest.mark.unit
    def test_parse_light_sensor(self, vallhorn_light_raw):
        """VALLHORN lightSensor (_3) payload parses correctly."""
        device = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert device.device_type == "lightSensor"

    @pytest.mark.unit
    def test_light_sensor_is_grouped(self, vallhorn_light_raw):
        """lightSensor has relationId — is_grouped is True."""
        device = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert device.is_grouped is True

    @pytest.mark.unit
    def test_light_sensor_shares_relation_with_motion(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """Both VALLHORN siblings share the same physical_id."""
        motion = DirigeraDevice.model_validate(vallhorn_motion_raw)
        light = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert motion.physical_id == light.physical_id

    @pytest.mark.unit
    def test_light_sensor_empty_custom_name(self, vallhorn_light_raw):
        """lightSensor _3 sibling has empty customName."""
        device = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert device.attributes.custom_name == ""

    @pytest.mark.unit
    def test_light_sensor_illuminance_in_raw(self, vallhorn_light_raw):
        """illuminance is present in raw_attributes."""
        device = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert "illuminance" in device.raw_attributes
        assert device.raw_attributes["illuminance"] == 0

    @pytest.mark.unit
    def test_light_sensor_no_room(self, vallhorn_light_raw):
        """lightSensor _3 sibling has no room assigned."""
        device = DirigeraDevice.model_validate(vallhorn_light_raw)
        assert device.room is None


# ── DirigeraDevice — VINDSTYRKA environmentSensor ────────────────────────────


class TestDirigeraDeviceEnvironmentSensor:
    @pytest.mark.unit
    def test_parse_environment_sensor(self, vindstyrka_raw):
        """VINDSTYRKA payload parses correctly."""
        device = DirigeraDevice.model_validate(vindstyrka_raw)
        assert device.device_type == "environmentSensor"

    @pytest.mark.unit
    def test_environment_sensor_not_grouped(self, vindstyrka_raw):
        """VINDSTYRKA has no relationId — is_grouped is False."""
        device = DirigeraDevice.model_validate(vindstyrka_raw)
        assert device.is_grouped is False

    @pytest.mark.unit
    def test_environment_sensor_attributes(self, vindstyrka_raw):
        """All four measurement attributes are in raw_attributes."""
        device = DirigeraDevice.model_validate(vindstyrka_raw)
        ra = device.raw_attributes
        assert "currentTemperature" in ra
        assert "currentRH" in ra
        assert "currentPM25" in ra
        assert "vocIndex" in ra

    @pytest.mark.unit
    def test_environment_sensor_values(self, vindstyrka_raw):
        """Measurement values match the fixture data."""
        device = DirigeraDevice.model_validate(vindstyrka_raw)
        ra = device.raw_attributes
        assert ra["currentTemperature"] == 20
        assert ra["currentRH"] == 50
        assert ra["currentPM25"] == 3
        assert ra["vocIndex"] == 158

    @pytest.mark.unit
    def test_environment_sensor_product_code(self, vindstyrka_raw):
        """productCode is accessible."""
        device = DirigeraDevice.model_validate(vindstyrka_raw)
        assert device.attributes.product_code == "E2112"


# ── DirigeraDevice — INSPELNING outlet ───────────────────────────────────────


class TestDirigeraDeviceOutlet:
    @pytest.mark.unit
    def test_parse_outlet(self, outlet_raw):
        """INSPELNING outlet payload parses correctly."""
        device = DirigeraDevice.model_validate(outlet_raw)
        assert device.device_type == "outlet"

    @pytest.mark.unit
    def test_outlet_energy_attributes(self, outlet_raw):
        """All energy monitoring attributes are in raw_attributes."""
        device = DirigeraDevice.model_validate(outlet_raw)
        ra = device.raw_attributes
        assert "currentActivePower" in ra
        assert "currentVoltage" in ra
        assert "currentAmps" in ra
        assert "totalEnergyConsumed" in ra

    @pytest.mark.unit
    def test_outlet_is_on(self, outlet_raw):
        """isOn is accessible in raw_attributes."""
        device = DirigeraDevice.model_validate(outlet_raw)
        assert device.raw_attributes["isOn"] is True

    @pytest.mark.unit
    def test_outlet_custom_name(self, outlet_raw):
        """customName is accessible."""
        device = DirigeraDevice.model_validate(outlet_raw)
        assert device.attributes.custom_name == "Computer Stekker"


# ── DirigeraDevice — gateway ──────────────────────────────────────────────────


class TestDirigeraDeviceGateway:
    @pytest.mark.unit
    def test_parse_gateway(self, gateway_raw):
        """Gateway payload parses correctly."""
        device = DirigeraDevice.model_validate(gateway_raw)
        assert device.device_type == "gateway"

    @pytest.mark.unit
    def test_gateway_is_grouped(self, gateway_raw):
        """Gateway has relationId — is_grouped is True."""
        device = DirigeraDevice.model_validate(gateway_raw)
        assert device.is_grouped is True

    @pytest.mark.unit
    def test_gateway_firmware_version(self, gateway_raw):
        """Gateway firmware version is accessible."""
        device = DirigeraDevice.model_validate(gateway_raw)
        assert device.attributes.firmware_version == "2.815.2"

    @pytest.mark.unit
    def test_gateway_coordinates_in_raw(self, gateway_raw):
        """Coordinates dict is present in raw_attributes."""
        device = DirigeraDevice.model_validate(gateway_raw)
        assert "coordinates" in device.raw_attributes
        coords = device.raw_attributes["coordinates"]
        assert "latitude" in coords
        assert "longitude" in coords

    @pytest.mark.unit
    def test_gateway_no_room(self, gateway_raw):
        """Gateway has no room assigned."""
        device = DirigeraDevice.model_validate(gateway_raw)
        assert device.room is None


# ── DirigeraDevice — remote (lightController) ─────────────────────────────────


class TestDirigeraDeviceRemote:
    @pytest.mark.unit
    def test_parse_remote(self, remote_raw):
        """Remote Control N2 payload parses correctly."""
        device = DirigeraDevice.model_validate(remote_raw)
        assert device.device_type == "lightController"

    @pytest.mark.unit
    def test_remote_battery(self, remote_raw):
        """batteryPercentage is accessible."""
        device = DirigeraDevice.model_validate(remote_raw)
        assert device.raw_attributes.get("batteryPercentage") == 90

    @pytest.mark.unit
    def test_remote_can_send(self, remote_raw):
        """canSend is accessible on remote capabilities."""
        device = DirigeraDevice.model_validate(remote_raw)
        assert "isOn" in device.capabilities.can_send
        assert "lightLevel" in device.capabilities.can_send


# ── DirigeraDevice — extra fields allowed ────────────────────────────────────


class TestDirigeraDeviceExtraFields:
    @pytest.mark.unit
    def test_extra_top_level_fields_tolerated(self, light_raw):
        """Extra top-level fields in the payload do not raise."""
        payload = dict(light_raw)
        payload["unknownField"] = "some_value"
        payload["anotherExtra"] = 42

        device = DirigeraDevice.model_validate(payload)
        assert device is not None
        assert device.device_type == "light"

    @pytest.mark.unit
    def test_extra_attribute_fields_in_raw_attributes(self, light_raw):
        """Extra attribute fields are captured in raw_attributes."""
        payload = dict(light_raw)
        payload["attributes"] = dict(light_raw["attributes"])
        payload["attributes"]["futureAttribute"] = "new_value"

        device = DirigeraDevice.model_validate(payload)
        assert device.raw_attributes.get("futureAttribute") == "new_value"


# ── DirigeraWebSocketEvent — state change ────────────────────────────────────


class TestDirigeraWebSocketEventStateChange:
    STATE_CHANGE = {
        "type": "deviceStateChanged",
        "data": {
            "id": "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1",
            "relationId": "fff75d00-607c-4f23-a0e7-3dbed0e18b12",
            "type": "sensor",
            "deviceType": "motionSensor",
            "attributes": {
                "isDetected": True,
                "batteryPercentage": 70,
            },
        },
    }

    @pytest.mark.unit
    def test_parse_state_change(self):
        """deviceStateChanged event parses correctly."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event is not None

    @pytest.mark.unit
    def test_is_state_change_true(self):
        """is_state_change is True for deviceStateChanged."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.is_state_change is True

    @pytest.mark.unit
    def test_is_device_added_false(self):
        """is_device_added is False for deviceStateChanged."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.is_device_added is False

    @pytest.mark.unit
    def test_is_device_removed_false(self):
        """is_device_removed is False for deviceStateChanged."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.is_device_removed is False

    @pytest.mark.unit
    def test_data_id(self):
        """data.id is the logical device id."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.data.id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1"

    @pytest.mark.unit
    def test_data_device_type(self):
        """data.device_type is accessible."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.data.device_type == "motionSensor"

    @pytest.mark.unit
    def test_data_physical_id_from_relation_id(self):
        """data.physical_id comes from relationId field."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.data.physical_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    def test_changed_attributes(self):
        """changed_attributes returns the attributes dict."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        changed = event.data.changed_attributes
        assert "isDetected" in changed
        assert changed["isDetected"] is True

    @pytest.mark.unit
    def test_type_field_accessible(self):
        """event.type is the raw type string."""
        event = DirigeraWebSocketEvent.model_validate(self.STATE_CHANGE)
        assert event.type == "deviceStateChanged"


# ── DirigeraWebSocketEvent — device added ─────────────────────────────────────


class TestDirigeraWebSocketEventDeviceAdded:
    DEVICE_ADDED = {
        "type": "deviceAdded",
        "data": {
            "id": "new_device_abc_1",
            "type": "light",
            "deviceType": "light",
            "attributes": {"isOn": False},
        },
    }

    @pytest.mark.unit
    def test_is_device_added_true(self):
        """is_device_added is True for deviceAdded event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_ADDED)
        assert event.is_device_added is True

    @pytest.mark.unit
    def test_is_state_change_false(self):
        """is_state_change is False for deviceAdded event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_ADDED)
        assert event.is_state_change is False

    @pytest.mark.unit
    def test_data_id_accessible(self):
        """data.id is accessible on deviceAdded event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_ADDED)
        assert event.data.id == "new_device_abc_1"


# ── DirigeraWebSocketEvent — device removed ───────────────────────────────────


class TestDirigeraWebSocketEventDeviceRemoved:
    DEVICE_REMOVED = {
        "type": "deviceRemoved",
        "data": {
            "id": "old_device_abc_1",
            "type": "light",
            "deviceType": "light",
            "attributes": {},
        },
    }

    @pytest.mark.unit
    def test_is_device_removed_true(self):
        """is_device_removed is True for deviceRemoved event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_REMOVED)
        assert event.is_device_removed is True

    @pytest.mark.unit
    def test_is_state_change_false(self):
        """is_state_change is False for deviceRemoved event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_REMOVED)
        assert event.is_state_change is False

    @pytest.mark.unit
    def test_data_id_accessible(self):
        """data.id is accessible on deviceRemoved event."""
        event = DirigeraWebSocketEvent.model_validate(self.DEVICE_REMOVED)
        assert event.data.id == "old_device_abc_1"


# ── DirigeraWebSocketEvent — unknown type ─────────────────────────────────────


class TestDirigeraWebSocketEventUnknownType:
    @pytest.mark.unit
    def test_unknown_type_does_not_raise(self):
        """Unknown event type is tolerated (extra='allow' pattern)."""
        payload = {"type": "someUnknownEventType"}
        event = DirigeraWebSocketEvent.model_validate(payload)
        assert event.type == "someUnknownEventType"
        assert event.is_state_change is False
        assert event.is_device_added is False
        assert event.is_device_removed is False

    @pytest.mark.unit
    def test_event_with_no_data_block(self):
        """Event with no data block is tolerated."""
        payload = {"type": "someEvent"}
        event = DirigeraWebSocketEvent.model_validate(payload)
        assert event.data is None
