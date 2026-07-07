"""
tests/mapping/domains/test_speaker.py

Tests for app/mapping/domains/speaker.py

speaker.py composes six HA entities (binary_sensor, sensor, number,
two buttons, switch) instead of a single media_player, since HA's
MQTT discovery has no media_player domain. See speaker.py's module
docstring for which parts of this design are CONFIRMED against real
sources vs ASSUMED/UNVERIFIED pending a real device capture.

Covers:
    - map_speaker() — always produces exactly six entities
    - Each entity's domain, name, and unique_id suffix
    - binary_sensor (reachable) — device_class=connectivity
    - sensor (playback) — no device_class, plain string sensor
    - number (volume) — min/max/step/mode configured correctly
    - button (next/previous) — distinct custom payload_press values
    - switch (power) — no special config (plain isOn switch)
    - All six unique_ids are distinct
    - All entity names contain the device name
    - Works with empty attributes (no attrs required — this device
      has no conditional entities, unlike e.g. gateway.py)
    - DEVICE_TYPES registry
"""

import pytest

from app.mapping.domains.speaker import DEVICE_TYPES, map_speaker


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
    def __init__(self, attrs=None, name="SYMFONISK", lid="speaker_abc_1"):
        self.logical_id = lid
        self.device_name = name
        self.attributes = MockAttrs(attrs or {})


def _by_suffix(entities, suffix):
    """Find the single entity whose unique_id ends with the given suffix."""
    matches = [e for e in entities if e.unique_id.endswith(f"_{suffix}")]
    assert len(matches) == 1, f"expected exactly one entity with suffix '{suffix}'"
    return matches[0]


# ── Structure ──────────────────────────────────────────────────────────────────


class TestMapSpeakerStructure:
    @pytest.mark.unit
    def test_returns_six_entities(self):
        """map_speaker always returns exactly six entities."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        assert isinstance(result, list)
        assert len(result) == 6

    @pytest.mark.unit
    def test_works_with_empty_attributes(self):
        """map_speaker works with no attributes — no conditional entities."""
        ctx = MockContext({})
        result = map_speaker(ctx, MockDeviceInfo())
        assert len(result) == 6

    @pytest.mark.unit
    def test_all_unique_ids_distinct(self):
        """All six unique_ids are distinct."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert len(uids) == len(set(uids))

    @pytest.mark.unit
    def test_all_unique_ids_have_dirigera_prefix(self):
        """All unique_ids start with 'dirigera_'."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        for entity in result:
            assert entity.unique_id.startswith("dirigera_")

    @pytest.mark.unit
    def test_unique_id_hyphens_replaced(self):
        """Hyphens in logical_id are replaced with underscores."""
        ctx = MockContext(lid="speaker-abc-1")
        result = map_speaker(ctx, MockDeviceInfo())
        for entity in result:
            assert "-" not in entity.unique_id

    @pytest.mark.unit
    def test_all_entity_names_contain_device_name(self):
        """All entity names contain the device name."""
        ctx = MockContext(name="SYMFONISK Woonkamer")
        result = map_speaker(ctx, MockDeviceInfo())
        for entity in result:
            assert "SYMFONISK Woonkamer" in entity.name

    @pytest.mark.unit
    def test_no_battery_entity(self):
        """No battery entity — SYMFONISK is mains powered."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        uids = [e.unique_id for e in result]
        assert not any("battery" in uid for uid in uids)


# ── binary_sensor: reachable ─────────────────────────────────────────────────


class TestReachableSensor:
    @pytest.mark.unit
    def test_domain_is_binary_sensor(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "reachable")
        assert entity.domain.value == "binary_sensor"

    @pytest.mark.unit
    def test_connectivity_device_class(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "reachable")
        assert entity.extra["device_class"] == "connectivity"

    @pytest.mark.unit
    def test_payload_on_off(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "reachable")
        assert entity.extra["payload_on"] == "ON"
        assert entity.extra["payload_off"] == "OFF"

    @pytest.mark.unit
    def test_is_diagnostic(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "reachable")
        assert entity.extra["entity_category"] == "diagnostic"


# ── sensor: playback ─────────────────────────────────────────────────────────


class TestPlaybackSensor:
    @pytest.mark.unit
    def test_domain_is_sensor(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "playback")
        assert entity.domain.value == "sensor"

    @pytest.mark.unit
    def test_no_device_class(self):
        """Plain string sensor — no device_class, unlike e.g. battery."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "playback")
        assert entity.extra == {}


# ── number: volume ───────────────────────────────────────────────────────────


class TestVolumeNumber:
    @pytest.mark.unit
    def test_domain_is_number(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "volume")
        assert entity.domain.value == "number"

    @pytest.mark.unit
    def test_range_is_0_to_100_step_1(self):
        """Matches Dirigera's confirmed native 0-100 int volume range."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "volume")
        assert entity.extra["min"] == 0
        assert entity.extra["max"] == 100
        assert entity.extra["step"] == 1

    @pytest.mark.unit
    def test_mode_is_slider(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "volume")
        assert entity.extra["mode"] == "slider"


# ── button: next / previous ──────────────────────────────────────────────────


class TestTrackButtons:
    @pytest.mark.unit
    def test_next_button_domain(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "next")
        assert entity.domain.value == "button"

    @pytest.mark.unit
    def test_previous_button_domain(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "previous")
        assert entity.domain.value == "button"

    @pytest.mark.unit
    def test_next_payload_press(self):
        """Custom payload lets command_mapper distinguish next vs previous
        purely by payload content, without needing per-topic routing."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "next")
        assert entity.extra["payload_press"] == "NEXT"

    @pytest.mark.unit
    def test_previous_payload_press(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "previous")
        assert entity.extra["payload_press"] == "PREVIOUS"

    @pytest.mark.unit
    def test_next_and_previous_payloads_distinct(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        next_entity = _by_suffix(result, "next")
        prev_entity = _by_suffix(result, "previous")
        assert next_entity.extra["payload_press"] != prev_entity.extra["payload_press"]


# ── switch: power ─────────────────────────────────────────────────────────────


class TestPowerSwitch:
    @pytest.mark.unit
    def test_domain_is_switch(self):
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "power")
        assert entity.domain.value == "switch"

    @pytest.mark.unit
    def test_no_extra_config(self):
        """Plain switch — no custom payload_on/off, HA defaults apply."""
        ctx = MockContext()
        result = map_speaker(ctx, MockDeviceInfo())
        entity = _by_suffix(result, "power")
        assert entity.extra == {}


# ── DEVICE_TYPES registry ─────────────────────────────────────────────────────


class TestSpeakerDeviceTypes:
    @pytest.mark.unit
    def test_speaker_key_registered(self):
        """DEVICE_TYPES maps 'speaker' to map_speaker."""
        assert "speaker" in DEVICE_TYPES
        assert DEVICE_TYPES["speaker"] is map_speaker

    @pytest.mark.unit
    def test_only_one_key(self):
        """Only 'speaker' registered in this module."""
        assert len(DEVICE_TYPES) == 1
