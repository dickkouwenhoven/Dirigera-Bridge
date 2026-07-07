"""
tests/core/test_state_cache.py

Tests for app/core/state_cache.py

Covers:
    - set() — first write returns True, unchanged returns False
    - set() — None is a valid cached value (distinct from not-set)
    - set() — different values return True
    - set_device_state() — bulk write, returns changed flags
    - get() — cached value, default for missing attribute/device
    - get_device_state() — all attributes for a device, returns copy
    - has_changed() — not cached = changed, same = not changed
    - has_changed() — does not write to cache
    - get_all_logical_ids() — returns copy of key set
    - device_count() and attribute_count()
    - clear_device() — removes one device, safe for unknown device
    - clear() — wipes entire cache
    - snapshot() — deep copy, mutations do not affect cache
    - Validation — empty logical_id, empty attribute
"""

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode


# ── set() ─────────────────────────────────────────────────────────────────────


class TestSet:
    @pytest.mark.unit
    def test_first_write_returns_true(self, state_cache):
        """First write for a new attribute returns True (changed)."""
        assert state_cache.set("device_1", "isOn", True) is True

    @pytest.mark.unit
    def test_same_value_returns_false(self, state_cache):
        """Writing the same value again returns False (unchanged)."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.set("device_1", "isOn", True) is False

    @pytest.mark.unit
    def test_different_value_returns_true(self, state_cache):
        """Writing a different value returns True (changed)."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.set("device_1", "isOn", False) is True

    @pytest.mark.unit
    def test_none_is_valid_value(self, state_cache):
        """None is a valid cached value — distinct from not-set."""
        state_cache.set("device_1", "colorHue", None)
        assert state_cache.get("device_1", "colorHue", "sentinel") is None

    @pytest.mark.unit
    def test_none_same_as_none_returns_false(self, state_cache):
        """Writing None when None is cached returns False."""
        state_cache.set("device_1", "colorHue", None)
        assert state_cache.set("device_1", "colorHue", None) is False

    @pytest.mark.unit
    def test_zero_is_valid_value(self, state_cache):
        """Zero is a valid cached value — not treated as falsy."""
        state_cache.set("device_1", "lightLevel", 0)
        assert state_cache.get("device_1", "lightLevel", 99) == 0

    @pytest.mark.unit
    def test_zero_same_as_zero_returns_false(self, state_cache):
        """Writing 0 when 0 is cached returns False."""
        state_cache.set("device_1", "lightLevel", 0)
        assert state_cache.set("device_1", "lightLevel", 0) is False

    @pytest.mark.unit
    def test_different_devices_independent(self, state_cache):
        """Same attribute on different devices are independent."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_2", "isOn", False)

        assert state_cache.get("device_1", "isOn") is True
        assert state_cache.get("device_2", "isOn") is False

    @pytest.mark.unit
    def test_empty_logical_id_raises(self, state_cache):
        """Empty logical_id raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            state_cache.set("", "isOn", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_attribute_raises(self, state_cache):
        """Empty attribute raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            state_cache.set("device_1", "", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_whitespace_logical_id_raises(self, state_cache):
        """Whitespace-only logical_id raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            state_cache.set("   ", "isOn", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── set_device_state() ────────────────────────────────────────────────────────


class TestSetDeviceState:
    @pytest.mark.unit
    def test_bulk_write_all_new(self, state_cache):
        """set_device_state returns True for all new attributes."""
        result = state_cache.set_device_state(
            "device_1",
            {"isOn": True, "lightLevel": 80, "colorTemp": 2700},
        )
        assert result == {
            "isOn": True,
            "lightLevel": True,
            "colorTemp": True,
        }

    @pytest.mark.unit
    def test_bulk_write_partial_change(self, state_cache):
        """set_device_state returns False for unchanged attributes."""
        state_cache.set_device_state(
            "device_1",
            {"isOn": True, "lightLevel": 80},
        )
        result = state_cache.set_device_state(
            "device_1",
            {"isOn": True, "lightLevel": 90},  # lightLevel changed
        )
        assert result["isOn"] is False
        assert result["lightLevel"] is True

    @pytest.mark.unit
    def test_bulk_write_stores_values(self, state_cache):
        """set_device_state persists all values correctly."""
        state_cache.set_device_state(
            "device_1",
            {"isOn": False, "lightLevel": 50},
        )
        assert state_cache.get("device_1", "isOn") is False
        assert state_cache.get("device_1", "lightLevel") == 50

    @pytest.mark.unit
    def test_bulk_write_empty_dict(self, state_cache):
        """set_device_state with empty dict returns empty result."""
        result = state_cache.set_device_state("device_1", {})
        assert result == {}

    @pytest.mark.unit
    def test_bulk_write_invalid_attributes_type_raises(self, state_cache):
        """set_device_state raises if attributes is not a dict."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            state_cache.set_device_state("device_1", "not_a_dict")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── get() ─────────────────────────────────────────────────────────────────────


class TestGet:
    @pytest.mark.unit
    def test_get_cached_value(self, state_cache):
        """get() returns the cached value."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.get("device_1", "isOn") is True

    @pytest.mark.unit
    def test_get_returns_none_for_missing_attribute(self, state_cache):
        """get() returns None for an uncached attribute."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.get("device_1", "lightLevel") is None

    @pytest.mark.unit
    def test_get_returns_default_for_missing_attribute(self, state_cache):
        """get() returns the provided default for uncached attributes."""
        assert state_cache.get("device_1", "lightLevel", 50) == 50

    @pytest.mark.unit
    def test_get_returns_none_for_unknown_device(self, state_cache):
        """get() returns None for an unknown device."""
        assert state_cache.get("unknown_device", "isOn") is None

    @pytest.mark.unit
    def test_get_returns_default_for_unknown_device(self, state_cache):
        """get() returns the provided default for unknown devices."""
        assert state_cache.get("unknown", "isOn", "fallback") == "fallback"

    @pytest.mark.unit
    def test_get_empty_logical_id_raises(self, state_cache):
        """get() raises for empty logical_id."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            state_cache.get("", "isOn")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── get_device_state() ────────────────────────────────────────────────────────


class TestGetDeviceState:
    @pytest.mark.unit
    def test_returns_all_attributes(self, state_cache):
        """get_device_state returns all cached attributes for a device."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_1", "lightLevel", 80)
        state_cache.set("device_1", "colorTemp", 2700)

        result = state_cache.get_device_state("device_1")
        assert result == {"isOn": True, "lightLevel": 80, "colorTemp": 2700}

    @pytest.mark.unit
    def test_returns_empty_dict_for_unknown_device(self, state_cache):
        """get_device_state returns {} for an unknown device."""
        assert state_cache.get_device_state("unknown") == {}

    @pytest.mark.unit
    def test_returns_copy(self, state_cache):
        """get_device_state returns a copy — mutating it does not affect cache."""
        state_cache.set("device_1", "isOn", True)
        result = state_cache.get_device_state("device_1")
        result["isOn"] = False

        assert state_cache.get("device_1", "isOn") is True


# ── has_changed() ─────────────────────────────────────────────────────────────


class TestHasChanged:
    @pytest.mark.unit
    def test_not_cached_returns_true(self, state_cache):
        """has_changed returns True when attribute is not cached."""
        assert state_cache.has_changed("device_1", "isOn", True) is True

    @pytest.mark.unit
    def test_same_value_returns_false(self, state_cache):
        """has_changed returns False when value matches cache."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.has_changed("device_1", "isOn", True) is False

    @pytest.mark.unit
    def test_different_value_returns_true(self, state_cache):
        """has_changed returns True when value differs from cache."""
        state_cache.set("device_1", "isOn", True)
        assert state_cache.has_changed("device_1", "isOn", False) is True

    @pytest.mark.unit
    def test_does_not_write_to_cache(self, state_cache):
        """has_changed never writes to the cache."""
        state_cache.has_changed("device_1", "isOn", True)
        assert state_cache.get("device_1", "isOn") is None

    @pytest.mark.unit
    def test_none_matches_cached_none(self, state_cache):
        """has_changed returns False when both cached and new are None."""
        state_cache.set("device_1", "colorHue", None)
        assert state_cache.has_changed("device_1", "colorHue", None) is False

    @pytest.mark.unit
    def test_zero_matches_cached_zero(self, state_cache):
        """has_changed handles zero correctly (not falsy)."""
        state_cache.set("device_1", "lightLevel", 0)
        assert state_cache.has_changed("device_1", "lightLevel", 0) is False
        assert state_cache.has_changed("device_1", "lightLevel", 1) is True


# ── get_all_logical_ids() ─────────────────────────────────────────────────────


class TestGetAllLogicalIds:
    @pytest.mark.unit
    def test_empty_cache_returns_empty_set(self, state_cache):
        """Empty cache returns empty set."""
        assert state_cache.get_all_logical_ids() == set()

    @pytest.mark.unit
    def test_returns_all_device_ids(self, state_cache):
        """Returns the set of all logical_ids in the cache."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_2", "isOn", False)
        state_cache.set("device_3", "illuminance", 100)

        ids = state_cache.get_all_logical_ids()
        assert ids == {"device_1", "device_2", "device_3"}

    @pytest.mark.unit
    def test_returns_copy(self, state_cache):
        """Returns a copy — mutating it does not affect the cache."""
        state_cache.set("device_1", "isOn", True)
        ids = state_cache.get_all_logical_ids()
        ids.add("injected")

        assert "injected" not in state_cache.get_all_logical_ids()


# ── device_count() and attribute_count() ─────────────────────────────────────


class TestCounts:
    @pytest.mark.unit
    def test_device_count_empty(self, state_cache):
        """device_count is 0 for empty cache."""
        assert state_cache.device_count() == 0

    @pytest.mark.unit
    def test_device_count(self, state_cache):
        """device_count reflects the number of distinct logical_ids."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_2", "isOn", True)
        assert state_cache.device_count() == 2

    @pytest.mark.unit
    def test_attribute_count_total(self, state_cache):
        """attribute_count() with no arg returns total across all devices."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_1", "lightLevel", 80)
        state_cache.set("device_2", "isOn", False)
        assert state_cache.attribute_count() == 3

    @pytest.mark.unit
    def test_attribute_count_per_device(self, state_cache):
        """attribute_count(logical_id) returns count for that device."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_1", "lightLevel", 80)
        state_cache.set("device_2", "isOn", False)

        assert state_cache.attribute_count("device_1") == 2
        assert state_cache.attribute_count("device_2") == 1

    @pytest.mark.unit
    def test_attribute_count_unknown_device(self, state_cache):
        """attribute_count for unknown device returns 0."""
        assert state_cache.attribute_count("unknown") == 0


