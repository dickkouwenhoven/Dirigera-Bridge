"""
tests/mapping/domains/test_outlet.py

Tests for app/mapping/domains/outlet.py

Covers:
    - map_outlet() — always produces switch as first entity
    - map_outlet() — conditional energy sensors
    - map_outlet() — full INSPELNING produces 5 entities
    - map_outlet() — correct domains, device_classes, units
    - map_outlet() — energy sensor uses state_class=total_increasing
    - map_outlet() — all unique_ids distinct with correct suffixes
    - map_outlet() — entity names correct
    - DEVICE_TYPES registry
"""

import pytest

from app.mapping.domains.outlet import DEVICE_TYPES, map_outlet


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
    def __init__(self, attrs, name="Computer Stekker", lid="outlet_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


FULL_ATTRS = {
    "isOn": True,
    "currentActivePower": 9.1,
    "currentVoltage": 226.6,
    "currentAmps": 0.003,
    "totalEnergyConsumed": 2.97,
}


class TestMapOutletEntityCount:
    @pytest.mark.unit
    def test_minimal_produces_one_switch(self):
        """No energy attributes → only switch entity."""
        ctx = MockContext({"isOn": True})
        result = map_outlet(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].domain.value == "switch"

    @pytest.mark.unit
    def test_full_inspelning_produces_five(self):
        """Full INSPELNING → 5 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        assert len(result) == 5

    @pytest.mark.unit
    def test_partial_attributes_produce_partial_entities(self):
        """Only power present → switch + power sensor = 2."""
        ctx = MockContext({"isOn": True, "currentActivePower": 5.0})
        result = map_outlet(ctx, MockDeviceInfo())
        assert len(result) == 2


class TestMapOutletDomains:
    @pytest.mark.unit
    def test_first_entity_is_switch(self):
        """First entity is always the switch."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        assert result[0].domain.value == "switch"

    @pytest.mark.unit
    def test_remaining_entities_are_sensors(self):
        """All entities after the switch are sensors."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        for entity in result[1:]:
            assert entity.domain.value == "sensor"


class TestMapOutletSensorConfig:
    @pytest.mark.unit
    def test_power_sensor_config(self):
        """Power sensor has correct device_class, unit, state_class."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        power = next(e for e in result if "power" in e.unique_id)
        assert power.extra["device_class"] == "power"
        assert power.extra["unit_of_measurement"] == "W"
        assert power.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_voltage_sensor_config(self):
        """Voltage sensor has correct device_class and unit."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        voltage = next(e for e in result if "voltage" in e.unique_id)
        assert voltage.extra["device_class"] == "voltage"
        assert voltage.extra["unit_of_measurement"] == "V"

    @pytest.mark.unit
    def test_current_sensor_config(self):
        """Current sensor has correct device_class and unit."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        current = next(
            e
            for e in result
            if "current" in e.unique_id
            and "energy" not in e.unique_id
            and "power" not in e.unique_id
        )
        assert current.extra["device_class"] == "current"
        assert current.extra["unit_of_measurement"] == "A"

    @pytest.mark.unit
    def test_energy_sensor_uses_total_increasing(self):
        """Energy sensor uses state_class=total_increasing."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        energy = next(e for e in result if "energy" in e.unique_id)
        assert energy.extra["device_class"] == "energy"
        assert energy.extra["unit_of_measurement"] == "kWh"
        assert energy.extra["state_class"] == "total_increasing"

    @pytest.mark.unit
    def test_switch_payload_convention(self):
        """Switch has payload_on=ON and payload_off=OFF."""
        ctx = MockContext(FULL_ATTRS)
        result = map_outlet(ctx, MockDeviceInfo())
        switch = result[0]
        assert switch.extra["payload_on"] == "ON"
        assert switch.extra["payload_off"] == "OFF"


class TestMapOutletUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self):
        """All 5 unique_ids are distinct."""
        ctx = MockContext(FULL_ATTRS, lid="outlet_abc_1")
        result = map_outlet(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_switch_has_no_suffix(self):
        """Switch entity unique_id has no suffix."""
        ctx = MockContext(FULL_ATTRS, lid="outlet_1")
        result = map_outlet(ctx, MockDeviceInfo())
        assert result[0].unique_id == "dirigera_outlet_1"

    @pytest.mark.unit
    def test_energy_sensors_have_correct_suffixes(self):
        """Energy sensors have expected suffixes."""
        ctx = MockContext(FULL_ATTRS, lid="outlet_1")
        result = map_outlet(ctx, MockDeviceInfo())
        uids = {e.unique_id for e in result}
        assert "dirigera_outlet_1_power" in uids
        assert "dirigera_outlet_1_voltage" in uids
        assert "dirigera_outlet_1_current" in uids
        assert "dirigera_outlet_1_energy" in uids


class TestMapOutletEntityNames:
    @pytest.mark.unit
    def test_switch_name_equals_device_name(self):
        """Switch entity name equals the device name."""
        ctx = MockContext(FULL_ATTRS, name="Computer Stekker")
        result = map_outlet(ctx, MockDeviceInfo())
        assert result[0].name == "Computer Stekker"

    @pytest.mark.unit
    def test_sensor_names_contain_device_name(self):
        """All sensor names contain the device name."""
        ctx = MockContext(FULL_ATTRS, name="Computer Stekker")
        result = map_outlet(ctx, MockDeviceInfo())
        for entity in result[1:]:
            assert "Computer Stekker" in entity.name


class TestOutletDeviceTypes:
    @pytest.mark.unit
    def test_outlet_key_registered(self):
        assert "outlet" in DEVICE_TYPES
        assert DEVICE_TYPES["outlet"] is map_outlet

    @pytest.mark.unit
    def test_only_one_key(self):
        assert len(DEVICE_TYPES) == 1
