"""
tests/mapping/domains/test_button.py

Tests for app/mapping/domains/button.py

Covers:
    - map_button() — 1 or 2 entities depending on battery
    - Primary entity is EVENT domain
    - event_types list present with standard button actions
    - No _off variants (buttons have no directional press)
    - Battery entity conditional
    - Both 'button' and 'shortcutController' keys registered
    - Both keys point to the same mapper function
"""

import pytest

from app.mapping.domains.button import DEVICE_TYPES, map_button


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
    def __init__(self, attrs=None, name="Shortcut Button", lid="button_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs or {})


class TestMapButton:
    @pytest.mark.unit
    def test_without_battery_produces_one_entity(self):
        """No battery → 1 event entity."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_with_battery_produces_two_entities(self):
        """With battery → 2 entities."""
        ctx = MockContext({"batteryPercentage": 85})
        result = map_button(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_primary_entity_is_event_domain(self):
        """Primary entity domain is 'event'."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert result[0].domain.value == "event"

    @pytest.mark.unit
    def test_event_types_present(self):
        """Entity has event_types list."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert "event_types" in result[0].extra
        assert len(result[0].extra["event_types"]) > 0

    @pytest.mark.unit
    def test_event_types_contain_short_release(self):
        """event_types includes shortRelease."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert "shortRelease" in result[0].extra["event_types"]

    @pytest.mark.unit
    def test_event_types_contain_long_release(self):
        """event_types includes longRelease."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert "longRelease" in result[0].extra["event_types"]

    @pytest.mark.unit
    def test_event_types_contain_double_press(self):
        """event_types includes doublePress."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        assert "doublePress" in result[0].extra["event_types"]

    @pytest.mark.unit
    def test_no_off_variants_in_event_types(self):
        """Button has no _off event variants (unlike lightController)."""
        ctx = MockContext()
        result = map_button(ctx, MockDeviceInfo())
        event_types = result[0].extra["event_types"]
        assert not any("_off" in et for et in event_types)

    @pytest.mark.unit
    def test_battery_entity_config(self):
        """Battery entity has correct config."""
        ctx = MockContext({"batteryPercentage": 85}, lid="button_1")
        result = map_button(ctx, MockDeviceInfo())
        battery = result[1]
        assert battery.domain.value == "sensor"
        assert battery.extra["device_class"] == "battery"
        assert battery.extra["unit_of_measurement"] == "%"
        assert battery.unique_id.endswith("battery")

    @pytest.mark.unit
    def test_unique_ids_distinct(self):
        """Event and battery unique_ids are distinct."""
        ctx = MockContext({"batteryPercentage": 85})
        result = map_button(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_entity_name_equals_device_name(self):
        """Primary entity name equals device name."""
        ctx = MockContext(name="Shortcut Woonkamer")
        result = map_button(ctx, MockDeviceInfo())
        assert result[0].name == "Shortcut Woonkamer"

    @pytest.mark.unit
    def test_fewer_event_types_than_remote(self):
        """Button has fewer event types than lightController (no _off variants)."""
        from app.mapping.domains.remote import map_light_controller

        btn_ctx = MockContext()
        remote_ctx = MockContext()

        btn_result = map_button(btn_ctx, MockDeviceInfo())
        remote_result = map_light_controller(remote_ctx, MockDeviceInfo())

        btn_types = btn_result[0].extra["event_types"]
        remote_types = remote_result[0].extra["event_types"]

        assert len(btn_types) < len(remote_types)


class TestButtonDeviceTypes:
    @pytest.mark.unit
    def test_both_keys_registered(self):
        """DEVICE_TYPES has 'button' and 'shortcutController'."""
        assert "button" in DEVICE_TYPES
        assert "shortcutController" in DEVICE_TYPES

    @pytest.mark.unit
    def test_both_keys_same_function(self):
        """Both keys point to the same mapper function."""
        assert DEVICE_TYPES["button"] is DEVICE_TYPES["shortcutController"]

    @pytest.mark.unit
    def test_only_two_keys(self):
        """Only two keys registered in this module."""
        assert len(DEVICE_TYPES) == 2
