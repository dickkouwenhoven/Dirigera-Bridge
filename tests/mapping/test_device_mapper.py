"""
tests/mapping/test_device_mapper.py

Tests for app/mapping/device_mapper.py

Covers:
    - DeviceMapper construction and validation
    - map_device() — known device type returns entities
    - map_device() — unknown device type returns [] + increments metric
    - map_device() — mapper error returns [] (never raises)
    - map_device() — invalid context raises INTERNAL_INVALID_ARGUMENT
    - map_device() — metrics incremented correctly
    - map_devices() — flattens results from multiple contexts
    - map_devices() — invalid input raises
    - supported_device_types() — returns sorted list
    - _build_device_info() — called with correct fields
"""

from unittest.mock import MagicMock, patch

import pytest
from ha_mqtt_sdk import DeviceInfo, Entity, HADomain

from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
from dirigera_bridge.core.metrics import MetricName, MetricsStore
from dirigera_bridge.mapping.device_mapper import DeviceMapper, build_device_info
from dirigera_bridge.mapping.device_registry import DeviceContext

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_context(
    logical_id: str = "light_abc_1",
    relation_id: str = "light_abc_1",
    device_type: str = "light",
    device_name: str = "Test Light",
    serial: str = "ABC123",
    is_reachable: bool = True,
) -> DeviceContext:
    return DeviceContext(
        logical_id=logical_id,
        relation_id=relation_id,
        device_type=device_type,
        is_reachable=is_reachable,
        attributes={"isOn": False},
        capabilities=["customName", "isOn"],
        device_name=device_name,
        room_name="Woonkamer",
        model="Test Model",
        manufacturer="IKEA of Sweden",
        serial_number=serial,
        product_code="E0001",
        firmware_version="1.0.0",
        is_grouped=False,
    )


def make_fake_entity(
    name: str = "Test Entity",
    domain_value: str = "light",
) -> Entity:
    entity = MagicMock()
    entity.unique_id = f"dirigera_{name.lower().replace(' ', '_')}"
    entity.name = name
    entity.domain = MagicMock()
    entity.domain.value = domain_value
    entity.state_topic = f"dirigera/{domain_value}/{entity.unique_id}/state"
    entity.command_topic = f"dirigera/{domain_value}/{entity.unique_id}/set"
    return entity


# ── DeviceMapper construction ─────────────────────────────────────────────────


class TestDeviceMapperConstruction:
    @pytest.mark.unit
    def test_valid_construction(self, metrics: MetricsStore) -> None:
        """DeviceMapper constructs with valid MetricsStore."""
        mapper = DeviceMapper(metrics=metrics)
        assert mapper is not None

    @pytest.mark.unit
    def test_invalid_metrics_raises(self) -> None:
        """Non-MetricsStore metrics raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            DeviceMapper(metrics="not_metrics")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── map_device() ──────────────────────────────────────────────────────────────


class TestMapDevice:
    @pytest.mark.unit
    def test_known_device_type_returns_entities(self, metrics: MetricsStore) -> None:
        """Known device type returns entities from the registered mapper."""
        fake_entity = make_fake_entity()
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: [fake_entity]},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx2 = make_context(device_type="light")
            result = mapper.map_device(ctx2)

        assert len(result) == 1
        assert result[0] is fake_entity

    @pytest.mark.unit
    def test_unknown_device_type_returns_empty(self, metrics: MetricsStore) -> None:
        """Unknown device type returns empty list."""
        mapper = DeviceMapper(metrics=metrics)

        with patch.dict(
            "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
            {},
            clear=True,
        ):
            ctx = make_context(device_type="unknownType")
            result = mapper.map_device(ctx)

        assert result == []

    @pytest.mark.unit
    def test_unknown_device_type_increments_metric(self, metrics: MetricsStore) -> None:
        """Unknown device type increments MAPPING_UNKNOWN_DEVICE_TYPE."""
        mapper = DeviceMapper(metrics=metrics)

        with patch.dict(
            "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
            {},
            clear=True,
        ):
            ctx = make_context(device_type="unknownType")
            mapper.map_device(ctx)

        assert metrics.get(MetricName.MAPPING_UNKNOWN_DEVICE_TYPE) == 1
        assert metrics.get(MetricName.MAPPING_ERRORS) == 1

    @pytest.mark.unit
    def test_mapper_error_returns_empty(self, metrics: MetricsStore) -> None:
        """Mapper that raises returns [] without propagating the error."""
        mapper = DeviceMapper(metrics=metrics)

        def broken_mapper() -> None:
            raise RuntimeError("mapper crashed")

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"broken": broken_mapper},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx = make_context(device_type="broken")
            result = mapper.map_device(ctx)

        assert result == []
        assert metrics.get(MetricName.MAPPING_ERRORS) >= 1

    @pytest.mark.unit
    def test_invalid_context_raises(self, metrics: MetricsStore) -> None:
        """Non-DeviceContext raises INTERNAL_INVALID_ARGUMENT."""
        mapper = DeviceMapper(metrics=metrics)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_device("not_a_context")  # type: ignore[arg-type]

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_metrics_incremented_on_success(self, metrics: MetricsStore) -> None:
        """Successful mapping increments MAPPING_DEVICES_PROCESSED."""
        fake_entity = make_fake_entity()
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: [fake_entity]},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx2 = make_context(device_type="light")
            mapper.map_device(ctx2)

        assert metrics.get(MetricName.MAPPING_DEVICES_PROCESSED) == 1
        assert metrics.get(MetricName.MAPPING_ENTITIES_CREATED) >= 1

    @pytest.mark.unit
    def test_build_device_info_failure_returns_empty(self, metrics: MetricsStore) -> None:
        """If _build_device_info returns None, map_device returns []."""
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: []},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info",
                side_effect=Exception("build failed"),
            ),
        ):
            ctx2 = make_context(device_type="light")
            result = mapper.map_device(ctx2)

        assert result == []


# ── map_devices() ─────────────────────────────────────────────────────────────


class TestMapDevices:
    @pytest.mark.unit
    def test_returns_flat_list(self, metrics: MetricsStore) -> None:
        """map_devices flattens entities from multiple contexts."""
        # e1 = make_fake_entity("Entity 1")
        # e2 = make_fake_entity("Entity 2")
        # call_count = [0]

        def rotating_mapper(
            context: DeviceContext,
            device_info: DeviceInfo,
        ) -> list[Entity]:
            return [
                Entity(
                    domain=HADomain.LIGHT,
                    name=context.device_name,
                    unique_id=context.logical_id,
                    device_info=device_info,
                )
            ]

        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": rotating_mapper},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            contexts = [
                make_context("light_1", device_type="light"),
                make_context("light_2", device_type="light"),
            ]
            result = mapper.map_devices(contexts)

        assert len(result) == 2

    @pytest.mark.unit
    def test_empty_list_returns_empty(self, metrics: MetricsStore) -> None:
        """map_devices with empty list returns []."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.map_devices([])
        assert result == []

    @pytest.mark.unit
    def test_invalid_input_raises(self, metrics: MetricsStore) -> None:
        """Non-list input raises INTERNAL_INVALID_ARGUMENT."""
        mapper = DeviceMapper(metrics=metrics)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_devices("not_a_list")  # type: ignore[arg-type]

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_one_bad_device_does_not_stop_others(self, metrics: MetricsStore) -> None:
        """A failing mapper for one device does not prevent others."""
        good_entity = make_fake_entity("Good Entity")
        call_count = [0]

        def sometimes_broken(_context: DeviceContext, _device_info: DeviceInfo) -> list[Entity]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first mapper fails")
            return [good_entity]

        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": sometimes_broken},
                clear=False,
            ),
            patch(
                "dirigera_bridge.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            contexts = [
                make_context("light_1", device_type="light"),
                make_context("light_2", device_type="light"),
            ]
            result = mapper.map_devices(contexts)

        assert len(result) == 1
        assert result[0] is good_entity


