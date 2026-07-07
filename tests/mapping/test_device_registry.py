"""
tests/mapping/test_device_registry.py

Tests for app/mapping/device_registry.py

Covers:
    - DeviceContext dataclass fields and defaults
    - build_device_contexts() — single-deviceType device (light, outlet)
    - build_device_contexts() — multi-deviceType device (VALLHORN)
    - build_device_contexts() — physical grouping by relationId
    - build_device_contexts() — gateway routing to gateway_contexts
    - build_device_contexts() — device_name election (customName → model → deviceType)
    - build_device_contexts() — room_name inherited from primary sibling
    - build_device_contexts() — lightSensor sibling inherits name from motion sibling
    - build_device_contexts() — empty list returns two empty lists
    - build_device_contexts() — invalid input raises
    - _elect_primary() — selects sibling with non-empty customName
    - _elect_device_name() — three-level fallback
    - DeviceContext.is_grouped for single vs grouped devices
"""

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.mapping.device_registry import (
    DeviceContext,
    build_device_contexts,
)


# ── Helpers — build lightweight mock device dicts ────────────────────────────


def make_device(
    logical_id,
    device_type,
    custom_name="",
    model="Test Model",
    manufacturer="IKEA of Sweden",
    serial="ABC123",
    firmware="1.0.0",
    product_code="E0001",
    is_reachable=True,
    relation_id=None,
    room_name=None,
    attributes=None,
    can_receive=None,
):
    """Build a minimal raw Dirigera device dict for testing."""
    payload = {
        "id": logical_id,
        "type": device_type,
        "deviceType": device_type,
        "isReachable": is_reachable,
        "attributes": {
            "customName": custom_name,
            "model": model,
            "manufacturer": manufacturer,
            "serialNumber": serial,
            "firmwareVersion": firmware,
            "productCode": product_code,
            **(attributes or {}),
        },
        "capabilities": {
            "canSend": [],
            "canReceive": can_receive or ["customName"],
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }
    if relation_id:
        payload["relationId"] = relation_id
    if room_name:
        payload["room"] = {
            "id": "room_001",
            "name": room_name,
            "color": "ikea_blue",
            "icon": "rooms_sofa",
        }
    return payload


def parse_devices(raw_list):
    """Parse a list of raw dicts into DirigeraDevice objects."""
    from app.dirigera.models import DirigeraDevice

    return [DirigeraDevice.model_validate(d) for d in raw_list]


# ── DeviceContext dataclass ───────────────────────────────────────────────────


class TestDeviceContext:
    @pytest.mark.unit
    def test_dataclass_fields_accessible(self):
        """All DeviceContext fields are accessible."""
        ctx = DeviceContext(
            logical_id="light_abc_1",
            relation_id="light_abc",
            device_type="light",
            is_reachable=True,
            attributes={"isOn": False},
            capabilities=["customName", "isOn"],
            device_name="Woonkamerlamp",
            room_name="Woonkamer",
            model="TRADFRI bulb GU10",
            manufacturer="IKEA of Sweden",
            serial_number="ABC123",
            product_code="E2010",
            firmware_version="1.0.44",
            is_grouped=False,
        )
        assert ctx.logical_id == "light_abc_1"
        assert ctx.relation_id == "light_abc"
        assert ctx.device_type == "light"
        assert ctx.is_reachable is True
        assert ctx.device_name == "Woonkamerlamp"
        assert ctx.room_name == "Woonkamer"
        assert ctx.is_grouped is False

    @pytest.mark.unit
    def test_is_grouped_defaults_to_false(self):
        """is_grouped defaults to False."""
        ctx = DeviceContext(
            logical_id="x_1",
            relation_id="x",
            device_type="light",
            is_reachable=True,
            attributes={},
            capabilities=[],
            device_name="Test",
            room_name=None,
            model="M",
            manufacturer="IKEA",
            serial_number="S",
            product_code=None,
            firmware_version=None,
        )
        assert ctx.is_grouped is False

    @pytest.mark.unit
    def test_repr_contains_key_fields(self):
        """repr includes logical_id, device_type and device_name."""
        ctx = DeviceContext(
            logical_id="light_1",
            relation_id="light_1",
            device_type="light",
            is_reachable=True,
            attributes={},
            capabilities=[],
            device_name="My Light",
            room_name=None,
            model="M",
            manufacturer="IKEA",
            serial_number="S",
            product_code=None,
            firmware_version=None,
        )
        r = repr(ctx)
        assert "light_1" in r
        assert "light" in r
        assert "My Light" in r


# ── build_device_contexts() — basic ──────────────────────────────────────────


class TestBuildDeviceContextsBasic:
    @pytest.mark.unit
    def test_empty_list_returns_two_empty_lists(self):
        """Empty device list returns ([], [])."""
        regular, gateway = build_device_contexts([])
        assert regular == []
        assert gateway == []

    @pytest.mark.unit
    def test_invalid_input_raises(self):
        """Non-list input raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            build_device_contexts("not_a_list")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_returns_two_lists(self, light_raw):
        """Return value is a tuple of two lists."""
        devices = parse_devices([light_raw])
        result = build_device_contexts(devices)
        assert isinstance(result, tuple)
        assert len(result) == 2
        regular, gateway = result
        assert isinstance(regular, list)
        assert isinstance(gateway, list)


# ── build_device_contexts() — single device ───────────────────────────────────


class TestBuildDeviceContextsSingleDevice:
    @pytest.mark.unit
    def test_single_light_produces_one_context(self, light_raw):
        """Single light device produces one DeviceContext."""
        devices = parse_devices([light_raw])
        regular, gateway = build_device_contexts(devices)
        assert len(regular) == 1
        assert len(gateway) == 0

    @pytest.mark.unit
    def test_light_context_fields(self, light_raw):
        """DeviceContext fields match the light payload."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        ctx = regular[0]

        assert ctx.logical_id == "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"
        assert ctx.device_type == "light"
        assert ctx.device_name == "Raamverlichting"
        assert ctx.room_name == "Woonkamer"
        assert ctx.manufacturer == "IKEA of Sweden"
        assert ctx.is_reachable is False
        assert ctx.is_grouped is False

    @pytest.mark.unit
    def test_light_physical_id_equals_logical_id(self, light_raw):
        """Single device — relation_id equals logical_id."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        ctx = regular[0]
        assert ctx.relation_id == ctx.logical_id

    @pytest.mark.unit
    def test_light_capabilities_populated(self, light_raw):
        """capabilities list is populated from canReceive."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        ctx = regular[0]
        assert "isOn" in ctx.capabilities
        assert "lightLevel" in ctx.capabilities

    @pytest.mark.unit
    def test_light_attributes_populated(self, light_raw):
        """attributes dict contains raw attribute values."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        ctx = regular[0]
        assert "isOn" in ctx.attributes
        assert ctx.attributes["isOn"] is True

    @pytest.mark.unit
    def test_outlet_produces_one_context(self, outlet_raw):
        """Single outlet produces one DeviceContext."""
        devices = parse_devices([outlet_raw])
        regular, _ = build_device_contexts(devices)
        assert len(regular) == 1
        assert regular[0].device_type == "outlet"


# ── build_device_contexts() — multi-deviceType (VALLHORN) ────────────────────


class TestBuildDeviceContextsVallhorn:
    @pytest.mark.unit
    def test_vallhorn_produces_two_contexts(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """VALLHORN pair produces two DeviceContext objects."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, gateway = build_device_contexts(devices)
        assert len(regular) == 2
        assert len(gateway) == 0

    @pytest.mark.unit
    def test_both_siblings_share_relation_id(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """Both VALLHORN siblings have the same relation_id."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        relation_ids = {ctx.relation_id for ctx in regular}
        assert len(relation_ids) == 1
        assert "fff75d00-607c-4f23-a0e7-3dbed0e18b12" in relation_ids

    @pytest.mark.unit
    def test_both_siblings_marked_as_grouped(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """Both VALLHORN siblings have is_grouped=True."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        for ctx in regular:
            assert ctx.is_grouped is True, f"{ctx.device_type} should be grouped"

    @pytest.mark.unit
    def test_light_sensor_inherits_device_name(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """lightSensor sibling inherits device_name from motionSensor."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        names = {ctx.device_name for ctx in regular}
        assert len(names) == 1
        assert "Bewegingssensor Gang" in names

    @pytest.mark.unit
    def test_light_sensor_inherits_room_name(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """lightSensor sibling inherits room_name from motionSensor."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        for ctx in regular:
            assert ctx.room_name == "Gang", f"{ctx.device_type} should have room 'Gang'"

    @pytest.mark.unit
    def test_each_sibling_has_own_logical_id(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """Each sibling has its own distinct logical_id."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        logical_ids = {ctx.logical_id for ctx in regular}
        assert len(logical_ids) == 2

    @pytest.mark.unit
    def test_each_sibling_has_own_device_type(
        self, vallhorn_motion_raw, vallhorn_light_raw
    ):
        """Each sibling has its own device_type."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw])
        regular, _ = build_device_contexts(devices)

        device_types = {ctx.device_type for ctx in regular}
        assert device_types == {"motionSensor", "lightSensor"}


# ── build_device_contexts() — gateway routing ────────────────────────────────


class TestBuildDeviceContextsGateway:
    @pytest.mark.unit
    def test_gateway_goes_to_gateway_list(self, gateway_raw):
        """Gateway device goes to gateway_contexts, not regular_contexts."""
        devices = parse_devices([gateway_raw])
        regular, gateway = build_device_contexts(devices)

        assert len(regular) == 0
        assert len(gateway) == 1

    @pytest.mark.unit
    def test_gateway_context_fields(self, gateway_raw):
        """Gateway DeviceContext has correct device_type."""
        devices = parse_devices([gateway_raw])
        _, gateway = build_device_contexts(devices)

        ctx = gateway[0]
        assert ctx.device_type == "gateway"
        assert ctx.device_name == "Ikea Hub"

    @pytest.mark.unit
    def test_mixed_list_routes_correctly(self, light_raw, gateway_raw):
        """Mixed list routes light to regular and gateway to gateway."""
        devices = parse_devices([light_raw, gateway_raw])
        regular, gateway = build_device_contexts(devices)

        assert len(regular) == 1
        assert len(gateway) == 1
        assert regular[0].device_type == "light"
        assert gateway[0].device_type == "gateway"


# ── build_device_contexts() — device_name election ───────────────────────────


class TestBuildDeviceContextsDeviceNameElection:
    @pytest.mark.unit
    def test_custom_name_used_when_present(self):
        """customName is used as device_name when non-empty."""
        raw = [
            make_device(
                "dev_1", "light", custom_name="My Light", room_name="Living Room"
            )
        ]
        devices = parse_devices(raw)
        regular, _ = build_device_contexts(devices)
        assert regular[0].device_name == "My Light"

    @pytest.mark.unit
    def test_model_used_when_custom_name_empty(self):
        """model is used when customName is empty."""
        raw = [make_device("dev_1", "light", custom_name="", model="TRADFRI bulb E27")]
        devices = parse_devices(raw)
        regular, _ = build_device_contexts(devices)
        assert regular[0].device_name == "TRADFRI bulb E27"

    @pytest.mark.unit
    def test_device_type_used_when_both_empty(self):
        """deviceType is used when both customName and model are empty."""
        raw = [make_device("dev_1", "motionSensor", custom_name="", model="")]
        devices = parse_devices(raw)
        regular, _ = build_device_contexts(devices)
        assert regular[0].device_name == "motionSensor"

    @pytest.mark.unit
    def test_primary_with_name_elected_over_nameless(self):
        """Sibling with non-empty customName is elected as primary."""
        relation = "shared_relation"
        raw = [
            make_device(
                relation + "_1",
                "motionSensor",
                custom_name="Sensor Gang",
                relation_id=relation,
                room_name="Gang",
            ),
            make_device(
                relation + "_3",
                "lightSensor",
                custom_name="",  # no name
                relation_id=relation,
            ),
        ]
        devices = parse_devices(raw)
        regular, _ = build_device_contexts(devices)

        # Both contexts should have the name from the _1 sibling
        for ctx in regular:
            assert ctx.device_name == "Sensor Gang"


# ── build_device_contexts() — firmware and product_code ──────────────────────


class TestBuildDeviceContextsOptionalFields:
    @pytest.mark.unit
    def test_firmware_version_populated(self, light_raw):
        """firmware_version is populated in DeviceContext."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        assert regular[0].firmware_version == "1.0.44"

    @pytest.mark.unit
    def test_product_code_populated(self, outlet_raw):
        """product_code is populated in DeviceContext."""
        devices = parse_devices([outlet_raw])
        regular, _ = build_device_contexts(devices)
        assert regular[0].product_code == "E2206"

    @pytest.mark.unit
    def test_serial_number_populated(self, light_raw):
        """serial_number is populated in DeviceContext."""
        devices = parse_devices([light_raw])
        regular, _ = build_device_contexts(devices)
        assert regular[0].serial_number == "94A081FFFE049D9C"


# ── build_device_contexts() — multiple independent devices ───────────────────


class TestBuildDeviceContextsMultipleDevices:
    @pytest.mark.unit
    def test_multiple_independent_devices(self, light_raw, outlet_raw, vindstyrka_raw):
        """Three independent devices produce three contexts."""
        devices = parse_devices([light_raw, outlet_raw, vindstyrka_raw])
        regular, _ = build_device_contexts(devices)
        assert len(regular) == 3

    @pytest.mark.unit
    def test_all_device_types_present(self, light_raw, outlet_raw, vindstyrka_raw):
        """All device types are represented in the contexts."""
        devices = parse_devices([light_raw, outlet_raw, vindstyrka_raw])
        regular, _ = build_device_contexts(devices)

        device_types = {ctx.device_type for ctx in regular}
        assert device_types == {"light", "outlet", "environmentSensor"}

    @pytest.mark.unit
    def test_mixed_grouped_and_ungrouped(
        self, vallhorn_motion_raw, vallhorn_light_raw, light_raw
    ):
        """Mix of grouped (VALLHORN) and ungrouped (light) devices."""
        devices = parse_devices([vallhorn_motion_raw, vallhorn_light_raw, light_raw])
        regular, _ = build_device_contexts(devices)
        assert len(regular) == 3

        grouped = [ctx for ctx in regular if ctx.is_grouped]
        ungrouped = [ctx for ctx in regular if not ctx.is_grouped]

        assert len(grouped) == 2  # VALLHORN pair
        assert len(ungrouped) == 1  # light
