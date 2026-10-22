"""
tests/mapping/domains/test_environment_sensor.py

Tests for app/mapping/domains/environment_sensor.py

Covers:
    - map_environment_sensor() — full VINDSTYRKA → 4 entities
    - map_environment_sensor() — partial attributes → fewer entities
    - map_environment_sensor() — zero values not filtered as falsy
    - map_environment_sensor() — no battery entity when no batteryPercentage
    - map_environment_sensor() — TIMMERFLOTTE: battery on "_1", none on "_2"
    - owns_battery() / battery_owner_id() helpers
    - Individual sensor configs: temperature, humidity, PM2.5, VOC
    - VOC entity has no unit_of_measurement
    - All unique_ids distinct with correct suffixes
    - Real VINDSTYRKA fixture
    - DEVICE_TYPES registry
"""

from typing import Any

import pytest
from ha_mqtt_sdk import DeviceInfo

from dirigera_bridge.mapping import DeviceContext
from dirigera_bridge.mapping.domains.environment_sensor import (
    DEVICE_TYPES,
    battery_owner_id,
    map_environment_sensor,
    owns_battery,
)


class MockDeviceInfo(DeviceInfo):
    """
    Minimal DeviceInfo double for domain-mapper unit tests.

    ha_mqtt_sdk.DeviceInfo is a TypedDict (a plain dict at runtime)
    since v0.4+, so this must subclass dict to satisfy the
    isinstance(device_info, dict) check in make_battery_entity().
    Subclassing dict means every existing MockDeviceInfo() call site
    across the test suite keeps working unchanged.
    """

    pass


class MockAttrs(dict[str, Any]):
    def __init__(self, d: Any):
        super().__init__()
        self._d = d

    def get(self, k: Any, default: Any = None) -> Any:
        return self._d.get(k, default)


