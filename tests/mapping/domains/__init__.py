""" "
Tests for app/mapping/domains/__init__.py

    - make_unique_id():      invalid logical_id / invalid suffix raise paths
    - make_battery_entity(): all four validation raise paths
    - _register_mappers():   module missing DEVICE_TYPES, duplicate
                              device-type registration (overwrite-with-warning
                              branch), ImportError branch, generic Exception
                              branch

NOTE: `_register_mappers()` runs at *module import time* against the real
domain modules. To exercise its error branches deliberately we call it again
directly with `importlib.import_module` monkeypatched, then restore the real
DEVICE_TYPE_REGISTRY afterward so we don't leak fake mappers into other
tests (e.g. device_mapper tests that rely on the real registry contents).
"""

from __future__ import annotations

import importlib
import types
from collections.abc import Generator
from typing import Any

import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from ha_mqtt_sdk import DeviceInfo, Entity

from dirigera_bridge.core.errors import DirigeraBridgeError
from dirigera_bridge.mapping import DeviceContext
from dirigera_bridge.mapping import domains as domains_module
from dirigera_bridge.mapping.domains import make_battery_entity, make_unique_id

# make_unique_id
# ──────────────────────────────────────────────────────────────────────────


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


class TestMakeUniqueId:
    def test_valid_without_suffix(self) -> None:
        assert make_unique_id("fff75d00_1") == "dirigera_fff75d00_1"

    def test_valid_with_suffix(self) -> None:
        assert make_unique_id("fff75d00_1", "battery") == "dirigera_fff75d00_1_battery"

    def test_hyphens_are_replaced_with_underscores(self) -> None:
        assert make_unique_id("fff-75d-00") == "dirigera_fff_75d_00"

    @pytest.mark.parametrize("bad_logical_id", ["", "   ", None, 123])
    def test_invalid_logical_id_raises(self, bad_logical_id: str | None | int) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_unique_id(bad_logical_id)  # type: ignore[arg-type]
        assert "logical_id must be a non-empty string" in str(exc_info.value)

    @pytest.mark.parametrize("bad_suffix", ["   ", 123])
    def test_invalid_suffix_raises(self, bad_suffix: str | int) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_unique_id("fff75d00_1", bad_suffix)  # type: ignore[arg-type]
        assert "suffix must be a non-empty string" in str(exc_info.value)

    def test_empty_suffix_is_ignored_not_raised(self) -> None:
        # suffix="" is falsy -> skips the suffix branch entirely, no raise
        assert make_unique_id("fff75d00_1", "") == "dirigera_fff75d00_1"


# ──────────────────────────────────────────────────────────────────────────
# make_battery_entity
# ──────────────────────────────────────────────────────────────────────────


VALID_DEVICE_INFO = {"identifiers": ["dirigera_fff75d00_1"], "name": "Test Device"}


class TestMakeBatteryEntity:
    def test_valid_creates_entity(self) -> None:
        entity = make_battery_entity(
            logical_id="fff75d00_1",
            device_name="Test Device",
            device_info=MockDeviceInfo(),
            battery_pct=87,
        )
        assert entity.unique_id == "dirigera_fff75d00_1_battery"
        assert entity.name == "Test Device Battery"

    @pytest.mark.parametrize("bad_logical_id", ["", "   ", None, 42])
    def test_invalid_logical_id_raises(self, bad_logical_id: str | None | int) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity(
                logical_id=bad_logical_id,  # type: ignore[arg-type]
                device_name="Test Device",
                device_info=MockDeviceInfo(),
                battery_pct=50,
            )
        assert "logical_id must be a non-empty string" in str(exc_info.value)

    @pytest.mark.parametrize("bad_device_name", ["", "   ", None, 42])
    def test_invalid_device_name_raises(self, bad_device_name: str | None | int) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity(
                logical_id="fff75d00_1",
                device_name=bad_device_name,  # type: ignore[arg-type]
                device_info=MockDeviceInfo(),
                battery_pct=50,
            )
        assert "device_name must be a non-empty string" in str(exc_info.value)

    @pytest.mark.parametrize("bad_device_info", [None, "not-a-dict", 42, ["a", "list"]])
    def test_invalid_device_info_raises(
        self, bad_device_info: None | str | int | list[str]
    ) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity(
                logical_id="fff75d00_1",
                device_name="Test Device",
                device_info=bad_device_info,  # type: ignore[arg-type]
                battery_pct=50,
            )
        assert "device_info must be DeviceInfo (dict)" in str(exc_info.value)

    @pytest.mark.parametrize("bad_battery_pct", [-1, 101, "50", None, 50.5])
    def test_invalid_battery_pct_raises(self, bad_battery_pct: str | None | int | float) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            make_battery_entity(
                logical_id="fff75d00_1",
                device_name="Test Device",
                device_info=MockDeviceInfo(),
                battery_pct=bad_battery_pct,  # type: ignore[arg-type]
            )
        assert "battery_pct must be int 0" in str(exc_info.value)


