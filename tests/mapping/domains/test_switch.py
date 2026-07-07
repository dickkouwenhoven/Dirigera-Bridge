"""
tests/mapping/domains/test_switch.py

Tests for app/mapping/domains/switch.py

Covers:
    - map_switch() — produces exactly one switch entity
    - Entity domain is HADomain.SWITCH
    - Entity name equals device_name
    - unique_id correct (no suffix)
    - payload_on / payload_off convention
    - No energy monitoring fields (distinct from outlet)
    - No battery entity
    - DEVICE_TYPES registry
"""

import pytest

from app.mapping.domains.switch import DEVICE_TYPES, map_switch


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
    def __init__(self, attrs=None, name="Schakelaar", lid="switch_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs or {})


class TestMapSwitch:
    @pytest.mark.unit
    def test_returns_single_element_list(self):
        """map_switch always returns a list with exactly one entity."""
        ctx = MockContext()
        result = map_switch(ctx, MockDeviceInfo())
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.unit
    def test_entity_domain_is_switch(self):
        """Entity domain is HADomain.SWITCH."""
        ctx = MockContext()
        result = map_switch(ctx, MockDeviceInfo())
        assert result[0].domain.value == "switch"

    @pytest.mark.unit
    def test_entity_name_equals_device_name(self):
        """Entity name equals device_name."""
        ctx = MockContext(name="Schakelaar Keuken")
        result = map_switch(ctx, MockDeviceInfo())
        assert result[0].name == "Schakelaar Keuken"

    @pytest.mark.unit
    def test_unique_id_no_suffix(self):
        """Primary entity unique_id has no suffix."""
        ctx = MockContext(lid="switch_1")
        result = map_switch(ctx, MockDeviceInfo())
        assert result[0].unique_id == "dirigera_switch_1"

    @pytest.mark.unit
    def test_unique_id_hyphens_replaced(self):
        """Hyphens in logical_id replaced with underscores."""
        ctx = MockContext(lid="switch-abc-1")
        result = map_switch(ctx, MockDeviceInfo())
        assert result[0].unique_id == "dirigera_switch_abc_1"

    @pytest.mark.unit
    def test_payload_on_off(self):
        """Entity has payload_on=ON and payload_off=OFF."""
        ctx = MockContext()
        result = map_switch(ctx, MockDeviceInfo())
        assert result[0].extra["payload_on"] == "ON"
        assert result[0].extra["payload_off"] == "OFF"

    @pytest.mark.unit
    def test_no_energy_monitoring_fields(self):
        """No energy monitoring fields (distinct from outlet)."""
        ctx = MockContext()
        result = map_switch(ctx, MockDeviceInfo())
        extra = result[0].extra
        for field in ["device_class", "unit_of_measurement", "state_class"]:
            assert field not in extra, f"switch should not have {field}"

    @pytest.mark.unit
    def test_no_battery_entity(self):
        """switch produces exactly 1 entity — no battery."""
        ctx = MockContext({"isOn": False, "batteryPercentage": 90})
        result = map_switch(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_switch_distinct_from_outlet(self):
        """Switch produces 1 entity; outlet produces 5."""
        from app.mapping.domains.outlet import map_outlet

        FULL_OUTLET_ATTRS = {
            "isOn": True,
            "currentActivePower": 9.1,
            "currentVoltage": 226.6,
            "currentAmps": 0.003,
            "totalEnergyConsumed": 2.97,
        }

        switch_ctx = MockContext({"isOn": False})
        outlet_ctx = MockContext(FULL_OUTLET_ATTRS)

        switch_result = map_switch(switch_ctx, MockDeviceInfo())
        outlet_result = map_outlet(outlet_ctx, MockDeviceInfo())

        assert len(switch_result) == 1
        assert len(outlet_result) == 5


class TestSwitchDeviceTypes:
    @pytest.mark.unit
    def test_switch_key_registered(self):
        """DEVICE_TYPES maps 'switch' to map_switch."""
        assert "switch" in DEVICE_TYPES
        assert DEVICE_TYPES["switch"] is map_switch

    @pytest.mark.unit
    def test_only_one_key(self):
        """Only 'switch' registered in this module."""
        assert len(DEVICE_TYPES) == 1
