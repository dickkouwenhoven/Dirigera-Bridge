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

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.metrics import MetricName
from app.mapping.device_mapper import DeviceMapper
from app.mapping.device_registry import DeviceContext


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_context(
    logical_id="light_abc_1",
    relation_id="light_abc_1",
    device_type="light",
    device_name="Test Light",
    serial="ABC123",
    is_reachable=True,
):
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


def make_fake_entity(name="Test Entity", domain_value="light"):
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
    def test_valid_construction(self, metrics):
        """DeviceMapper constructs with valid MetricsStore."""
        mapper = DeviceMapper(metrics=metrics)
        assert mapper is not None

    @pytest.mark.unit
    def test_invalid_metrics_raises(self):
        """Non-MetricsStore metrics raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            DeviceMapper(metrics="not_metrics")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── map_device() ──────────────────────────────────────────────────────────────


class TestMapDevice:
    @pytest.mark.unit
    def test_known_device_type_returns_entities(self, metrics):
        """Known device type returns entities from the registered mapper."""
        fake_entity = make_fake_entity()
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: [fake_entity]},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx = make_context(device_type="light")
            result = mapper.map_device(ctx)

        assert len(result) == 1
        assert result[0] is fake_entity

    @pytest.mark.unit
    def test_unknown_device_type_returns_empty(self, metrics):
        """Unknown device type returns empty list."""
        mapper = DeviceMapper(metrics=metrics)

        with patch.dict(
            "app.mapping.domains.DEVICE_TYPE_REGISTRY",
            {},
            clear=True,
        ):
            ctx = make_context(device_type="unknownType")
            result = mapper.map_device(ctx)

        assert result == []

    @pytest.mark.unit
    def test_unknown_device_type_increments_metric(self, metrics):
        """Unknown device type increments MAPPING_UNKNOWN_DEVICE_TYPE."""
        mapper = DeviceMapper(metrics=metrics)

        with patch.dict(
            "app.mapping.domains.DEVICE_TYPE_REGISTRY",
            {},
            clear=True,
        ):
            ctx = make_context(device_type="unknownType")
            mapper.map_device(ctx)

        assert metrics.get(MetricName.MAPPING_UNKNOWN_DEVICE_TYPE) == 1
        assert metrics.get(MetricName.MAPPING_ERRORS) == 1

    @pytest.mark.unit
    def test_mapper_error_returns_empty(self, metrics):
        """Mapper that raises returns [] without propagating the error."""
        mapper = DeviceMapper(metrics=metrics)

        def broken_mapper(ctx, di):
            raise RuntimeError("mapper crashed")

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"broken": broken_mapper},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx = make_context(device_type="broken")
            result = mapper.map_device(ctx)

        assert result == []
        assert metrics.get(MetricName.MAPPING_ERRORS) >= 1

    @pytest.mark.unit
    def test_invalid_context_raises(self, metrics):
        """Non-DeviceContext raises INTERNAL_INVALID_ARGUMENT."""
        mapper = DeviceMapper(metrics=metrics)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_device("not_a_context")

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_metrics_incremented_on_success(self, metrics):
        """Successful mapping increments MAPPING_DEVICES_PROCESSED."""
        fake_entity = make_fake_entity()
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: [fake_entity]},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            ctx = make_context(device_type="light")
            mapper.map_device(ctx)

        assert metrics.get(MetricName.MAPPING_DEVICES_PROCESSED) == 1
        assert metrics.get(MetricName.MAPPING_ENTITIES_CREATED) >= 1

    @pytest.mark.unit
    def test_build_device_info_failure_returns_empty(self, metrics):
        """If _build_device_info returns None, map_device returns []."""
        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": lambda ctx, di: []},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info",
                side_effect=Exception("build failed"),
            ),
        ):
            ctx = make_context(device_type="light")
            result = mapper.map_device(ctx)

        assert result == []


# ── map_devices() ─────────────────────────────────────────────────────────────


class TestMapDevices:
    @pytest.mark.unit
    def test_returns_flat_list(self, metrics):
        """map_devices flattens entities from multiple contexts."""
        e1 = make_fake_entity("Entity 1")
        e2 = make_fake_entity("Entity 2")
        call_count = [0]

        def rotating_mapper(ctx, di):
            call_count[0] += 1
            return [e1] if call_count[0] == 1 else [e2]

        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": rotating_mapper},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info", return_value=MagicMock()
            ),
        ):
            contexts = [
                make_context("light_1", device_type="light"),
                make_context("light_2", device_type="light"),
            ]
            result = mapper.map_devices(contexts)

        assert len(result) == 2

    @pytest.mark.unit
    def test_empty_list_returns_empty(self, metrics):
        """map_devices with empty list returns []."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.map_devices([])
        assert result == []

    @pytest.mark.unit
    def test_invalid_input_raises(self, metrics):
        """Non-list input raises INTERNAL_INVALID_ARGUMENT."""
        mapper = DeviceMapper(metrics=metrics)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            mapper.map_devices("not_a_list")

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_one_bad_device_does_not_stop_others(self, metrics):
        """A failing mapper for one device does not prevent others."""
        good_entity = make_fake_entity("Good Entity")
        call_count = [0]

        def sometimes_broken(ctx, di):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first mapper fails")
            return [good_entity]

        mapper = DeviceMapper(metrics=metrics)

        with (
            patch.dict(
                "app.mapping.domains.DEVICE_TYPE_REGISTRY",
                {"light": sometimes_broken},
                clear=False,
            ),
            patch(
                "app.mapping.device_mapper.build_device_info", return_value=MagicMock()
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
    def test_returns_list(self, metrics):
        """supported_device_types returns a list."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.supported_device_types()
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_returns_sorted_list(self, metrics):
        """supported_device_types returns a sorted list."""
        mapper = DeviceMapper(metrics=metrics)
        result = mapper.supported_device_types()
        assert result == sorted(result)

    @pytest.mark.unit
    def test_contains_known_device_types(self, metrics):
        """Known device types are present in the registry."""
        mapper = DeviceMapper(metrics=metrics)
        types = mapper.supported_device_types()
        for expected in ["light", "outlet", "motionSensor", "gateway"]:
            assert expected in types, f"'{expected}' not in registry"
