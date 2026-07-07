"""
tests/mapping/domains/test_blind.py

Tests for app/mapping/domains/blind.py

Covers:
    - map_blind() — always produces cover entity
    - map_blind() — battery conditional
    - Cover entity config (device_class, payloads, position, optimistic)
    - Position inversion documented (not implemented here)
    - Battery entity config
    - Both 'blind' and 'blinds' keys registered
    - DEVICE_TYPES both keys point to same function
"""

import pytest

from app.mapping.domains.blind import DEVICE_TYPES, map_blind


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
    def __init__(self, attrs, name="Gordijn", lid="blind_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


class TestMapBlind:
    @pytest.mark.unit
    def test_always_produces_cover_entity(self):
        """map_blind always produces at least one cover entity."""
        ctx = MockContext({"currentLevel": 50})
        result = map_blind(ctx, MockDeviceInfo())
        assert len(result) >= 1
        assert result[0].domain.value == "cover"

    @pytest.mark.unit
    def test_without_battery_produces_one_entity(self):
        """No battery → 1 cover entity."""
        ctx = MockContext({"currentLevel": 50})
        result = map_blind(ctx, MockDeviceInfo())
        assert len(result) == 1

    @pytest.mark.unit
    def test_with_battery_produces_two_entities(self):
        """With battery → 2 entities."""
        ctx = MockContext({"currentLevel": 0, "batteryPercentage": 80})
        result = map_blind(ctx, MockDeviceInfo())
        assert len(result) == 2

    @pytest.mark.unit
    def test_cover_device_class_blind(self):
        """Cover entity has device_class=blind."""
        ctx = MockContext({})
        result = map_blind(ctx, MockDeviceInfo())
        assert result[0].extra["device_class"] == "blind"

    @pytest.mark.unit
    def test_cover_payload_commands(self):
        """Cover entity has OPEN/CLOSE/STOP payloads."""
        ctx = MockContext({})
        result = map_blind(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["payload_open"] == "OPEN"
        assert extra["payload_close"] == "CLOSE"
        assert extra["payload_stop"] == "STOP"

    @pytest.mark.unit
    def test_cover_position_range(self):
        """Cover entity has position_open=100 and position_closed=0."""
        ctx = MockContext({})
        result = map_blind(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["position_open"] == 100
        assert extra["position_closed"] == 0

    @pytest.mark.unit
    def test_cover_not_optimistic(self):
        """Cover entity has optimistic=False."""
        ctx = MockContext({})
        result = map_blind(ctx, MockDeviceInfo())
        assert result[0].extra["optimistic"] is False

    @pytest.mark.unit
    def test_cover_name_equals_device_name(self):
        """Cover entity name equals device name."""
        ctx = MockContext({}, name="Slaapkamergordijn")
        result = map_blind(ctx, MockDeviceInfo())
        assert result[0].name == "Slaapkamergordijn"

    @pytest.mark.unit
    def test_battery_entity_config(self):
        """Battery entity has correct config."""
        ctx = MockContext({"batteryPercentage": 80}, lid="blind_1")
        result = map_blind(ctx, MockDeviceInfo())
        battery = result[1]
        assert battery.extra["device_class"] == "battery"
        assert battery.extra["unit_of_measurement"] == "%"
        assert battery.unique_id.endswith("battery")

    @pytest.mark.unit
    def test_unique_ids_distinct(self):
        """Cover and battery unique_ids are distinct."""
        ctx = MockContext({"batteryPercentage": 80})
        result = map_blind(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))


class TestBlindDeviceTypes:
    @pytest.mark.unit
    def test_both_keys_registered(self):
        """Both 'blind' and 'blinds' are registered."""
        assert "blind" in DEVICE_TYPES
        assert "blinds" in DEVICE_TYPES

    @pytest.mark.unit
    def test_both_keys_same_function(self):
        """Both keys point to the same mapper function."""
        assert DEVICE_TYPES["blind"] is DEVICE_TYPES["blinds"]
