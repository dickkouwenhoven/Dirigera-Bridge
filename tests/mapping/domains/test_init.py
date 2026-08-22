"""
tests/mapping/domains/test_init.py

Covers app/mapping/domains/__init__.py:
    - make_unique_id() validation branches
    - make_battery_entity() validation branches
    - _register_mappers() success / missing-DEVICE_TYPES / ImportError /
      generic-Exception / duplicate-registration branches

_register_mappers() only runs once, automatically, at first import of
dirigera_bridge.mapping.domains -- which happens before pytest-cov starts tracking.
So instead of relying on that side effect, we call it directly here
with importlib.import_module monkeypatched, to exercise every branch
under coverage.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from ha_mqtt_sdk import DeviceInfo, Entity

import dirigera_bridge.mapping.domains as domains_pkg
from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
from dirigera_bridge.mapping.device_registry import DeviceContext
from dirigera_bridge.mapping.domains import make_battery_entity, make_unique_id

# ── make_unique_id ──────────────────────────────────────────────────────────


class TestMakeUniqueId:
    def test_valid_no_suffix(self) -> None:
        assert make_unique_id("abc-123") == "dirigera_abc_123"

    def test_valid_with_suffix(self) -> None:
        assert make_unique_id("abc-123", "battery") == "dirigera_abc_123_battery"

    def test_empty_logical_id_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_unique_id("")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    def test_whitespace_only_logical_id_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError):
            make_unique_id("   ")

    def test_non_string_logical_id_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError):
            make_unique_id(None)  # type: ignore[arg-type]

    def test_whitespace_only_suffix_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_unique_id("abc123", suffix="   ")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    def test_non_string_suffix_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError):
            make_unique_id("abc123", suffix=123)  # type: ignore[arg-type]


# ── make_battery_entity ──────────────────────────────────────────────────────


class TestMakeBatteryEntity:
    @staticmethod
    def _valid_device_info() -> DeviceInfo:
        return {"identifiers": [("dirigera", "abc-123")], "name": "Test Device"}

    def test_valid_creates_entity(self) -> None:
        entity = make_battery_entity(
            logical_id="abc-123",
            device_name="Test Device",
            device_info=self._valid_device_info(),
            battery_pct=87,
        )
        assert entity is not None

    def test_empty_logical_id_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity("", "Test Device", self._valid_device_info(), 50)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    def test_empty_device_name_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity("abc-123", "", self._valid_device_info(), 50)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    def test_non_dict_device_info_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity(
                "abc-123",
                "Test Device",
                "not-a-dict",  # type: ignore[arg-type]
                50,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    def test_battery_pct_out_of_range_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError):
            make_battery_entity("abc-123", "Test Device", self._valid_device_info(), 101)

    def test_battery_pct_non_int_raises(self) -> None:
        with pytest.raises(DirigeraBridgeError):
            make_battery_entity(
                "abc-123",
                "Test Device",
                self._valid_device_info(),
                "50",  # type: ignore[arg-type]
            )


# ── _register_mappers ────────────────────────────────────────────────────────


class TestRegisterMappers:
    """
    _register_mappers() populates the module-level DEVICE_TYPE_REGISTRY.
    We save/restore it around each test so we don't corrupt the real
    registry other tests (or app code) may depend on.
    """

    _original_registry: dict[str, Any]

    def setup_method(self) -> None:
        self._original_registry = dict(domains_pkg.DEVICE_TYPE_REGISTRY)

    def teardown_method(self) -> None:
        domains_pkg.DEVICE_TYPE_REGISTRY.clear()
        domains_pkg.DEVICE_TYPE_REGISTRY.update(self._original_registry)

    def test_success_missing_device_types_import_error_and_exception_branches(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)

        def fake_mapper(_context: DeviceContext, _device_info: DeviceInfo) -> list[Entity]:
            return []

        fake_module_with_types = MagicMock()
        fake_module_with_types.DEVICE_TYPES = {"fakeType": fake_mapper}

        # spec=[] means hasattr(module, "DEVICE_TYPES") is False
        fake_module_without_types = MagicMock(spec=[])

        def fake_import_module(module_path: str, package: str | None = None) -> Any:
            # `package` is required so this fake matches the real
            # importlib.import_module(module_path, package=__name__)
            # call signature used in _register_mappers(); we don't need
            # its value here.
            del package

            if module_path == ".gateway":
                raise ImportError("simulated import failure")
            if module_path == ".light":
                raise ValueError("simulated unexpected failure")
            if module_path == ".outlet":
                return fake_module_without_types
            return fake_module_with_types

        domains_pkg.DEVICE_TYPE_REGISTRY.clear()

        with patch("importlib.import_module", side_effect=fake_import_module):
            domains_pkg._register_mappers()

        # Modules that returned a valid DEVICE_TYPES dict got registered
        assert "fakeType" in domains_pkg.DEVICE_TYPE_REGISTRY

        # ImportError branch logged a warning and did not crash
        assert "Failed to import domain module" in caplog.text
        # Missing-DEVICE_TYPES branch logged a warning and continued
        assert "has no DEVICE_TYPES dict" in caplog.text
        # Generic Exception branch logged a warning and did not crash
        assert "Unexpected error registering domain module" in caplog.text

    def test_duplicate_device_type_logs_overwrite_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)

        def fake_mapper(_context: DeviceContext, _device_info: DeviceInfo) -> list[Entity]:
            return []

        fake_module = MagicMock()
        fake_module.DEVICE_TYPES = {"duplicateType": fake_mapper}

        domains_pkg.DEVICE_TYPE_REGISTRY.clear()

        # Every domain module "successfully" registers the same device
        # type, forcing the overwrite branch on every iteration after
        # the first.
        with patch("importlib.import_module", return_value=fake_module):
            domains_pkg._register_mappers()

        assert domains_pkg.DEVICE_TYPE_REGISTRY["duplicateType"] is fake_mapper
        assert "already registered" in caplog.text
