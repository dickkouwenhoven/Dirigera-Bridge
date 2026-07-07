"""
tests/mapping/domains/test_binary_sensor.py

Tests for app/mapping/domains/binary_sensor.py

Covers:
    - map_motion_sensor() — 1 or 2 entities depending on battery
    - map_water_sensor()  — 1 or 2 entities depending on battery
    - Correct HA device_class for each sensor type
    - payload_on / payload_off convention
    - Battery entity config (device_class, unit, suffix)
    - Battery conditional on attribute presence
    - All unique_ids distinct
    - DEVICE_TYPES registry has both motionSensor and waterSensor
"""

import pytest

from app.mapping.domains.binary_sensor import (
    DEVICE_TYPES,
    map_motion_sensor,
    map_water_sensor,
)


class MockDeviceInfo(dict):
    """
    Minimal DeviceInfo double for domain-mapper unit tests.

    ha_mqtt_sdk.DeviceInfo is a TypedDict (a plain dict at runtime)
    since v0.4+, so this must subclass dict to satisfy the
    isinstance(device_info, dict) check in make_battery_entity().
    Subclassing dict means every existing MockDeviceInfo() call site
    across the test suite keeps working unchanged.
    """

    pass


class MockAttrs:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class MockContext:
    def __init__(self, attrs, name="Test Sensor", lid="sensor_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


class TestMapMotionSensor:
    @pytest.mark.unit
    def test_without_battery_produces_one_entity(self):
        """motionSensor without battery → 1 binary_sensor."""
        ctx = MockContext({"isDetected": False})
        result = map_motion_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].domain.value == "binary_sensor"

    @pytest.mark.unit
    def test_with_battery_produces_two_entities(self):
        """motionSensor with battery → 2 entities."""
        ctx = MockContext({"isDetected": False, "batteryPercentage": 70})
        result = map_motion_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_motion_device_class(self):
        """Primary entity has device_class=motion."""
        ctx = MockContext({"isDetected": False})
        result = map_motion_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["device_class"] == "motion"

    @pytest.mark.unit
    def test_payload_convention(self):
        """Binary sensor has payload_on=ON and payload_off=OFF."""
        ctx = MockContext({"isDetected": False})
        result = map_motion_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["payload_on"] == "ON"
        assert result[0].extra["payload_off"] == "OFF"

    @pytest.mark.unit
    def test_battery_entity_config(self):
        """Battery entity has correct device_class and unit."""
        ctx = MockContext(
            {"isDetected": False, "batteryPercentage": 70},
            lid="fff75d00_1",
        )
        result = map_motion_sensor(ctx, MockDeviceInfo())
        battery = result[1]
        assert battery.domain.value == "sensor"
        assert battery.extra["device_class"] == "battery"
        assert battery.extra["unit_of_measurement"] == "%"
        assert battery.unique_id.endswith("battery")

    @pytest.mark.unit
    def test_unique_ids_distinct(self):
        """motion and battery unique_ids are distinct."""
        ctx = MockContext({"isDetected": False, "batteryPercentage": 70})
        result = map_motion_sensor(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_entity_name_contains_device_name(self):
        """Entity names contain device name."""
        ctx = MockContext(
            {"isDetected": False, "batteryPercentage": 70},
            name="Bewegingssensor Gang",
        )
        result = map_motion_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert "Bewegingssensor Gang" in entity.name

    @pytest.mark.unit
    def test_real_vallhorn_fixture(self, vallhorn_motion_raw):
        """Real VALLHORN motionSensor fixture maps correctly."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(vallhorn_motion_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_motion_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2
        assert result[0].extra["device_class"] == "motion"
        assert result[1].extra["device_class"] == "battery"


class TestMapWaterSensor:
    @pytest.mark.unit
    def test_without_battery_produces_one_entity(self):
        """waterSensor without battery → 1 binary_sensor."""
        ctx = MockContext({"waterLeakDetected": False})
        result = map_water_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_with_battery_produces_two_entities(self):
        """waterSensor with battery → 2 entities."""
        ctx = MockContext({"waterLeakDetected": False, "batteryPercentage": 70})
        result = map_water_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_moisture_device_class(self):
        """Primary entity has device_class=moisture."""
        ctx = MockContext({"waterLeakDetected": False})
        result = map_water_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["device_class"] == "moisture"

    @pytest.mark.unit
    def test_payload_convention(self):
        """Binary sensor has payload_on=ON and payload_off=OFF."""
        ctx = MockContext({"waterLeakDetected": False})
        result = map_water_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["payload_on"] == "ON"
        assert result[0].extra["payload_off"] == "OFF"

    @pytest.mark.unit
    def test_unique_ids_distinct(self):
        """moisture and battery unique_ids are distinct."""
        ctx = MockContext({"waterLeakDetected": False, "batteryPercentage": 70})
        result = map_water_sensor(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_real_badring_fixture(self, water_sensor_raw):
        """Real BADRING waterSensor fixture maps correctly."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(water_sensor_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_water_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2
        assert result[0].extra["device_class"] == "moisture"


class TestBinarySensorDeviceTypes:
    @pytest.mark.unit
    def test_both_keys_registered(self):
        """DEVICE_TYPES has motionSensor and waterSensor."""
        assert "motionSensor" in DEVICE_TYPES
        assert "waterSensor" in DEVICE_TYPES

    @pytest.mark.unit
    def test_different_mappers(self):
        """motionSensor and waterSensor point to different functions."""
        assert DEVICE_TYPES["motionSensor"] is not DEVICE_TYPES["waterSensor"]
