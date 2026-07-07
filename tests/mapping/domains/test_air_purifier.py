"""
tests/mapping/domains/test_air_purifier.py

Tests for app/mapping/domains/air_purifier.py

Covers:
    - map_air_purifier() — 1, 2, or 3 entities depending on attributes
    - Primary entity is FAN domain
    - Fan entity config (speed range, preset_modes, optimistic, payloads)
    - PM2.5 sensor uses fanSensorPM25 (not currentPM25)
    - Filter sensor has entity_category=diagnostic and mdi:air-filter icon
    - No battery entity (mains powered)
    - All unique_ids distinct with correct suffixes
    - DEVICE_TYPES registry
"""

import pytest

from app.mapping.domains.air_purifier import DEVICE_TYPES, map_air_purifier


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
    def __init__(self, attrs=None, name="STARKVIND", lid="ap_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs or {})


FULL_ATTRS = {
    "fanMode": "auto",
    "fanSensorPM25": 12,
    "filterLifetime": 95,
    "motorSpeed": 1500,
}


class TestMapAirPurifierEntityCount:
    @pytest.mark.unit
    def test_minimal_produces_one_fan(self):
        """No optional attrs → 1 fan entity."""
        ctx = MockContext({"fanMode": "low"})
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].domain.value == "fan"

    @pytest.mark.unit
    def test_with_pm25_produces_two(self):
        """With fanSensorPM25 → 2 entities."""
        ctx = MockContext({"fanMode": "low", "fanSensorPM25": 5})
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_full_starkvind_produces_three(self):
        """Full STARKVIND attrs → 3 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert len(result) == 3

    @pytest.mark.unit
    def test_no_battery_entity(self):
        """No battery entity (mains powered)."""
        ctx = MockContext(FULL_ATTRS)
        result = map_air_purifier(ctx, MockDeviceInfo())
        for entity in result:
            assert entity.extra.get("device_class") != "battery"


class TestMapAirPurifierFanConfig:
    @pytest.mark.unit
    def test_fan_domain(self):
        """Primary entity domain is 'fan'."""
        ctx = MockContext()
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert result[0].domain.value == "fan"

    @pytest.mark.unit
    def test_fan_speed_range(self):
        """Fan has speed_range_min=1 and speed_range_max=100."""
        ctx = MockContext()
        result = map_air_purifier(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["speed_range_min"] == 1
        assert extra["speed_range_max"] == 100

    @pytest.mark.unit
    def test_fan_preset_modes_include_auto(self):
        """Fan preset_modes includes 'auto'."""
        ctx = MockContext()
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert "auto" in result[0].extra["preset_modes"]

    @pytest.mark.unit
    def test_fan_not_optimistic(self):
        """Fan entity has optimistic=False."""
        ctx = MockContext()
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert result[0].extra["optimistic"] is False

    @pytest.mark.unit
    def test_fan_payload_on_off(self):
        """Fan has payload_on=ON and payload_off=OFF."""
        ctx = MockContext()
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert result[0].extra["payload_on"] == "ON"
        assert result[0].extra["payload_off"] == "OFF"

    @pytest.mark.unit
    def test_fan_name_equals_device_name(self):
        """Fan entity name equals device name."""
        ctx = MockContext(name="STARKVIND Woonkamer")
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert result[0].name == "STARKVIND Woonkamer"


class TestMapAirPurifierSensorConfig:
    @pytest.mark.unit
    def test_pm25_sensor_config(self):
        """PM2.5 sensor has correct device_class and unit."""
        ctx = MockContext({"fanSensorPM25": 12})
        result = map_air_purifier(ctx, MockDeviceInfo())
        pm25 = next(e for e in result if "pm25" in e.unique_id)
        assert pm25.extra["device_class"] == "pm25"
        assert pm25.extra["unit_of_measurement"] == "µg/m³"
        assert pm25.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_pm25_uses_fan_sensor_attribute(self):
        """PM2.5 uses fanSensorPM25, not currentPM25."""
        ctx = MockContext({"currentPM25": 5})  # VINDSTYRKA attribute — wrong
        result = map_air_purifier(ctx, MockDeviceInfo())
        # currentPM25 should NOT trigger pm25 entity
        assert len(result) == 1  # only fan

    @pytest.mark.unit
    def test_filter_sensor_config(self):
        """Filter sensor has diagnostic entity_category and icon."""
        ctx = MockContext({"filterLifetime": 95})
        result = map_air_purifier(ctx, MockDeviceInfo())
        flt = next(e for e in result if "filter" in e.unique_id)
        assert flt.extra["entity_category"] == "diagnostic"
        assert flt.extra["icon"] == "mdi:air-filter"
        assert flt.extra["unit_of_measurement"] == "%"

    @pytest.mark.unit
    def test_filter_name_contains_device_name(self):
        """Filter Life entity name contains device name."""
        ctx = MockContext({"filterLifetime": 95}, name="STARKVIND")
        result = map_air_purifier(ctx, MockDeviceInfo())
        flt = next(e for e in result if "filter" in e.unique_id)
        assert "STARKVIND" in flt.name


class TestMapAirPurifierUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self):
        """All 3 unique_ids are distinct."""
        ctx = MockContext(FULL_ATTRS, lid="ap_1")
        result = map_air_purifier(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_fan_has_no_suffix(self):
        """Fan entity unique_id has no suffix."""
        ctx = MockContext(lid="ap_1")
        result = map_air_purifier(ctx, MockDeviceInfo())
        assert result[0].unique_id == "dirigera_ap_1"

    @pytest.mark.unit
    def test_sensor_suffixes(self):
        """PM2.5 and filter sensors have correct suffixes."""
        ctx = MockContext(FULL_ATTRS, lid="ap_1")
        result = map_air_purifier(ctx, MockDeviceInfo())
        uids = {e.unique_id for e in result}
        assert "dirigera_ap_1_pm25" in uids
        assert "dirigera_ap_1_filter" in uids


class TestAirPurifierDeviceTypes:
    @pytest.mark.unit
    def test_key_registered(self):
        """DEVICE_TYPES maps 'airPurifier' to map_air_purifier."""
        assert "airPurifier" in DEVICE_TYPES

    @pytest.mark.unit
    def test_only_one_key(self):
        """Only one key registered."""
        assert len(DEVICE_TYPES) == 1
