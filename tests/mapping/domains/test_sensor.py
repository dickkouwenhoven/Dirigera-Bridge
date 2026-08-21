"""
tests/mapping/domains/test_sensor.py

Tests for app/mapping/domains/sensor.py

Covers:
    - map_light_sensor() — produces 1 illuminance entity
    - map_light_sensor() — illuminance entity config
    - map_light_sensor() — illuminance=0 is valid (not falsy-filtered)
    - map_light_sensor() — defensive battery entity
    - map_light_sensor() — unique_id uses _3 logical_id with illuminance suffix
    - map_light_sensor() — entity name appends ' Illuminance'
    - Real VALLHORN lightSensor fixture
    - DEVICE_TYPES registry maps 'lightSensor'
"""

from typing import Any

import pytest
from ha_mqtt_sdk import DeviceInfo

from app.mapping import DeviceContext
from app.mapping.domains.sensor import DEVICE_TYPES, map_light_sensor


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
    def __init__(self, attrs: Any, name: str = "Motion Sensor", lid: str = "sensor_abc_3") -> None:
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


class TestMapLightSensor:
    @pytest.mark.unit
    def test_produces_one_entity(self) -> None:
        """map_light_sensor returns exactly one entity."""
        ctx = MockContext({"illuminance": 500})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_entity_domain_is_sensor(self) -> None:
        """Entity domain is sensor."""
        ctx = MockContext({"illuminance": 500})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].domain.value == "sensor"

    @pytest.mark.unit
    def test_device_class_illuminance(self) -> None:
        """Entity has device_class=illuminance."""
        ctx = MockContext({"illuminance": 500})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["device_class"] == "illuminance"

    @pytest.mark.unit
    def test_unit_is_lux(self) -> None:
        """Entity unit is lx."""
        ctx = MockContext({"illuminance": 500})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["unit_of_measurement"] == "lx"

    @pytest.mark.unit
    def test_state_class_measurement(self) -> None:
        """Entity state_class is measurement."""
        ctx = MockContext({"illuminance": 500})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].extra["state_class"] == "measurement"

    @pytest.mark.unit
    def test_entity_name_appends_illuminance(self) -> None:
        """Entity name is device_name + ' Illuminance'."""
        ctx = MockContext({"illuminance": 500}, name="Bewegingssensor Gang")
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].name == "Bewegingssensor Gang Illuminance"

    @pytest.mark.unit
    def test_unique_id_has_illuminance_suffix(self) -> None:
        """unique_id ends with 'illuminance' suffix."""
        ctx = MockContext({"illuminance": 500}, lid="sensor_abc_3")
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result[0].unique_id.endswith("illuminance")

    @pytest.mark.unit
    def test_illuminance_zero_not_filtered(self) -> None:
        """illuminance=0 is valid — not filtered as falsy."""
        ctx = MockContext({"illuminance": 0})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_defensive_battery_when_present(self) -> None:
        """Battery entity created if batteryPercentage present."""
        ctx = MockContext({"illuminance": 0, "batteryPercentage": 85})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert len(result) == 2
        assert result[1].extra["device_class"] == "battery"

    @pytest.mark.unit
    def test_no_battery_when_absent(self) -> None:
        """No battery entity when batteryPercentage absent."""
        ctx = MockContext({"illuminance": 0})
        result = map_light_sensor(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_real_vallhorn_light_sensor(
        self,
        vallhorn_motion_raw: dict[str, Any],
        vallhorn_light_raw: dict[str, Any],
    ) -> None:
        """Real VALLHORN lightSensor maps with inherited device name."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        devices = [
            DirigeraDevice.model_validate(vallhorn_motion_raw),
            DirigeraDevice.model_validate(vallhorn_light_raw),
        ]
        regular, _ = build_device_contexts(devices)
        ctx = next(c for c in regular if c.device_type == "lightSensor")

        result = map_light_sensor(ctx, MockDeviceInfo())
        assert result is not None
        assert len(result) == 1

        name = result[0].name
        assert isinstance(name, str)
        assert "Illuminance" in name
        assert result[0].extra["device_class"] == "illuminance"


class TestSensorDeviceTypes:
    @pytest.mark.unit
    def test_light_sensor_key_registered(self) -> None:
        assert "lightSensor" in DEVICE_TYPES
        assert DEVICE_TYPES["lightSensor"] is map_light_sensor

    @pytest.mark.unit
    def test_only_one_key(self) -> None:
        assert len(DEVICE_TYPES) == 1
