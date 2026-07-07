"""
tests/core/test_discovery_cache.py

Tests for app/core/discovery_cache.py

Covers:
    - RegistrationRecord — immutable, correct fields
    - DiscoveryCache initial state
    - register() — single-deviceType device
    - register() — multi-deviceType device (VALLHORN pattern)
    - register() — idempotent (update on re-register)
    - register() — validation errors
    - unregister() — removes from both primary and secondary index
    - unregister() — last sibling removes relation from secondary index
    - unregister() — unknown id is no-op
    - is_registered() — True/False, validation
    - get_record() — returns RegistrationRecord or None
    - get_registered_domains() — correct frozenset, empty for unknown
    - get_all_logical_ids() — returns copy
    - get_logical_ids_for_relation() — single, multiple siblings, unknown
    - registered_count() and relation_count()
    - clear() — wipes both indexes
    - snapshot() — returns copy, values are RegistrationRecord
"""

import pytest

from app.core.discovery_cache import RegistrationRecord
from app.core.errors import DirigeraBridgeError, ErrorCode


# ── RegistrationRecord ────────────────────────────────────────────────────────


class TestRegistrationRecord:
    @pytest.mark.unit
    def test_is_frozen(self):
        """RegistrationRecord is immutable (frozen dataclass)."""
        record = RegistrationRecord(
            logical_id="abc_1",
            relation_id="abc",
            ha_domains=frozenset({"light"}),
            device_name="Test Light",
        )
        with pytest.raises((AttributeError, TypeError)):
            record.logical_id = "changed"

    @pytest.mark.unit
    def test_fields_accessible(self):
        """All fields are accessible as attributes."""
        record = RegistrationRecord(
            logical_id="abc_1",
            relation_id="abc",
            ha_domains=frozenset({"binary_sensor", "sensor"}),
            device_name="Motion Sensor",
        )
        assert record.logical_id == "abc_1"
        assert record.relation_id == "abc"
        assert record.ha_domains == frozenset({"binary_sensor", "sensor"})
        assert record.device_name == "Motion Sensor"

    @pytest.mark.unit
    def test_ha_domains_is_frozenset(self):
        """ha_domains is stored as a frozenset."""
        record = RegistrationRecord(
            logical_id="abc_1",
            relation_id="abc",
            ha_domains=frozenset({"sensor"}),
            device_name="Test",
        )
        assert isinstance(record.ha_domains, frozenset)


# ── DiscoveryCache initial state ──────────────────────────────────────────────


class TestDiscoveryCacheInitialState:
    @pytest.mark.unit
    def test_initially_empty(self, discovery_cache):
        """Fresh cache has no registrations."""
        assert discovery_cache.registered_count() == 0
        assert discovery_cache.relation_count() == 0
        assert discovery_cache.get_all_logical_ids() == set()

    @pytest.mark.unit
    def test_snapshot_initially_empty(self, discovery_cache):
        """snapshot() returns empty dict on fresh cache."""
        assert discovery_cache.snapshot() == {}


# ── register() — single-deviceType device ────────────────────────────────────


class TestRegisterSingleDevice:
    @pytest.mark.unit
    def test_register_single_device(self, discovery_cache):
        """Can register a single-deviceType device."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Woonkamerlamp",
        )
        assert discovery_cache.is_registered("light_1")
        assert discovery_cache.registered_count() == 1
        assert discovery_cache.relation_count() == 1

    @pytest.mark.unit
    def test_registered_domains_correct(self, discovery_cache):
        """get_registered_domains returns correct domains."""
        discovery_cache.register(
            logical_id="outlet_1",
            relation_id="outlet_1",
            ha_domains=["switch", "sensor"],
            device_name="Smart Plug",
        )
        domains = discovery_cache.get_registered_domains("outlet_1")
        assert domains == frozenset({"switch", "sensor"})

    @pytest.mark.unit
    def test_get_record_returns_correct_record(self, discovery_cache):
        """get_record returns the correct RegistrationRecord."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Woonkamerlamp",
        )
        record = discovery_cache.get_record("light_1")
        assert isinstance(record, RegistrationRecord)
        assert record.logical_id == "light_1"
        assert record.relation_id == "light_1"
        assert record.ha_domains == frozenset({"light"})
        assert record.device_name == "Woonkamerlamp"