# ── supported_device_types() ──────────────────────────────────────────────────


class TestSupportedDeviceTypes:
    @pytest.mark.unit
    def test_returns_list(self, metrics: MetricsStore) -> None:
        """supported_device_types returns a list."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.supported_device_types()
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_returns_sorted_list(self, metrics: MetricsStore) -> None:
        """supported_device_types returns a sorted list."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.supported_device_types()
        assert result == sorted(result)

    @pytest.mark.unit
    def test_contains_known_device_types(self, metrics: MetricsStore) -> None:
        """Known device types are present in the registry."""
        mapper = DeviceMapper(metrics=metrics)
        types = mapper.supported_device_types()
        for expected in ["light", "outlet", "motionSensor", "gateway"]:
            assert expected in types, f"'{expected}' not in registry"


"""
Additional tests for app/mapping/device_mapper.py.

Targets the coverage gaps reported by `pytest --cov --cov-report=term-missing`:

    Missing: 174-180, 316-343

ASSUMPTION — the exact DeviceInfo key names asserted in
test_builds_device_info_for_valid_context are inferred from the
module's own docstring (serial_number/device_name/manufacturer/model
mapping table). Please adjust the key names there if your actual
DeviceInfo TypedDict differs.
"""


# ── map_device() — device_info is None (not a raised exception) ────────────


class TestMapDeviceInfoNone:
    @pytest.mark.unit
    def test_build_device_info_returning_none_returns_empty(self, metrics: MetricsStore) -> None:
        """Distinct from test_build_device_info_failure_returns_empty:
        that test covers build_device_info() *raising*; this covers it
        returning None cleanly, which is a separate branch in
        map_device()."""
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "dirigera_bridge.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda _ctx, _di: []},
                clear=False,
            ),
            patch("dirigera_bridge.mapping.device_mapper.build_device_info", return_value=None),
        ):
            ctx = make_context(device_type="light")
            result = mapper.map_device(ctx)

        assert result == []
        assert metrics.get(MetricName.MAPPING_ERRORS) >= 1


# ── build_device_info() — the real, unpatched implementation ───────────────


class TestBuildDeviceInfo:
    @pytest.mark.unit
    def test_builds_device_info_for_valid_context(self) -> None:
        """Every other test in this file patches build_device_info()
        away entirely — this exercises the real implementation."""
        context = make_context(device_name="Test Light", serial="ABC123")

        result = build_device_info(context)

        assert result is not None
        assert result["name"] == "Test Light"
        assert result["manufacturer"] == "IKEA of Sweden"
        assert result["model"] == "Test Model"
        assert result["serial_number"] == "ABC123"
        assert ("dirigera", "ABC123") in result["identifiers"]

    @pytest.mark.unit
    def test_returns_none_when_create_device_info_raises(self) -> None:
        context = make_context()

        with patch(
            "dirigera_bridge.mapping.device_mapper.create_device_info",
            side_effect=RuntimeError("sdk boom"),
        ):
            result = build_device_info(context)

        assert result is None