# ──────────────────────────────────────────────────────────────────────────
# _register_mappers (module-load-time plugin registry)
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def preserve_registry() -> Generator[None, Any]:
    """Snapshot and restore DEVICE_TYPE_REGISTRY.

    _register_mappers() mutates the module-level DEVICE_TYPE_REGISTRY
    dict in place. Tests below call it again with a patched importer,
    so we snapshot the real (already-populated) registry first and put
    it back afterward to avoid leaking fake mappers into other tests.
    """
    original = dict(domains_module.DEVICE_TYPE_REGISTRY)
    yield
    domains_module.DEVICE_TYPE_REGISTRY.clear()
    domains_module.DEVICE_TYPE_REGISTRY.update(original)


class FakeDomainModule(types.ModuleType):
    DEVICE_TYPES: dict[Any, Any] | None


def _make_fake_module(
    device_types: dict[Any, Any] | None,
) -> FakeDomainModule:
    """Build a bare module object, optionally exposing DEVICE_TYPES."""
    fake = FakeDomainModule("fake_domain_module")
    fake.DEVICE_TYPES = device_types
    return fake


class TestRegisterMappers:
    def test_module_without_device_types_is_skipped(
        self, monkeypatch: MonkeyPatch, preserve_registry: None, caplog: LogCaptureFixture
    ) -> None:
        def fake_import_module() -> FakeDomainModule:
            return _make_fake_module(device_types=None)  # no DEVICE_TYPES attr

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        with caplog.at_level("WARNING"):
            domains_module._register_mappers()  # must not raise

        assert "has no DEVICE_TYPES dict" in caplog.text

    def test_duplicate_device_type_overwrites_with_warning(
        self, monkeypatch: MonkeyPatch, preserve_registry: None, caplog: LogCaptureFixture
    ) -> None:
        def first_mapper(_ctx: DeviceContext, _info: DeviceInfo) -> list[Entity]:
            return []

        def second_mapper(_ctx: DeviceContext, _info: DeviceInfo) -> list[Entity]:
            return []

        calls = {"count": 0}

        def fake_import_module() -> FakeDomainModule:
            # Every simulated module registers the same deviceType, so
            # the 2nd...Nth registration hits the "already registered"
            # warning branch, and the last one wins.
            calls["count"] += 1
            mapper = first_mapper if calls["count"] == 1 else second_mapper
            return _make_fake_module({"light": mapper})

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        with caplog.at_level("WARNING"):
            domains_module._register_mappers()

        assert "already registered" in caplog.text
        assert domains_module.DEVICE_TYPE_REGISTRY["light"] is second_mapper

    def test_import_error_is_caught_and_logged(
        self,
        monkeypatch: MonkeyPatch,
        preserve_registry: None,
        caplog: LogCaptureFixture,
    ) -> None:
        def fake_import_module() -> None:
            raise ImportError("simulated missing dependency")

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        with caplog.at_level("WARNING"):
            domains_module._register_mappers()  # must not raise/propagate

        assert "Failed to import domain module" in caplog.text

    def test_unexpected_exception_is_caught_and_logged(
        self, monkeypatch: MonkeyPatch, preserve_registry: None, caplog: LogCaptureFixture
    ) -> None:
        def fake_import_module() -> None:
            raise ValueError("simulated unexpected failure")

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        with caplog.at_level("WARNING"):
            domains_module._register_mappers()  # must not raise/propagate

        assert "Unexpected error registering domain module" in caplog.text

    def test_real_registry_is_populated_on_actual_import(self) -> None:
        # Sanity check that the module-load-time call (not mocked) did
        # what it's supposed to: real device types are registered.
        assert "light" in domains_module.DEVICE_TYPE_REGISTRY
        assert callable(domains_module.DEVICE_TYPE_REGISTRY["light"])