class MockContext(DeviceContext):
    def __init__(self, attrs: Any, name: str = "Hygrometer", lid: str = "env_abc_1") -> None:
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
    def test_full_vindstyrka_produces_four(self) -> None:
        """Full VINDSTYRKA attrs → 4 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4

    @pytest.mark.unit
    def test_partial_attrs_produce_fewer_entities(self) -> None:
        """Only temperature + humidity → 2 entities."""
        ctx = MockContext({"currentTemperature": 20, "currentRH": 50})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_empty_attrs_produce_no_entities(self) -> None:
        """No measurement attrs → 0 entities."""
        ctx = MockContext({})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 0

    @pytest.mark.unit
    def test_zero_values_not_filtered(self) -> None:
        """Zero measurement values are valid."""
        ctx = MockContext({"currentTemperature": 0, "currentPM25": 0})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_no_battery_entity(self) -> None:
        """No battery entity (VINDSTYRKA is mains powered)."""
        ctx = MockContext(FULL_ATTRS)
        result = map_environment_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert entity.extra.get("device_class") != "battery"


class TestMapEnvironmentSensorConfig:
    @pytest.mark.unit
    def test_temperature_config(self) -> None:
        """Temperature entity has correct config."""
        ctx = MockContext({"currentTemperature": 20})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        temp = result[0]
        assert temp.extra["device_class"] == "temperature"
        assert temp.extra["unit_of_measurement"] == "°C"
        assert temp.extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_humidity_config(self) -> None:
        """Humidity entity has correct config."""
        ctx = MockContext({"currentRH": 50})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        hum = result[0]
        assert hum.extra["device_class"] == "humidity"
        assert hum.extra["unit_of_measurement"] == "%"

    @pytest.mark.unit
    def test_pm25_config(self) -> None:
        """PM2.5 entity has correct config."""
        ctx = MockContext({"currentPM25": 3})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        pm = result[0]
        assert pm.extra["device_class"] == "pm25"
        assert pm.extra["unit_of_measurement"] == "µg/m³"

    @pytest.mark.unit
    def test_voc_config(self) -> None:
        """VOC entity has correct device_class and no unit."""
        ctx = MockContext({"vocIndex": 158})
        result = map_environment_sensor(ctx, MockDeviceInfo())
        voc = result[0]
        assert voc.extra["device_class"] == "volatile_organic_compounds_parts"
        assert "unit_of_measurement" not in voc.extra

    @pytest.mark.unit
    def test_all_entity_names_contain_device_name(self) -> None:
        """All entity names contain device name."""
        ctx = MockContext(FULL_ATTRS, name="Hygrometer Woonkamer")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        for entity in result:
            assert isinstance(entity.name, str)
            assert "Hygrometer Woonkamer" in entity.name


class TestMapEnvironmentSensorUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self) -> None:
        """All 4 unique_ids are distinct."""
        ctx = MockContext(FULL_ATTRS, lid="env_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_correct_suffixes(self) -> None:
        """All four suffixes are present."""
        ctx = MockContext(FULL_ATTRS, lid="env_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        uids = {e.unique_id for e in result}
        assert any("temperature" in u for u in uids)
        assert any("humidity" in u for u in uids)
        assert any("pm25" in u for u in uids)
        assert any("voc" in u for u in uids)

    @pytest.mark.unit
    def test_real_vindstyrka_fixture(self, vindstyrka_raw: dict[str, Any]) -> None:
        """Real VINDSTYRKA fixture maps to 4 entities."""
        from dirigera_bridge.dirigera.models import DirigeraDevice
        from dirigera_bridge.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(vindstyrka_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 4


RELATION_ID = "fd5aef28_c0a1_45aa_8228_db7f62827f72"

TIMMERFLOTTE_PRIMARY_ATTRS = {"currentTemperature": 18.8, "batteryPercentage": 87}
TIMMERFLOTTE_SECONDARY_ATTRS = {"currentRH": 51, "batteryPercentage": 87}


class TestMapEnvironmentSensorBattery:
    @pytest.mark.unit
    def test_primary_device_creates_temperature_and_battery(self) -> None:
        """TIMMERFLOTTE '_1' (temperature) → temperature + battery."""
        ctx = MockContext(TIMMERFLOTTE_PRIMARY_ATTRS, lid=f"{RELATION_ID}_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2
        classes = {e.extra["device_class"] for e in result}
        assert classes == {"temperature", "battery"}

    @pytest.mark.unit
    def test_secondary_device_creates_no_battery(self) -> None:
        """TIMMERFLOTTE '_2' (humidity) → humidity only, battery is owned by '_1'."""
        ctx = MockContext(TIMMERFLOTTE_SECONDARY_ATTRS, lid=f"{RELATION_ID}_2")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].extra["device_class"] == "humidity"

    @pytest.mark.unit
    def test_one_battery_per_physical_device(self) -> None:
        """Mapping both halves of one TIMMERFLOTTE yields exactly one battery."""
        entities = [
            *map_environment_sensor(
                MockContext(TIMMERFLOTTE_PRIMARY_ATTRS, lid=f"{RELATION_ID}_1"),
                MockDeviceInfo(),
            ),
            *map_environment_sensor(
                MockContext(TIMMERFLOTTE_SECONDARY_ATTRS, lid=f"{RELATION_ID}_2"),
                MockDeviceInfo(),
            ),
        ]
        batteries = [e for e in entities if e.extra["device_class"] == "battery"]
        assert len(batteries) == 1
        assert str(batteries[0].unique_id).endswith("_1_battery")
        uids = [e.unique_id for e in entities]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_device_without_suffix_keeps_its_battery(self) -> None:
        """A device id without '_2' suffix still owns its battery."""
        ctx = MockContext({**FULL_ATTRS, "batteryPercentage": 55}, lid="env_abc")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 5
        assert any(e.extra["device_class"] == "battery" for e in result)

    @pytest.mark.unit
    def test_battery_entity_name_and_unique_id(self) -> None:
        """Battery entity carries device name and a 'battery' unique_id suffix."""
        ctx = MockContext(
            TIMMERFLOTTE_PRIMARY_ATTRS,
            name="Hygrometer Gang",
            lid=f"{RELATION_ID}_1",
        )
        battery = next(
            e
            for e in map_environment_sensor(ctx, MockDeviceInfo())
            if e.extra["device_class"] == "battery"
        )
        assert isinstance(battery.name, str)
        assert "Hygrometer Gang" in battery.name
        assert "battery" in str(battery.unique_id)

    @pytest.mark.unit
    @pytest.mark.parametrize("bad_value", [None, "87", "n/a"])
    def test_non_numeric_battery_is_ignored(self, bad_value: Any) -> None:
        """Missing or non-numeric batteryPercentage → no battery entity."""
        ctx = MockContext(
            {"currentTemperature": 20, "batteryPercentage": bad_value},
            lid=f"{RELATION_ID}_1",
        )
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].extra["device_class"] == "temperature"

    @pytest.mark.unit
    def test_float_battery_value_accepted(self) -> None:
        """Float batteryPercentage is accepted (cast to int)."""
        ctx = MockContext({"batteryPercentage": 87.0}, lid=f"{RELATION_ID}_1")
        result = map_environment_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1
        assert result[0].extra["device_class"] == "battery"


class TestBatteryOwnership:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("logical_id", "expected"),
        [
            (f"{RELATION_ID}_1", True),
            (f"{RELATION_ID}_2", False),
            ("env_abc", True),
        ],
    )
    def test_owns_battery(self, logical_id: str, expected: bool) -> None:
        assert owns_battery(logical_id) is expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("logical_id", "expected"),
        [
            (f"{RELATION_ID}_2", f"{RELATION_ID}_1"),
            (f"{RELATION_ID}_1", f"{RELATION_ID}_1"),
            ("env_abc", "env_abc"),
        ],
    )
    def test_battery_owner_id(self, logical_id: str, expected: str) -> None:
        assert battery_owner_id(logical_id) == expected
        

class TestEnvironmentSensorDeviceTypes:
    @pytest.mark.unit
    def test_key_registered(self) -> None:
        assert "environmentSensor" in DEVICE_TYPES

    @pytest.mark.unit
    def test_only_one_key(self) -> None:
        assert len(DEVICE_TYPES) == 1