# ── register() — multi-deviceType device (VALLHORN pattern) ──────────────────


class TestRegisterMultiDeviceType:
    RELATION = "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    def test_register_both_siblings(self, discovery_cache):
        """Registering both VALLHORN siblings works correctly."""
        discovery_cache.register(
            logical_id=self.RELATION + "_1",
            relation_id=self.RELATION,
            ha_domains=["binary_sensor", "sensor"],
            device_name="Bewegingssensor Gang",
        )
        discovery_cache.register(
            logical_id=self.RELATION + "_3",
            relation_id=self.RELATION,
            ha_domains=["sensor"],
            device_name="Bewegingssensor Gang",
        )

        assert discovery_cache.registered_count() == 2
        assert discovery_cache.relation_count() == 1

    @pytest.mark.unit
    def test_get_logical_ids_for_relation_returns_both(self, discovery_cache):
        """get_logical_ids_for_relation returns both sibling ids."""
        discovery_cache.register(
            logical_id=self.RELATION + "_1",
            relation_id=self.RELATION,
            ha_domains=["binary_sensor"],
            device_name="Motion Sensor",
        )
        discovery_cache.register(
            logical_id=self.RELATION + "_3",
            relation_id=self.RELATION,
            ha_domains=["sensor"],
            device_name="Motion Sensor",
        )

        siblings = discovery_cache.get_logical_ids_for_relation(self.RELATION)
        assert siblings == {
            self.RELATION + "_1",
            self.RELATION + "_3",
        }

    @pytest.mark.unit
    def test_each_sibling_has_independent_domains(self, discovery_cache):
        """Each sibling has its own ha_domains."""
        discovery_cache.register(
            logical_id=self.RELATION + "_1",
            relation_id=self.RELATION,
            ha_domains=["binary_sensor", "sensor"],
            device_name="Motion Sensor",
        )
        discovery_cache.register(
            logical_id=self.RELATION + "_3",
            relation_id=self.RELATION,
            ha_domains=["sensor"],
            device_name="Motion Sensor",
        )

        d1 = discovery_cache.get_registered_domains(self.RELATION + "_1")
        d3 = discovery_cache.get_registered_domains(self.RELATION + "_3")

        assert d1 == frozenset({"binary_sensor", "sensor"})
        assert d3 == frozenset({"sensor"})


# ── register() — idempotent update ───────────────────────────────────────────


class TestRegisterIdempotent:
    @pytest.mark.unit
    def test_re_register_updates_record(self, discovery_cache):
        """Re-registering an existing logical_id updates the record."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Old Name",
        )
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light", "sensor"],
            device_name="New Name",
        )

        assert discovery_cache.registered_count() == 1
        record = discovery_cache.get_record("light_1")
        assert record.ha_domains == frozenset({"light", "sensor"})
        assert record.device_name == "New Name"

    @pytest.mark.unit
    def test_re_register_does_not_duplicate_in_secondary_index(self, discovery_cache):
        """Re-registering does not add duplicate entries to the relation index."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )

        siblings = discovery_cache.get_logical_ids_for_relation("light_1")
        assert len(siblings) == 1


# ── register() — validation ───────────────────────────────────────────────────


