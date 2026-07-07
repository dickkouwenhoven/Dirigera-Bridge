"""
tests/ha/test_ha_client.py

Tests for app/ha/ha_client.py

HAClient is the bridge's sole integration point with HA-MQTT-SDK. All
MQTT traffic is faked via FakeMQTTClient, a lightweight double
implementing BaseAsyncMQTTClient — no real broker or aiomqtt connection
is made. A real AsyncHASDK/AsyncEntityManager runs underneath it, so
the behaviour HAClient actually depends on (topic building, schema
validation, command routing) is exercised exactly as the SDK
implements it, not re-mocked away.

Covers:
    - Construction validation (settings/metrics/lifecycle/discovery_cache)
    - connect() — builds SDK objects, connects the transport, increments
      connection metrics
    - is_connected — False before connect(), True after, False after
      stop() (regression test for the previously-inverted boolean)
    - register_entity() — validates entity type, requires connection,
      publishes discovery, updates discovery_cache, increments metrics
    - register_entity() — duplicate registration is skipped (cache hit)
    - register_entity() with command_callback — the callback is wired
      through the SDK's own routing and fires on an incoming MQTT
      message (regression test for the message-callback-clobbering bug)
    - register_entity() command_callback errors are caught, not
      propagated, and counted as errors
    - update_availability() — validates entity type, requires
      connection, requires prior registration, publishes a retained
      "online"/"offline" string to the availability topic (regression
      test for the update_state()-instead-of-update_availability() bug)
    - set_all_offline() — marks all given entities offline, tolerates
      per-entity failures without raising
    - get_state_topic() — validates entity type, resolves a real topic
      for state-capable domains, returns None for domains with no
      state topic (regression test for the entity.state_topic bug)
    - update_state_direct() — requires connection, publishes to an
      arbitrary topic, wraps publish failures in DirigeraBridgeError
    - stop() — disconnects cleanly, safe with no prior connect(), safe
      to call twice
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from ha_mqtt_sdk import Entity, HADomain
from ha_mqtt_sdk.mqtt.base_async_mqtt_client import BaseAsyncMQTTClient
from ha_mqtt_sdk.types import PublishPayload

import app.ha.ha_client as ha_client_module
from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.metrics import MetricName
from app.ha.ha_client import HAClient


# ── Fake MQTT transport ─────────────────────────────────────────────────────
# Stands in for aiomqtt so tests never touch a real broker, while still
# letting a real AsyncHASDK run its actual topic-building and command
# routing logic on top of it.


class FakeMQTTClient(BaseAsyncMQTTClient):
    """In-memory double for AsyncMQTTClient."""

    def __init__(self, config: object = None) -> None:
        self.config = config
        self.published: List[Tuple[str, PublishPayload, bool]] = []
        self.subscribed: List[str] = []
        self.last_will: Optional[Tuple[str, str]] = None
        self._callback = None
        self.connected = False
        self.fail_publish = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def publish(
        self, topic: str, payload: PublishPayload, retain: bool = False
    ) -> None:
        if self.fail_publish:
            raise RuntimeError("simulated publish failure")
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def set_message_callback(self, callback) -> None:
        self._callback = callback

    def set_last_will(self, topic: str, payload: str = "offline") -> None:
        self.last_will = (topic, payload)

    async def simulate_incoming(self, topic: str, payload: str) -> None:
        """Deliver an incoming MQTT message exactly as aiomqtt would."""
        assert self._callback is not None, "no message callback was ever registered"
        await self._callback(topic, payload)


def make_entity(
    domain: HADomain = HADomain.SWITCH,
    name: str = "Test Switch",
    unique_id: str = "switch_test_1",
) -> Entity:
    return Entity(domain=domain, name=name, unique_id=unique_id)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_mqtt_factory(monkeypatch):
    """
    Patch app.ha.ha_client.AsyncMQTTClient with FakeMQTTClient.

    Returns the list of instances created, so tests can reach into the
    one FakeMQTTClient a given HAClient actually built.
    """

    created = []

    def _factory(config: object = None) -> FakeMQTTClient:
        client = FakeMQTTClient(config=config)
        created.append(client)
        return client

    monkeypatch.setattr(ha_client_module, "AsyncMQTTClient", _factory)
    return created


@pytest.fixture
def ha_client(settings, metrics, lifecycle, discovery_cache, fake_mqtt_factory):
    """A constructed but not-yet-connected HAClient."""
    return HAClient(settings, metrics, lifecycle, discovery_cache)


@pytest.fixture
async def connected(ha_client, fake_mqtt_factory):
    """A connected HAClient, paired with the FakeMQTTClient behind it."""
    await ha_client.connect()
    fake_mqtt = fake_mqtt_factory[0]
    yield ha_client, fake_mqtt
    await ha_client.stop()


# ── Construction ─────────────────────────────────────────────────────────────


class TestConstruction:
    @pytest.mark.unit
    def test_valid_construction(self, settings, metrics, lifecycle, discovery_cache):
        """HAClient constructs with valid dependencies and starts disconnected."""
        client = HAClient(settings, metrics, lifecycle, discovery_cache)
        assert client.is_connected is False

    @pytest.mark.unit
    def test_invalid_settings_raises(self, metrics, lifecycle, discovery_cache):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient("not settings", metrics, lifecycle, discovery_cache)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_metrics_raises(self, settings, lifecycle, discovery_cache):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, "not metrics", lifecycle, discovery_cache)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_lifecycle_raises(self, settings, metrics, discovery_cache):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, metrics, "not lifecycle", discovery_cache)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_discovery_cache_raises(self, settings, metrics, lifecycle):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, metrics, lifecycle, "not a cache")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── connect() / is_connected / stop() ───────────────────────────────────────


class TestConnectAndLifecycle:
    @pytest.mark.unit
    async def test_is_connected_false_before_connect(self, ha_client):
        assert ha_client.is_connected is False

    @pytest.mark.unit
    async def test_connect_builds_sdk_and_connects_transport(
        self, ha_client, fake_mqtt_factory
    ):
        await ha_client.connect()
        assert ha_client.is_connected is True
        assert fake_mqtt_factory[0].connected is True
        await ha_client.stop()

    @pytest.mark.unit
    async def test_connect_increments_metrics(self, ha_client, metrics):
        await ha_client.connect()
        assert metrics.get(MetricName.MQTT_CONNECT_ATTEMPTS) == 1
        assert metrics.get(MetricName.MQTT_CONNECT_SUCCESS) == 1
        await ha_client.stop()

    @pytest.mark.unit
    async def test_is_connected_false_after_stop(self, connected):
        client, _ = connected
        await client.stop()
        assert client.is_connected is False

    @pytest.mark.unit
    async def test_stop_without_connect_is_safe(self, ha_client):
        await ha_client.stop()
        assert ha_client.is_connected is False

    @pytest.mark.unit
    async def test_stop_twice_is_safe(self, connected):
        client, _ = connected
        await client.stop()
        await client.stop()
        assert client.is_connected is False

    @pytest.mark.unit
    async def test_transport_disconnected_on_stop(self, connected):
        client, fake_mqtt = connected
        await client.stop()
        assert fake_mqtt.connected is False


# ── register_entity() ────────────────────────────────────────────────────────


class TestRegisterEntity:
    @pytest.mark.unit
    async def test_invalid_entity_raises(self, connected):
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.register_entity("not an entity")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_requires_connected(self, ha_client):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.register_entity(make_entity())
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_discovery(self, connected):
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_discovery_test")

        await client.register_entity(entity)

        retained_topics = [t for t, _, retain in fake_mqtt.published if retain]
        assert any(entity.unique_id in t for t in retained_topics)

    @pytest.mark.unit
    async def test_updates_discovery_cache(self, connected, discovery_cache):
        client, _ = connected
        entity = make_entity(unique_id="switch_cache_test")

        await client.register_entity(entity)

        assert discovery_cache.is_registered("switch_cache_test") is True

    @pytest.mark.unit
    async def test_increments_metrics(self, connected, metrics):
        client, _ = connected
        entity = make_entity(unique_id="switch_metrics_test")

        await client.register_entity(entity)

        assert metrics.get(MetricName.ENTITY_REGISTERED) == 1
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) >= 1

    @pytest.mark.unit
    async def test_duplicate_registration_is_skipped(self, connected, metrics):
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_dup_test")
        await client.register_entity(entity)
        published_after_first = len(fake_mqtt.published)

        await client.register_entity(entity)

        assert len(fake_mqtt.published) == published_after_first
        assert metrics.get(MetricName.ENTITY_ALREADY_REGISTERED) == 1


class TestRegisterEntityCommandCallback:
    @pytest.mark.unit
    async def test_command_callback_fires_on_incoming_message(self, connected, metrics):
        """
        Regression test: a command_callback passed to register_entity()
        must fire when HA publishes to the entity's command topic. This
        is the exact path that previously raised TypeError, because the
        bridge overwrote the SDK's own message callback with a sync
        function the transport then tried to await.
        """
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_cmd_test")
        received = []

        async def on_command(topic: str, payload: str) -> None:
            received.append((topic, payload))

        await client.register_entity(entity, command_callback=on_command)

        assert fake_mqtt.subscribed, "no command topic was ever subscribed"
        command_topic = fake_mqtt.subscribed[0]

        await fake_mqtt.simulate_incoming(command_topic, "ON")

        assert received == [(command_topic, "ON")]
        assert metrics.get(MetricName.MQTT_MESSAGES_RECEIVED) == 1

    @pytest.mark.unit
    async def test_command_callback_error_is_caught(self, connected, metrics):
        """A raising callback must not propagate — only counted as an error."""
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_cmd_err_test")

        async def on_command(topic: str, payload: str) -> None:
            raise ValueError("boom")

        await client.register_entity(entity, command_callback=on_command)
        command_topic = fake_mqtt.subscribed[0]

        await fake_mqtt.simulate_incoming(command_topic, "ON")  # must not raise

        assert metrics.get(MetricName.ERROR_MQTT) == 1

    @pytest.mark.unit
    async def test_entity_without_callback_subscribes_nothing(self, connected):
        """
        Domains without a command topic (e.g. sensor) register cleanly
        with no callback and no subscription.
        """
        client, fake_mqtt = connected
        entity = make_entity(
            domain=HADomain.SENSOR, unique_id="sensor_test", name="Test Sensor"
        )

        await client.register_entity(entity)

        assert fake_mqtt.subscribed == []


# ── update_availability() ───────────────────────────────────────────────────


class TestUpdateAvailability:
    @pytest.mark.unit
    async def test_invalid_entity_raises(self, connected):
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_availability("not an entity", True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_requires_connected(self, ha_client):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.update_availability(make_entity(), True)
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_retained_online(self, connected, metrics):
        """
        Regression test: previously this called sdk.update_state(),
        which published the raw bool to the *state* topic. It must now
        publish the string "online", retained, to the *availability*
        topic.
        """
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_avail_test")
        await client.register_entity(entity)
        fake_mqtt.published.clear()

        await client.update_availability(entity, online=True)

        assert len(fake_mqtt.published) == 1
        topic, payload, retain = fake_mqtt.published[0]
        assert topic.endswith("/availability")
        assert payload == "online"
        assert retain is True
        assert metrics.get(MetricName.ENTITY_AVAILABILITY_ONLINE) == 1

    @pytest.mark.unit
    async def test_publishes_retained_offline(self, connected, metrics):
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_avail_off_test")
        await client.register_entity(entity)
        fake_mqtt.published.clear()

        await client.update_availability(entity, online=False)

        topic, payload, retain = fake_mqtt.published[0]
        assert topic.endswith("/availability")
        assert payload == "offline"
        assert retain is True
        assert metrics.get(MetricName.ENTITY_AVAILABILITY_OFFLINE) == 1

    @pytest.mark.unit
    async def test_unregistered_entity_raises(self, connected):
        """The SDK requires an entity be registered before its
        availability can be published."""
        client, _ = connected
        entity = make_entity(unique_id="switch_never_registered")

        with pytest.raises(Exception):  # noqa: B017 — SDK's own EntityError type
            await client.update_availability(entity, True)


# ── set_all_offline() ────────────────────────────────────────────────────────


class TestSetAllOffline:
    @pytest.mark.unit
    async def test_marks_all_entities_offline(self, connected):
        client, fake_mqtt = connected
        entities = [
            make_entity(unique_id="switch_offline_1"),
            make_entity(unique_id="switch_offline_2"),
        ]
        for entity in entities:
            await client.register_entity(entity)
        fake_mqtt.published.clear()

        await client.set_all_offline(entities)

        offline_payloads = [p for _, p, _ in fake_mqtt.published if p == "offline"]
        assert len(offline_payloads) == 2

    @pytest.mark.unit
    async def test_tolerates_per_entity_failure(self, connected):
        """
        One entity that was never registered must not stop the others
        from being marked offline.
        """
        client, fake_mqtt = connected
        good_entity = make_entity(unique_id="switch_good")
        bad_entity = make_entity(unique_id="switch_never_registered_2")
        await client.register_entity(good_entity)
        fake_mqtt.published.clear()

        await client.set_all_offline([bad_entity, good_entity])  # must not raise

        offline_payloads = [p for _, p, _ in fake_mqtt.published if p == "offline"]
        assert len(offline_payloads) == 1


# ── get_state_topic() ────────────────────────────────────────────────────────


class TestGetStateTopic:
    @pytest.mark.unit
    async def test_invalid_entity_raises(self, connected):
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            client.get_state_topic("not an entity")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_resolves_topic_for_state_capable_domain(self, connected):
        client, _ = connected
        entity = make_entity(domain=HADomain.SWITCH, unique_id="switch_topic_test")

        topic = client.get_state_topic(entity)

        assert topic == "homeassistant/switch/switch_topic_test/state"

    @pytest.mark.unit
    async def test_returns_none_for_domain_without_state_topic(self, connected):
        """
        Regression coverage: BUTTON has a command_topic but no
        state_topic — get_state_topic() must return None rather than
        raising, mirroring how orchestrator.py checks `if not
        state_topic`.
        """
        client, _ = connected
        entity = make_entity(
            domain=HADomain.BUTTON, unique_id="button_topic_test", name="Test Button"
        )

        assert client.get_state_topic(entity) is None


# ── update_state_direct() ───────────────────────────────────────────────────


class TestUpdateStateDirect:
    @pytest.mark.unit
    async def test_requires_connected(self, ha_client):
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.update_state_direct("some/topic", "ON")
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_to_given_topic(self, connected, metrics):
        client, fake_mqtt = connected

        await client.update_state_direct("dirigera/switch/foo/state", "ON")

        assert fake_mqtt.published == [("dirigera/switch/foo/state", "ON", False)]
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) == 1

    @pytest.mark.unit
    async def test_publish_failure_raises_bridge_error(self, connected, metrics):
        client, fake_mqtt = connected
        fake_mqtt.fail_publish = True

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_state_direct("dirigera/switch/foo/state", "ON")

        assert exc_info.value.code == ErrorCode.MQTT_PUBLISH_FAILED
        assert metrics.get(MetricName.MQTT_PUBLISH_ERRORS) == 1