# ── clear_device() ────────────────────────────────────────────────────────────


class TestClearDevice:
    @pytest.mark.unit
    def test_clear_device_removes_all_attributes(self, state_cache):
        """clear_device removes all cached attributes for that device."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_1", "lightLevel", 80)
        state_cache.set("device_2", "isOn", False)

        state_cache.clear_device("device_1")

        assert state_cache.get("device_1", "isOn") is None
        assert state_cache.get("device_1", "lightLevel") is None
        assert state_cache.device_count() == 1

    @pytest.mark.unit
    def test_clear_device_does_not_affect_others(self, state_cache):
        """clear_device does not affect other devices."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_2", "isOn", False)

        state_cache.clear_device("device_1")

        assert state_cache.get("device_2", "isOn") is False

    @pytest.mark.unit
    def test_clear_device_unknown_is_noop(self, state_cache):
        """clear_device for unknown device does not raise."""
        state_cache.clear_device("nonexistent")


# ── clear() ───────────────────────────────────────────────────────────────────


class TestClear:
    @pytest.mark.unit
    def test_clear_wipes_all_devices(self, state_cache):
        """clear() removes all devices and attributes."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_2", "isOn", False)
        state_cache.set("device_3", "illuminance", 100)

        state_cache.clear()

        assert state_cache.device_count() == 0
        assert state_cache.attribute_count() == 0
        assert state_cache.get_all_logical_ids() == set()

    @pytest.mark.unit
    def test_clear_empty_cache_is_noop(self, state_cache):
        """clear() on empty cache does not raise."""
        state_cache.clear()
        assert state_cache.device_count() == 0

    @pytest.mark.unit
    def test_can_write_after_clear(self, state_cache):
        """Cache is usable again after clear()."""
        state_cache.set("device_1", "isOn", True)
        state_cache.clear()
        assert state_cache.set("device_1", "isOn", True) is True


# ── snapshot() ────────────────────────────────────────────────────────────────


class TestSnapshot:
    @pytest.mark.unit
    def test_snapshot_returns_all_data(self, state_cache):
        """snapshot() returns all devices and attributes."""
        state_cache.set("device_1", "isOn", True)
        state_cache.set("device_1", "lightLevel", 80)
        state_cache.set("device_2", "illuminance", 500)

        snap = state_cache.snapshot()

        assert "device_1" in snap
        assert "device_2" in snap
        assert snap["device_1"]["isOn"] is True
        assert snap["device_1"]["lightLevel"] == 80
        assert snap["device_2"]["illuminance"] == 500

    @pytest.mark.unit
    def test_snapshot_is_deep_copy(self, state_cache):
        """Mutating the snapshot does not affect the cache."""
        state_cache.set("device_1", "isOn", True)
        snap = state_cache.snapshot()

        snap["device_1"]["isOn"] = False

        assert state_cache.get("device_1", "isOn") is True

    @pytest.mark.unit
    def test_snapshot_empty_cache(self, state_cache):
        """snapshot() of empty cache returns empty dict."""
        assert state_cache.snapshot() == {}

    @pytest.mark.unit
    def test_snapshot_does_not_share_references(self, state_cache):
        """Adding keys to snapshot does not affect the cache."""
        state_cache.set("device_1", "isOn", True)
        snap = state_cache.snapshot()
        snap["injected_device"] = {"isOn": False}

        assert "injected_device" not in state_cache.get_all_logical_ids()