class TestRegisterValidation:
    @pytest.mark.unit
    def test_empty_logical_id_raises(self, discovery_cache):
        """Empty logical_id raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.register(
                logical_id="",
                relation_id="rel_1",
                ha_domains=["light"],
                device_name="Test",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_relation_id_raises(self, discovery_cache):
        """Empty relation_id raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.register(
                logical_id="light_1",
                relation_id="",
                ha_domains=["light"],
                device_name="Test",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_ha_domains_raises(self, discovery_cache):
        """Empty ha_domains list raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.register(
                logical_id="light_1",
                relation_id="light_1",
                ha_domains=[],
                device_name="Test",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_domain_string_raises(self, discovery_cache):
        """ha_domains containing an empty string raises."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.register(
                logical_id="light_1",
                relation_id="light_1",
                ha_domains=["light", ""],
                device_name="Test",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_device_name_raises(self, discovery_cache):
        """Empty device_name raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.register(
                logical_id="light_1",
                relation_id="light_1",
                ha_domains=["light"],
                device_name="",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── unregister() ──────────────────────────────────────────────────────────────


class TestUnregister:
    RELATION = "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    def test_unregister_removes_device(self, discovery_cache):
        """unregister removes the logical_id from the primary index."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )
        discovery_cache.unregister("light_1")

        assert not discovery_cache.is_registered("light_1")
        assert discovery_cache.registered_count() == 0

    @pytest.mark.unit
    def test_unregister_cleans_secondary_index(self, discovery_cache):
        """unregister removes the entry from the relation index."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )
        discovery_cache.unregister("light_1")

        assert discovery_cache.get_logical_ids_for_relation("light_1") == set()
        assert discovery_cache.relation_count() == 0

    @pytest.mark.unit
    def test_unregister_sibling_keeps_relation(self, discovery_cache):
        """Unregistering one sibling keeps the relation in secondary index."""
        discovery_cache.register(
            logical_id=self.RELATION + "_1",
            relation_id=self.RELATION,
            ha_domains=["binary_sensor"],
            device_name="Motion Sensor",
        )
        discovery_cache.register(
            logical_id=self.RELATION + "_3",
            relation_id=self.RELATION,
            ha_domains=["sensor"],
            device_name="Motion Sensor",
        )

        discovery_cache.unregister(self.RELATION + "_3")

        assert discovery_cache.relation_count() == 1
        siblings = discovery_cache.get_logical_ids_for_relation(self.RELATION)
        assert siblings == {self.RELATION + "_1"}

    @pytest.mark.unit
    def test_unregister_last_sibling_removes_relation(self, discovery_cache):
        """Unregistering the last sibling removes the relation_id."""
        discovery_cache.register(
            logical_id=self.RELATION + "_1",
            relation_id=self.RELATION,
            ha_domains=["binary_sensor"],
            device_name="Motion Sensor",
        )
        discovery_cache.unregister(self.RELATION + "_1")

        assert discovery_cache.relation_count() == 0
        assert discovery_cache.get_logical_ids_for_relation(self.RELATION) == set()

    @pytest.mark.unit
    def test_unregister_unknown_is_noop(self, discovery_cache):
        """unregister for unknown logical_id does not raise."""
        discovery_cache.unregister("nonexistent_id")


# ── is_registered() ───────────────────────────────────────────────────────────


class TestIsRegistered:
    @pytest.mark.unit
    def test_returns_true_for_registered(self, discovery_cache):
        """is_registered returns True for a registered device."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )
        assert discovery_cache.is_registered("light_1") is True

    @pytest.mark.unit
    def test_returns_false_for_unregistered(self, discovery_cache):
        """is_registered returns False for an unregistered device."""
        assert discovery_cache.is_registered("unknown_id") is False

    @pytest.mark.unit
    def test_returns_false_after_unregister(self, discovery_cache):
        """is_registered returns False after unregistering."""
        discovery_cache.register(
            logical_id="light_1",
            relation_id="light_1",
            ha_domains=["light"],
            device_name="Test",
        )
        discovery_cache.unregister("light_1")
        assert discovery_cache.is_registered("light_1") is False

    @pytest.mark.unit
    def test_empty_id_raises(self, discovery_cache):
        """is_registered raises for empty logical_id."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            discovery_cache.is_registered("")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── get_all_logical_ids() ─────────────────────────────────────────────────────


class TestGetAllLogicalIds:
    @pytest.mark.unit
    def test_returns_all_ids(self, discovery_cache):
        """Returns the set of all registered logical_ids."""
        discovery_cache.register("id_1", "id_1", ["light"], "Device 1")
        discovery_cache.register("id_2", "id_2", ["sensor"], "Device 2")

        ids = discovery_cache.get_all_logical_ids()
        assert ids == {"id_1", "id_2"}

    @pytest.mark.unit
    def test_returns_copy(self, discovery_cache):
        """Returns a copy — mutating it does not affect the cache."""
        discovery_cache.register("id_1", "id_1", ["light"], "Device 1")
        ids = discovery_cache.get_all_logical_ids()
        ids.add("injected")

        assert "injected" not in discovery_cache.get_all_logical_ids()


# ── get_logical_ids_for_relation() ────────────────────────────────────────────


class TestGetLogicalIdsForRelation:
    @pytest.mark.unit
    def test_single_device_returns_set_of_one(self, discovery_cache):
        """Single-deviceType device returns a set with one id."""
        discovery_cache.register("light_1", "light_1", ["light"], "Light")
        result = discovery_cache.get_logical_ids_for_relation("light_1")
        assert result == {"light_1"}

    @pytest.mark.unit
    def test_unknown_relation_returns_empty_set(self, discovery_cache):
        """Unknown relation_id returns an empty set."""
        assert discovery_cache.get_logical_ids_for_relation("unknown") == set()

    @pytest.mark.unit
    def test_returns_copy(self, discovery_cache):
        """Returns a copy — mutating it does not affect the cache."""
        discovery_cache.register("light_1", "light_1", ["light"], "Light")
        result = discovery_cache.get_logical_ids_for_relation("light_1")
        result.add("injected")

        assert "injected" not in discovery_cache.get_logical_ids_for_relation("light_1")


# ── clear() ───────────────────────────────────────────────────────────────────


class TestClear:
    @pytest.mark.unit
    def test_clear_wipes_all(self, discovery_cache):
        """clear() removes all registrations from both indexes."""
        discovery_cache.register("id_1", "id_1", ["light"], "Device 1")
        discovery_cache.register("id_2", "id_2", ["sensor"], "Device 2")

        discovery_cache.clear()

        assert discovery_cache.registered_count() == 0
        assert discovery_cache.relation_count() == 0
        assert discovery_cache.get_all_logical_ids() == set()

    @pytest.mark.unit
    def test_clear_empty_cache_is_noop(self, discovery_cache):
        """clear() on empty cache does not raise."""
        discovery_cache.clear()
        assert discovery_cache.registered_count() == 0

    @pytest.mark.unit
    def test_can_register_after_clear(self, discovery_cache):
        """Cache is usable after clear()."""
        discovery_cache.register("id_1", "id_1", ["light"], "Test")
        discovery_cache.clear()
        discovery_cache.register("id_1", "id_1", ["light"], "Test")
        assert discovery_cache.is_registered("id_1")


# ── snapshot() ────────────────────────────────────────────────────────────────


class TestSnapshot:
    @pytest.mark.unit
    def test_snapshot_contains_records(self, discovery_cache):
        """snapshot() contains the registered RegistrationRecords."""
        discovery_cache.register("light_1", "light_1", ["light"], "Test")
        snap = discovery_cache.snapshot()

        assert "light_1" in snap
        assert isinstance(snap["light_1"], RegistrationRecord)

    @pytest.mark.unit
    def test_snapshot_is_copy(self, discovery_cache):
        """Mutating the snapshot does not affect the cache."""
        discovery_cache.register("light_1", "light_1", ["light"], "Test")
        snap = discovery_cache.snapshot()
        snap["injected"] = None

        assert "injected" not in discovery_cache.get_all_logical_ids()

    @pytest.mark.unit
    def test_snapshot_empty_cache(self, discovery_cache):
        """snapshot() of empty cache returns empty dict."""
        assert discovery_cache.snapshot() == {}
