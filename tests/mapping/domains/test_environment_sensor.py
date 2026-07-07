"""
tests/mapping/domains/test_environment_sensor.py

Tests for app/mapping/domains/environment_sensor.py

Covers:
    - map_environment_sensor() — full VINDSTYRKA → 4 entities
    - map_environment_sensor() — partial attributes → fewer entities
    - map_environment_sensor() — zero values not filtered as falsy
    - map_environment_sensor() — no battery entity (mains powered)
    - Individual sensor configs: temperature, humidity, PM2.5, VOC
    - VOC entity has no unit_of_measurement
    - All unique_ids distinct with correct suffixes
    - Real VINDSTYRKA fixture
    - DEVICE_TYPES registry
"""

import pytest

from app.mapping.domains.environment_sensor import (
    DEVICE_TYPES,
    map_environment_sensor,
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
    def __init__(self, attrs, name="Hygrometer", lid="env_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


FULL_ATTRS = {
    "currentTemperature": 20,
    "currentRH": 50,
    "currentPM25": 3,
    "vocIndex": 158,
}


class TestMapEnvironmentSensorEntityCount:
    @pytest.mark.unit
    def test_full_vindstyrka_produces_four(self):
        """Full VINDSTYRKA attrs → 4 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4

    @pytest.mark.unit
    def test_partial_attrs_produce_fewer_entities(self):
        """Only temperature + humidity → 2 entities."""
        ctx = MockContext({"currentTemperature": 20, "currentRH": 50})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_empty_attrs_produce_no_entities(self):
        """No measurement attrs → 0 entities."""
        ctx = MockContext({})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 0

    @pytest.mark.unit
    def test_zero_values_not_filtered(self):
        """Zero measurement values are valid."""
        ctx = MockContext({"currentTemperature": 0, "currentPM25": 0})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_no_battery_entity(self):
        """No battery entity (VINDSTYRKA is mains powered)."""
        ctx = MockContext(FULL_ATTRS)
        result = map_environment_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert entity.extra.get("device_class") != "battery"


class TestMapEnvironmentSensorConfig:
    @pytest.mark.unit
    def test_temperature_config(self):
        """Temperature entity has correct config."""
        ctx = MockContext({"currentTemperature": 20})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        temp = result[0]
        assert temp.extra["device_class"] == "temperature"
        assert temp.extra["unit_of_measurement"] == "°C"
        assert temp.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_humidity_config(self):
        """Humidity entity has correct config."""
        ctx = MockContext({"currentRH": 50})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        hum = result[0]
        assert hum.extra["device_class"] == "humidity"
        assert hum.extra["unit_of_measurement"] == "%"

    @pytest.mark.unit
    def test_pm25_config(self):
        """PM2.5 entity has correct config."""
        ctx = MockContext({"currentPM25": 3})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        pm = result[0]
        assert pm.extra["device_class"] == "pm25"
        assert pm.extra["unit_of_measurement"] == "µg/m³"

    @pytest.mark.unit
    def test_voc_config(self):
        """VOC entity has correct device_class and no unit."""
        ctx = MockContext({"vocIndex": 158})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        voc = result[0]
        assert voc.extra["device_class"] == "volatile_organic_compounds_parts"
        assert "unit_of_measurement" not in voc.extra

    @pytest.mark.unit
    def test_all_entity_names_contain_device_name(self):
        """All entity names contain device name."""
        ctx = MockContext(FULL_ATTRS, name="Hygrometer Woonkamer")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert "Hygrometer Woonkamer" in entity.name


class TestMapEnvironmentSensorUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self):
        """All 4 unique_ids are distinct."""
        ctx = MockContext(FULL_ATTRS, lid="env_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_correct_suffixes(self):
        """All four suffixes are present."""
        ctx = MockContext(FULL_ATTRS, lid="env_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        uids = {e.unique_id for e in result}
        assert any("temperature" in u for u in uids)
        assert any("humidity" in u for u in uids)
        assert any("pm25" in u for u in uids)
        assert any("voc" in u for u in uids)

    @pytest.mark.unit
    def test_real_vindstyrka_fixture(self, vindstyrka_raw):
        """Real VINDSTYRKA fixture maps to 4 entities."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(vindstyrka_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4


class TestEnvironmentSensorDeviceTypes:
    @pytest.mark.unit
    def test_key_registered(self):
        assert "environmentSensor" in DEVICE_TYPES

    @pytest.mark.unit
    def test_only_one_key(self):
        assert len(DEVICE_TYPES) == 1
