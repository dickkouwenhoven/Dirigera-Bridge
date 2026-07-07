"""
tests/mapping/domains/test_gateway.py

Tests for app/mapping/domains/gateway.py

Covers:
    - map_gateway() — minimal payload produces at least 1 entity
    - map_gateway() — full payload produces exactly 10 entities
    - map_gateway() — correct HA domains for each entity
    - map_gateway() — binary_sensor entities have device_class=connectivity
    - map_gateway() — conditional entities absent when attributes missing
    - map_gateway() — timestamp sensors have device_class=timestamp
    - map_gateway() — device_tracker has source_type=gps
    - map_gateway() — coordinates accuracy=-1 handled (documented)
    - map_gateway() — coordinates with missing lat/lon skipped
    - map_gateway() — all unique_ids are distinct
    - map_gateway() — all entity names contain device name
    - map_gateway() — diagnostic entities have entity_category=diagnostic
    - DEVICE_TYPES registry maps 'gateway' to map_gateway
"""

import pytest

from app.mapping.domains.gateway import DEVICE_TYPES, map_gateway


# ── Shared mock objects ───────────────────────────────────────────────────────


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
    def __init__(self, attrs, name="DIRIGERA Hub", lid="gw_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs)


FULL_ATTRS = {
    "backendConnected": True,
    "otaStatus": "updateAvailable",
    "otaState": "readyToUpdate",
    "firmwareVersion": "2.815.2",
    "homeState": "home",
    "timezone": "Europe/Amsterdam",
    "nextSunRise": "2026-01-31T07:17:00.000Z",
    "nextSunSet": "2026-01-30T16:19:00.000Z",
    "coordinates": {
        "latitude": 51.87,
        "longitude": 6.24,
        "accuracy": -1,
    },
}


# ── Entity count ──────────────────────────────────────────────────────────────


class TestMapGatewayEntityCount:
    @pytest.mark.unit
    def test_minimal_produces_at_least_one_entity(self):
        """Minimal gateway (no optional attrs) produces 1 entity."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        assert len(result) >= 1

    @pytest.mark.unit
    def test_full_produces_ten_entities(self):
        """Full gateway payload produces exactly 10 entities."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())
        assert len(result) == 10

    @pytest.mark.unit
    def test_without_coordinates_produces_nine_entities(self):
        """Without coordinates, 9 entities are produced."""
        attrs = {k: v for k, v in FULL_ATTRS.items() if k != "coordinates"}
        ctx = MockContext(attrs)
        result = map_gateway(ctx, MockDeviceInfo())
        assert len(result) == 9

    @pytest.mark.unit
    def test_without_backend_connected_produces_nine(self):
        """Without backendConnected, 9 entities are produced."""
        attrs = {k: v for k, v in FULL_ATTRS.items() if k != "backendConnected"}
        ctx = MockContext(attrs)
        result = map_gateway(ctx, MockDeviceInfo())
        assert len(result) == 9


# ── HA domains ────────────────────────────────────────────────────────────────


class TestMapGatewayDomains:
    @pytest.mark.unit
    def test_full_domain_distribution(self):
        """Full payload produces 2 binary_sensor + 7 sensor + 1 device_tracker."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())

        domains = [e.domain.value for e in result]
        assert domains.count("binary_sensor") == 2
        assert domains.count("sensor") == 7
        assert domains.count("device_tracker") == 1

    @pytest.mark.unit
    def test_minimal_has_binary_sensor(self):
        """Minimal gateway has at least one binary_sensor (reachable)."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        domains = [e.domain.value for e in result]
        assert "binary_sensor" in domains


# ── Binary sensors ────────────────────────────────────────────────────────────


class TestMapGatewayBinarySensors:
    @pytest.mark.unit
    def test_reachable_sensor_always_created(self):
        """Reachable binary_sensor is always created."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("reachable" in uid for uid in uids)

    @pytest.mark.unit
    def test_backend_connected_created_when_present(self):
        """backendConnected sensor created when attribute present."""
        ctx = MockContext({"backendConnected": True})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("backend_connected" in uid for uid in uids)

    @pytest.mark.unit
    def test_backend_connected_absent_when_missing(self):
        """backendConnected sensor not created when attribute absent."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert not any("backend_connected" in uid for uid in uids)

    @pytest.mark.unit
    def test_binary_sensors_have_connectivity_device_class(self):
        """All binary_sensor entities have device_class=connectivity."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())
        for entity in result:
            if entity.domain.value == "binary_sensor":
                assert entity.extra.get("device_class") == "connectivity"

    @pytest.mark.unit
    def test_binary_sensors_have_payload_on_off(self):
        """Binary sensors have payload_on and payload_off."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())
        for entity in result:
            if entity.domain.value == "binary_sensor":
                assert entity.extra.get("payload_on") == "ON"
                assert entity.extra.get("payload_off") == "OFF"


# ── String sensors ────────────────────────────────────────────────────────────


class TestMapGatewayStringSensors:
    @pytest.mark.unit
    def test_ota_status_created_when_present(self):
        """otaStatus sensor created when attribute present."""
        ctx = MockContext({"otaStatus": "updateAvailable"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("ota_status" in uid for uid in uids)

    @pytest.mark.unit
    def test_ota_state_created_when_present(self):
        """otaState sensor created when attribute present."""
        ctx = MockContext({"otaState": "readyToUpdate"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("ota_state" in uid for uid in uids)

    @pytest.mark.unit
    def test_firmware_version_created_when_present(self):
        """firmwareVersion sensor created when attribute present."""
        ctx = MockContext({"firmwareVersion": "2.815.2"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("firmware_version" in uid for uid in uids)

    @pytest.mark.unit
    def test_home_state_created_when_present(self):
        """homeState sensor created when attribute present."""
        ctx = MockContext({"homeState": "home"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("home_state" in uid for uid in uids)

    @pytest.mark.unit
    def test_timezone_created_when_present(self):
        """timezone sensor created when attribute present."""
        ctx = MockContext({"timezone": "Europe/Amsterdam"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("timezone" in uid for uid in uids)

    @pytest.mark.unit
    def test_diagnostic_sensors_have_entity_category(self):
        """Diagnostic sensors have entity_category=diagnostic."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())
        diag = [
            e
            for e in result
            if e.extra and e.extra.get("entity_category") == "diagnostic"
        ]
        assert len(diag) >= 3


# ── Timestamp sensors ─────────────────────────────────────────────────────────


class TestMapGatewayTimestampSensors:
    @pytest.mark.unit
    def test_sunrise_created_when_present(self):
        """nextSunRise sensor created when attribute present."""
        ctx = MockContext({"nextSunRise": "2026-01-31T07:17:00.000Z"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("next_sunrise" in uid for uid in uids)

    @pytest.mark.unit
    def test_sunset_created_when_present(self):
        """nextSunSet sensor created when attribute present."""
        ctx = MockContext({"nextSunSet": "2026-01-30T16:19:00.000Z"})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert any("next_sunset" in uid for uid in uids)

    @pytest.mark.unit
    def test_timestamp_sensors_have_device_class(self):
        """Timestamp sensors have device_class=timestamp."""
        ctx = MockContext(
            {
                "nextSunRise": "2026-01-31T07:17:00.000Z",
                "nextSunSet": "2026-01-30T16:19:00.000Z",
            }
        )
        result = map_gateway(ctx, MockDeviceInfo())
        ts_entities = [
            e for e in result if e.extra and e.extra.get("device_class") == "timestamp"
        ]
        assert len(ts_entities) == 2

    @pytest.mark.unit
    def test_sunrise_absent_when_missing(self):
        """nextSunRise sensor not created when attribute absent."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert not any("next_sunrise" in uid for uid in uids)


# ── Device tracker ────────────────────────────────────────────────────────────


class TestMapGatewayDeviceTracker:
    @pytest.mark.unit
    def test_device_tracker_created_with_coordinates(self):
        """device_tracker created when coordinates present."""
        ctx = MockContext(
            {"coordinates": {"latitude": 51.87, "longitude": 6.24, "accuracy": -1}}
        )
        result = map_gateway(ctx, MockDeviceInfo())
        trackers = [e for e in result if e.domain.value == "device_tracker"]
        assert len(trackers) == 1

    @pytest.mark.unit
    def test_device_tracker_has_source_type_gps(self):
        """device_tracker has source_type=gps."""
        ctx = MockContext(
            {"coordinates": {"latitude": 51.87, "longitude": 6.24, "accuracy": -1}}
        )
        result = map_gateway(ctx, MockDeviceInfo())
        tracker = next(e for e in result if e.domain.value == "device_tracker")
        assert tracker.extra.get("source_type") == "gps"

    @pytest.mark.unit
    def test_no_tracker_without_coordinates(self):
        """No device_tracker when coordinates absent."""
        ctx = MockContext({})
        result = map_gateway(ctx, MockDeviceInfo())
        trackers = [e for e in result if e.domain.value == "device_tracker"]
        assert len(trackers) == 0

    @pytest.mark.unit
    def test_no_tracker_when_coordinates_not_dict(self):
        """No device_tracker when coordinates is not a dict."""
        ctx = MockContext({"coordinates": "not_a_dict"})
        result = map_gateway(ctx, MockDeviceInfo())
        trackers = [e for e in result if e.domain.value == "device_tracker"]
        assert len(trackers) == 0

    @pytest.mark.unit
    def test_no_tracker_when_lat_missing(self):
        """No device_tracker when latitude is missing from coordinates."""
        ctx = MockContext({"coordinates": {"longitude": 6.24}})
        result = map_gateway(ctx, MockDeviceInfo())
        trackers = [e for e in result if e.domain.value == "device_tracker"]
        assert len(trackers) == 0

    @pytest.mark.unit
    def test_no_tracker_when_lon_missing(self):
        """No device_tracker when longitude is missing from coordinates."""
        ctx = MockContext({"coordinates": {"latitude": 51.87}})
        result = map_gateway(ctx, MockDeviceInfo())
        trackers = [e for e in result if e.domain.value == "device_tracker"]
        assert len(trackers) == 0


# ── Unique IDs ────────────────────────────────────────────────────────────────


class TestMapGatewayUniqueIds:
    @pytest.mark.unit
    def test_all_unique_ids_distinct(self):
        """All 10 entity unique_ids are distinct."""
        ctx = MockContext(FULL_ATTRS, lid="gw_abc_1")
        result = map_gateway(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_unique_ids_have_dirigera_prefix(self):
        """All unique_ids start with 'dirigera_'."""
        ctx = MockContext(FULL_ATTRS)
        result = map_gateway(ctx, MockDeviceInfo())
        for entity in result:
            assert entity.unique_id.startswith("dirigera_")

    @pytest.mark.unit
    def test_unique_ids_contain_logical_id(self):
        """All unique_ids contain the logical_id (hyphens replaced)."""
        ctx = MockContext(FULL_ATTRS, lid="gw-abc-1")
        result = map_gateway(ctx, MockDeviceInfo())
        for entity in result:
            assert "gw_abc_1" in entity.unique_id


# ── Entity names ──────────────────────────────────────────────────────────────


class TestMapGatewayEntityNames:
    @pytest.mark.unit
    def test_all_entity_names_contain_device_name(self):
        """All entity names contain the device name."""
        ctx = MockContext(FULL_ATTRS, name="DIRIGERA Hub")
        result = map_gateway(ctx, MockDeviceInfo())
        for entity in result:
            assert "DIRIGERA Hub" in entity.name


# ── DEVICE_TYPES registry ─────────────────────────────────────────────────────


class TestGatewayDeviceTypes:
    @pytest.mark.unit
    def test_gateway_key_registered(self):
        """DEVICE_TYPES maps 'gateway' to map_gateway."""
        assert "gateway" in DEVICE_TYPES
        assert DEVICE_TYPES["gateway"] is map_gateway

    @pytest.mark.unit
    def test_only_one_key_registered(self):
        """Only 'gateway' is registered in this module."""
        assert len(DEVICE_TYPES) == 1
