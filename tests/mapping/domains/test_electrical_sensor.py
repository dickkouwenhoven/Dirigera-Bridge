"""
tests/mapping/domains/test_electrical_sensor.py

Tests for dirigera_bridge/mapping/domains/electrical_sensor.py

Covers:
    - map_electrical_sensor() — full GRILLPLATS payload → 4 entities
    - map_electrical_sensor() — each attribute creates exactly its own entity
    - map_electrical_sensor() — partial / empty attributes
    - map_electrical_sensor() — zero values not filtered as falsy
    - Individual sensor configs: power, voltage, current, energy
    - Entity names contain the device name
    - All unique_ids distinct with correct suffixes
    - DEVICE_TYPES registry
"""

from typing import Any

import pytest
from ha_mqtt_sdk import DeviceInfo

from dirigera_bridge.mapping import DeviceContext
from dirigera_bridge.mapping.domains.electrical_sensor import (
    DEVICE_TYPES,
    map_electrical_sensor,
)


class MockDeviceInfo(DeviceInfo):
    """Minimal DeviceInfo double for domain-mapper unit tests."""

    pass


class MockAttrs(dict[str, Any]):
    def __init__(self, d: Any):
        super().__init__()
        self._d = d

    def get(self, k: Any, default: Any = None) -> Any:
        return self._d.get(k, default)


class MockContext(DeviceContext):
    def __init__(
        self,
        attrs: Any,
        name: str = "Grillplats",
        lid: str = "elec_abc_1",
    ) -> None:
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


FULL_ATTRS = {
    "currentActivePower": 9.1,
    "currentVoltage": 226.6,
    "currentAmps": 0.003,
    "totalEnergyConsumed": 2.97,
}


class TestMapElectricalSensorEntityCount:
    @pytest.mark.unit
    def test_full_payload_produces_four(self) -> None:
        """Full electricalSensor attrs → 4 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4

    @pytest.mark.unit
    def test_empty_attrs_produce_no_entities(self) -> None:
        """No measurement attrs → 0 entities."""
        ctx = MockContext({})
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        assert result == []

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("attr", "device_class"),
        [
            ("currentActivePower", "power"),
            ("currentVoltage", "voltage"),
            ("currentAmps", "current"),
            ("totalEnergyConsumed", "energy"),
        ],
    )
    def test_single_attribute_creates_only_its_entity(self, attr: str, device_class: str) -> None:
        """Each attribute on its own creates exactly one matching entity."""
        ctx = MockContext({attr: 1})
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].extra["device_class"] == device_class

    @pytest.mark.unit
    def test_zero_values_not_filtered(self) -> None:
        """Zero measurement values are valid (e.g. an idle plug)."""
        ctx = MockContext(
            {
                "currentActivePower": 0,
                "currentVoltage": 0,
                "currentAmps": 0,
                "totalEnergyConsumed": 0,
            }
        )
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4

    @pytest.mark.unit
    def test_none_values_are_filtered(self) -> None:
        """Attributes explicitly set to None do not create entities."""
        ctx = MockContext(
            {
                "currentActivePower": None,
                "currentVoltage": 226.6,
                "currentAmps": None,
                "totalEnergyConsumed": None,
            }
        )
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].extra["device_class"] == "voltage"


class TestMapElectricalSensorConfig:
    @pytest.mark.unit
    def test_power_config(self) -> None:
        ctx = MockContext({"currentActivePower": 9.1})
        power = map_electrical_sensor(ctx, MockDeviceInfo())[0]
        assert power.extra["device_class"] == "power"
        assert power.extra["unit_of_measurement"] == "W"
        assert power.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_voltage_config(self) -> None:
        ctx = MockContext({"currentVoltage": 226.6})
        voltage = map_electrical_sensor(ctx, MockDeviceInfo())[0]
        assert voltage.extra["device_class"] == "voltage"
        assert voltage.extra["unit_of_measurement"] == "V"
        assert voltage.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_current_config(self) -> None:
        ctx = MockContext({"currentAmps": 0.003})
        current = map_electrical_sensor(ctx, MockDeviceInfo())[0]
        assert current.extra["device_class"] == "current"
        assert current.extra["unit_of_measurement"] == "A"
        assert current.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_energy_config(self) -> None:
        """Energy is a cumulative counter → total_increasing."""
        ctx = MockContext({"totalEnergyConsumed": 2.97})
        energy = map_electrical_sensor(ctx, MockDeviceInfo())[0]
        assert energy.extra["device_class"] == "energy"
        assert energy.extra["unit_of_measurement"] == "kWh"
        assert energy.extra["state_class"] == "total_increasing"

    @pytest.mark.unit
    def test_all_entity_names_contain_device_name(self) -> None:
        ctx = MockContext(FULL_ATTRS, name="Grillplats Keuken")
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert isinstance(entity.name, str)
            assert "Grillplats Keuken" in entity.name

    @pytest.mark.unit
    def test_entity_name_suffixes(self) -> None:
        ctx = MockContext(FULL_ATTRS, name="Plug")
        names = {str(e.name) for e in map_electrical_sensor(ctx, MockDeviceInfo())}
        assert names == {"Plug Power", "Plug Voltage", "Plug Current", "Plug Energy"}


class TestMapElectricalSensorUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self) -> None:
        ctx = MockContext(FULL_ATTRS, lid="elec_1")
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_correct_suffixes(self) -> None:
        ctx = MockContext(FULL_ATTRS, lid="elec_1")
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        uids = {str(e.unique_id) for e in result}
        for suffix in ("power", "voltage", "current", "energy"):
            assert any(u.endswith(suffix) for u in uids), suffix

    @pytest.mark.unit
    def test_unique_ids_contain_logical_id(self) -> None:
        ctx = MockContext(FULL_ATTRS, lid="elec_xyz_1")
        result = map_electrical_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert "elec_xyz_1" in str(entity.unique_id)


class TestElectricalSensorDeviceTypes:
    @pytest.mark.unit
    def test_key_registered(self) -> None:
        assert "electricalSensor" in DEVICE_TYPES

    @pytest.mark.unit
    def test_only_one_key(self) -> None:
        assert len(DEVICE_TYPES) == 1

    @pytest.mark.unit
    def test_registered_mapper_is_map_electrical_sensor(self) -> None:
        assert DEVICE_TYPES["electricalSensor"] is map_electrical_sensor
