"""
tests/mapping/domains/test_remote.py

Tests for app/mapping/domains/remote.py

Covers:
    - map_light_controller() — produces 1 or 2 entities
    - Primary entity is EVENT domain
    - event_types list present and non-empty
    - event_types contains standard IKEA N2 action strings
    - Battery entity conditional on batteryPercentage
    - No _off variants absent from event_types sanity check
    - isOn and lightLevel NOT exposed as entities
    - DEVICE_TYPES uses 'lightController' key (not 'remote')
    - Real Remote Control N2 fixture
"""

import pytest

from app.mapping.domains.remote import DEVICE_TYPES, map_light_controller


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
    def __init__(self, attrs, name="Remote", lid="remote_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


class TestMapLightController:
    @pytest.mark.unit
    def test_without_battery_produces_one_entity(self):
        """No battery → 1 event entity."""
        ctx = MockContext({"isOn": False, "lightLevel": 1})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_with_battery_produces_two_entities(self):
        """With battery → 2 entities."""
        ctx = MockContext({"isOn": False, "batteryPercentage": 90})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_primary_entity_is_event_domain(self):
        """Primary entity domain is 'event'."""
        ctx = MockContext({})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert result[0].domain.value == "event"

    @pytest.mark.unit
    def test_event_types_present(self):
        """Entity has event_types list."""
        ctx = MockContext({})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert "event_types" in result[0].extra
        assert len(result[0].extra["event_types"]) > 0

    @pytest.mark.unit
    def test_event_types_contain_short_release(self):
        """event_types includes shortRelease."""
        ctx = MockContext({})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert "shortRelease" in result[0].extra["event_types"]

    @pytest.mark.unit
    def test_event_types_contain_long_press(self):
        """event_types includes longPress."""
        ctx = MockContext({})
        result = map_light_controller(ctx, MockDeviceInfo())
        assert "longPress" in result[0].extra["event_types"]

    @pytest.mark.unit
    def test_event_types_contain_off_variants(self):
        """event_types includes _off variants for N2 remote."""
        ctx = MockContext({})
        result = map_light_controller(ctx, MockDeviceInfo())
        event_types = result[0].extra["event_types"]
        assert any("_off" in et for et in event_types)

    @pytest.mark.unit
    def test_battery_entity_config(self):
        """Battery entity has correct config."""
        ctx = MockContext({"batteryPercentage": 90}, lid="remote_1")
        result = map_light_controller(ctx, MockDeviceInfo())
        battery = result[1]
        assert battery.domain.value == "sensor"
        assert battery.extra["device_class"] == "battery"
        assert battery.extra["unit_of_measurement"] == "%"
        assert battery.unique_id.endswith("battery")

    @pytest.mark.unit
    def test_internal_fields_not_exposed(self):
        """isOn and lightLevel are not separate entities."""
        ctx = MockContext({"isOn": False, "lightLevel": 1})
        result = map_light_controller(ctx, MockDeviceInfo())
        names = [e.name for e in result]
        assert not any("isOn" in n or "lightLevel" in n for n in names)

    @pytest.mark.unit
    def test_unique_ids_distinct(self):
        """Event and battery unique_ids are distinct."""
        ctx = MockContext({"batteryPercentage": 90})
        result = map_light_controller(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_real_remote_n2_fixture(self, remote_raw):
        """Real Remote Control N2 fixture maps correctly."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(remote_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_light_controller(ctx, MockDeviceInfo())
        assert len(result) == 2
        assert result[0].domain.value == "event"
        assert result[1].extra["device_class"] == "battery"


class TestRemoteDeviceTypes:
    @pytest.mark.unit
    def test_light_controller_key_registered(self):
        """DEVICE_TYPES key is 'lightController' not 'remote'."""
        assert "lightController" in DEVICE_TYPES
        assert "remote" not in DEVICE_TYPES

    @pytest.mark.unit
    def test_only_one_key(self):
        assert len(DEVICE_TYPES) == 1
