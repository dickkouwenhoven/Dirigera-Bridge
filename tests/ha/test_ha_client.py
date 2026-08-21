"""
tests/ha/test_ha_client.py

Tests for app/ha/ha_client.py

HAClient is the bridge's sole integration point with HA-MQTT-SDK. All
MQTT traffic is faked via FakeMQTTClient, a lightweight double
implementing BaseAsyncMQTTClient — no real broker or aiomqtt connection
is made. A real AsyncHASDK/AsyncEntityManager runs underneath it, so
the behavior HAClient actually depends on (topic building, schema
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

from _collections_abc import Awaitable, Callable
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from ha_mqtt_sdk import AsyncMQTTClient, DeviceInfo, Entity, HADomain, MQTTSettings
from ha_mqtt_sdk.mqtt.base_async_mqtt_client import BaseAsyncMQTTClient
from ha_mqtt_sdk.types import PublishPayload

import app.ha.ha_client as ha_client_module
from app.config import Settings
from app.core import DiscoveryCache, MetricsStore, ServiceLifecycle
from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.lifecycle import LifecycleState
from app.core.metrics import MetricName
from app.ha.ha_client import HAClient

invalid_state: Any = "not an entity"

# routing logic on top of it.


class FakeMQTTClient(BaseAsyncMQTTClient):
    """In-memory double for AsyncMQTTClient."""

    def __init__(self, config: object = None) -> None:
        self.config = config
        self.published: list[tuple[str, PublishPayload, bool]] = []
        self.subscribed: list[str] = []
        self.last_will: tuple[str, str] | None = None
        self._callback: Callable[[str, str], Awaitable[None]] | None = None
        self.connected = False
        self.fail_publish = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def publish(self, topic: str, payload: PublishPayload, retain: bool = False) -> None:
        if self.fail_publish:
            raise RuntimeError("simulated publish failure")
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def set_message_callback(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
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
    device_info: DeviceInfo = {
        "identifiers": [("my_integration", "device_ABC123")],
        "name": "Test Switch",
    }
    return Entity(
        domain=domain,
        name=name,
        unique_id=unique_id,
        device_info=device_info,
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_mqtt_factory(monkeypatch: MonkeyPatch) -> list[Any]:
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

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ha_client_module, "AsyncMQTTClient", _factory)

    return created


@pytest.fixture
def ha_client(
    settings: Settings,
    metrics: MetricsStore,
    lifecycle: ServiceLifecycle,
    discovery_cache: DiscoveryCache,
    fake_mqtt_factory: list[Any],
) -> HAClient:
    """A constructed but not-yet-connected HAClient."""
    return HAClient(settings, metrics, lifecycle, discovery_cache)


@pytest.fixture
async def connected(
    ha_client: HAClient,
    fake_mqtt_factory: list[Any],
) -> AsyncGenerator[tuple[HAClient, Any], Any]:
    """A connected HAClient, paired with the FakeMQTTClient behind it."""
    await ha_client.connect()
    fake_mqtt = fake_mqtt_factory[0]
    yield ha_client, fake_mqtt
    await ha_client.stop()


# ── Construction ─────────────────────────────────────────────────────────────


class TestConstruction:
    @pytest.mark.unit
    def test_valid_construction(
        self,
        settings: Settings,
        metrics: MetricsStore,
        lifecycle: ServiceLifecycle,
        discovery_cache: DiscoveryCache,
    ) -> None:
        """HAClient constructs with valid dependencies and starts disconnected."""
        client = HAClient(settings, metrics, lifecycle, discovery_cache)
        assert client.is_connected is False

    @pytest.mark.unit
    def test_invalid_settings_raises(
        self,
        metrics: MetricsStore,
        lifecycle: ServiceLifecycle,
        discovery_cache: DiscoveryCache,
    ) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient("not settings", metrics, lifecycle, discovery_cache)  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_metrics_raises(
        self,
        settings: Settings,
        lifecycle: ServiceLifecycle,
        discovery_cache: DiscoveryCache,
    ) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, "not metrics", lifecycle, discovery_cache)  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_lifecycle_raises(
        self,
        settings: Settings,
        metrics: MetricsStore,
        discovery_cache: DiscoveryCache,
    ) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, metrics, "not lifecycle", discovery_cache)  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_discovery_cache_raises(
        self,
        settings: Settings,
        metrics: MetricsStore,
        lifecycle: ServiceLifecycle,
    ) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            HAClient(settings, metrics, lifecycle, "not a cache")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── connect() / is_connected / stop() ───────────────────────────────────────


class TestConnectAndLifecycle:
    @pytest.mark.unit
    async def test_is_connected_false_before_connect(self, ha_client: HAClient) -> None:
        assert ha_client.is_connected is False

    @pytest.mark.unit
    async def test_connect_builds_sdk_and_connects_transport(
        self,
        ha_client: HAClient,
        fake_mqtt_factory: list[Any],
    ) -> None:
        await ha_client.connect()
        assert ha_client.is_connected is True
        assert fake_mqtt_factory[0].connected is True
        await ha_client.stop()

    @pytest.mark.unit
    async def test_connect_increments_metrics(
        self,
        ha_client: HAClient,
        metrics: MetricsStore,
    ) -> None:
        await ha_client.connect()
        assert metrics.get(MetricName.MQTT_CONNECT_ATTEMPTS) == 1
        assert metrics.get(MetricName.MQTT_CONNECT_SUCCESS) == 1
        await ha_client.stop()

    @pytest.mark.unit
    async def test_connect_increments_reconnect_metrics(
        self, ha_client: HAClient, metrics: MetricsStore, monkeypatch: MonkeyPatch
    ) -> None:
        """A failed connection attempt increments the reconnect metric."""
        ha_client._retry_config.max_attempts = 2
        ha_client._retry_config.initial_delay = 0

        currently_patched_factory: type[AsyncMQTTClient] = ha_client_module.AsyncMQTTClient
        calls = {"n": 0}

        def patched_factory(config: MQTTSettings) -> AsyncMQTTClient:
            client = currently_patched_factory(config)
            calls["n"] += 1

            if calls["n"] == 1:

                async def failing_connect() -> None:
                    raise ConnectionError("first connection failed")

                client.connect = failing_connect  # type: ignore[method-assign]

            return client

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(
            ha_client_module,
            "AsyncMQTTClient",
            patched_factory,
        )

        await ha_client.connect()

        assert metrics.get(MetricName.MQTT_CONNECT_ATTEMPTS) == 1
        assert metrics.get(MetricName.MQTT_RECONNECT_ATTEMPTS) == 1
        assert metrics.get(MetricName.MQTT_CONNECT_SUCCESS) == 1
        assert metrics.get(MetricName.ERROR_MQTT) >= 1

        await ha_client.stop()

    @pytest.mark.unit
    async def test_is_connected_false_after_stop(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        await client.stop()
        assert client.is_connected is False

    @pytest.mark.unit
    async def test_stop_without_connect_is_safe(self, ha_client: HAClient) -> None:
        await ha_client.stop()
        assert ha_client.is_connected is False

    @pytest.mark.unit
    async def test_stop_twice_is_safe(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        await client.stop()
        await client.stop()
        assert client.is_connected is False

    @pytest.mark.unit
    async def test_transport_disconnected_on_stop(self, connected: tuple[HAClient, Any]) -> None:
        client, fake_mqtt = connected
        await client.stop()
        assert fake_mqtt.connected is False


# ── register_entity() ────────────────────────────────────────────────────────


class TestRegisterEntity:
    @pytest.mark.unit
    async def test_invalid_entity_raises(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.register_entity(invalid_state)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_requires_connected(self, ha_client: HAClient) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.register_entity(make_entity())
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_discovery(self, connected: tuple[HAClient, Any]) -> None:
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_discovery_test")

        await client.register_entity(entity)

        retained_topics = [t for t, _, retain in fake_mqtt.published if retain]
        assert any(entity.unique_id in t for t in retained_topics)

    @pytest.mark.unit
    async def test_updates_discovery_cache(
        self,
        connected: tuple[HAClient, Any],
        discovery_cache: DiscoveryCache,
    ) -> None:
        client, _ = connected
        entity = make_entity(unique_id="switch_cache_test")

        await client.register_entity(entity)

        assert discovery_cache.is_registered("switch_cache_test") is True

    @pytest.mark.unit
    async def test_increments_metrics(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        client, _ = connected
        entity = make_entity(unique_id="switch_metrics_test")

        await client.register_entity(entity)

        assert metrics.get(MetricName.ENTITY_REGISTERED) == 1
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) >= 1

    @pytest.mark.unit
    async def test_duplicate_registration_is_skipped(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_dup_test")
        await client.register_entity(entity)
        published_after_first = len(fake_mqtt.published)

        await client.register_entity(entity)

        assert len(fake_mqtt.published) == published_after_first
        assert metrics.get(MetricName.ENTITY_ALREADY_REGISTERED) == 1


class TestRegisterEntityCommandCallback:
    @pytest.mark.unit
    async def test_command_callback_fires_on_incoming_message(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
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
    async def test_command_callback_error_is_caught(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        """A raising callback must not propagate — only counted as an error."""
        client, fake_mqtt = connected
        entity = make_entity(unique_id="switch_cmd_err_test")

        # noinspection unused-parameter
        async def on_command(topic: str, payload: str) -> None:
            raise ValueError("boom")

        await client.register_entity(entity, command_callback=on_command)
        command_topic = fake_mqtt.subscribed[0]

        await fake_mqtt.simulate_incoming(command_topic, "ON")  # must not raise

        assert metrics.get(MetricName.ERROR_MQTT) == 1

    @pytest.mark.unit
    async def test_entity_without_callback_subscribes_nothing(
        self,
        connected: tuple[HAClient, Any],
    ) -> None:
        """
        Domains without a command topic (e.g. sensor) register cleanly
        with no callback and no subscription.
        """
        client, fake_mqtt = connected
        entity = make_entity(domain=HADomain.SENSOR, unique_id="sensor_test", name="Test Sensor")

        await client.register_entity(entity)

        assert fake_mqtt.subscribed == []


# ── update_availability() ───────────────────────────────────────────────────


class TestUpdateAvailability:
    @pytest.mark.unit
    async def test_invalid_entity_raises(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_availability(invalid_state, True)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_requires_connected(self, ha_client: HAClient) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.update_availability(make_entity(), True)
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_retained_online(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
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
    async def test_publishes_retained_offline(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
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
    async def test_unregistered_entity_raises(self, connected: tuple[HAClient, Any]) -> None:
        """The SDK requires an entity be registered before its
        availability can be published."""
        client, _ = connected
        entity = make_entity(unique_id="switch_never_registered")

        with pytest.raises(Exception):  # noqa: B017 — SDK's own EntityError type
            await client.update_availability(entity, True)


# ── set_all_offline() ────────────────────────────────────────────────────────


class TestSetAllOffline:
    @pytest.mark.unit
    async def test_marks_all_entities_offline(self, connected: tuple[HAClient, Any]) -> None:
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
    async def test_tolerates_per_entity_failure(self, connected: tuple[HAClient, Any]) -> None:
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
    async def test_invalid_entity_raises(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            client.get_state_topic(invalid_state)
            assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_resolves_topic_for_state_capable_domain(
        self,
        connected: tuple[HAClient, Any],
    ) -> None:
        client, _ = connected
        entity = make_entity(domain=HADomain.SWITCH, unique_id="switch_topic_test")

        topic = client.get_state_topic(entity)

        assert topic == "homeassistant/switch/switch_topic_test/state"

    @pytest.mark.unit
    async def test_returns_none_for_domain_without_state_topic(
        self,
        connected: tuple[HAClient, Any],
    ) -> None:
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
    async def test_requires_connected(self, ha_client: HAClient) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.update_state_direct("some/topic", "ON")
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_to_given_topic(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        client, fake_mqtt = connected

        await client.update_state_direct("dirigera/switch/foo/state", "ON")

        assert fake_mqtt.published == [("dirigera/switch/foo/state", "ON", False)]
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) == 1

    @pytest.mark.unit
    async def test_publish_failure_raises_bridge_error(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        client, fake_mqtt = connected
        fake_mqtt.fail_publish = True

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_state_direct("dirigera/switch/foo/state", "ON")

        assert exc_info.value.code == ErrorCode.MQTT_PUBLISH_FAILED
        assert metrics.get(MetricName.MQTT_PUBLISH_ERRORS) == 1


"""
Additional tests for app/ha/ha_client.py — APPEND to the end of your
existing tests/ha/test_ha_client.py. Reuses the file's own fixtures
and helpers (ha_client, connected, fake_mqtt_factory, make_entity,
FakeMQTTClient) — no new fixtures are defined here.

Targets the coverage gaps reported by `pytest --cov --cov-report=term-missing`:

    Missing: 199->exit, 204-205, 216-224, 243-244, 328, 351-357,
             386-402, 535

Needs one extra import at the top of the file:
    from app.core.lifecycle import LifecycleState
"""


# ── connect() retry-loop branches ───────────────────────────────────────────


class TestConnectRetryBehavior:
    @pytest.mark.unit
    async def test_connect_returns_when_retry_yields_no_attempts(
        self,
        ha_client: HAClient,
        fake_mqtt_factory: list[Any],
        metrics: MetricsStore,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """retry_with_backoff() yielding zero attempts must be handled
        gracefully — connect() simply returns without ever building a
        client (covers the async-for loop's zero-iteration exit)."""

        # noinspection unused-parameter
        async def empty_retry(*_args: Any, **_kwargs: Any) -> AsyncGenerator[int]:
            return
            # noinspection unreachable-code
            for _ in ():
                yield _  # unreachable by construction - makes this an async generator

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(ha_client_module, "retry_with_backoff", empty_retry)

        await ha_client.connect()

        assert ha_client.is_connected is False
        assert fake_mqtt_factory == []
        assert metrics.get(MetricName.MQTT_CONNECT_SUCCESS) == 0

    @pytest.mark.unit
    async def test_connect_aborts_when_lifecycle_is_stopping(
        self,
        ha_client: HAClient,
        lifecycle: ServiceLifecycle,
        fake_mqtt_factory: list[Any],
    ) -> None:
        """If the lifecycle is already stopping when a retry attempt
        starts, connect() must bail out instead of building a client."""
        lifecycle._state = LifecycleState.STOPPING

        await ha_client.connect()

        assert ha_client.is_connected is False
        assert fake_mqtt_factory == []

    @pytest.mark.unit
    async def test_connect_retries_after_a_failed_attempt(
        self,
        ha_client: HAClient,
        fake_mqtt_factory: list[Any],
        metrics: MetricsStore,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """The first transport connect() fails; connect() must catch
        the error, count it, and retry until a later attempt succeeds.

        Patches connect() on just the first *instance* created (rather
        than on the FakeMQTTClient class) so there's no self-binding
        involved at all — _build_and_connect() creates a fresh
        AsyncMQTTClient on every retry attempt, so instance #2 is
        untouched and behaves normally.
        """
        currently_patched_factory: type[AsyncMQTTClient] = (
            ha_client_module.AsyncMQTTClient
        )  # the fixture's _factory
        calls = {"n": 0}

        def patched_factory(config: MQTTSettings) -> AsyncMQTTClient:
            client = currently_patched_factory(config)
            calls["n"] += 1
            if calls["n"] == 1:

                async def failing_connect() -> None:
                    raise RuntimeError("simulated connect failure")

                client.connect = failing_connect  # type: ignore[method-assign]
            return client

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(ha_client_module, "AsyncMQTTClient", patched_factory)

        await ha_client.connect()

        assert ha_client.is_connected is True
        assert len(fake_mqtt_factory) == 2  # one failed build, one that succeeded
        assert metrics.get(MetricName.ERROR_MQTT) >= 1
        assert metrics.get(MetricName.MQTT_CONNECT_SUCCESS) == 1

        await ha_client.stop()


# ── stop() error handling ───────────────────────────────────────────────────


class TestStopErrorHandling:
    @pytest.mark.unit
    async def test_stop_logs_warning_when_disconnect_fails(
        self,
        connected: tuple[HAClient, Any],
        monkeypatch: MonkeyPatch,
    ) -> None:
        client, fake_mqtt = connected

        async def failing_disconnect() -> None:
            raise RuntimeError("simulated disconnect failure")

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(fake_mqtt, "disconnect", failing_disconnect)

        await client.stop()  # must not raise

        assert client.is_connected is False


# ── register_entity() error handling ────────────────────────────────────────


class TestRegisterEntityErrorHandling:
    @pytest.mark.unit
    async def test_raises_when_device_info_missing_name(
        self, connected: tuple[HAClient, Any]
    ) -> None:
        client, _ = connected
        entity = Entity(
            domain=HADomain.SWITCH,
            name="Test Switch",
            unique_id="switch_no_name_test",
            device_info=cast(
                DeviceInfo, cast(object, {"identifiers": [("my_integration", "device_XYZ")]})
            ),
        )

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.register_entity(entity)

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_wraps_unexpected_sdk_errors(
        self,
        connected: tuple[HAClient, Any],
        monkeypatch: MonkeyPatch,
    ) -> None:
        client, _ = connected
        entity = make_entity(unique_id="switch_sdk_error_test")

        async def failing_register(_entity_arg: Entity, _command_callback: Any = None) -> None:
            raise RuntimeError("sdk boom")

        assert client._sdk is not None
        monkeypatch.setattr(client._sdk, "register", failing_register)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.register_entity(entity)

        assert exc_info.value.code == ErrorCode.MQTT_REGISTRATION_FAILED


# ── update_state() ───────────────────────────────────────────────────────────


class TestUpdateState:
    @pytest.mark.unit
    async def test_invalid_unique_id_raises(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_state("", "dirigera/switch/foo/state", "ON")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_invalid_state_topic_raises(self, connected: tuple[HAClient, Any]) -> None:
        client, _ = connected
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_state("switch_1", "   ", "ON")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_requires_connected(self, ha_client: HAClient) -> None:
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await ha_client.update_state("switch_1", "dirigera/switch/foo/state", "ON")
        assert exc_info.value.code == ErrorCode.MQTT_CONNECTION_FAILED

    @pytest.mark.unit
    async def test_publishes_via_update_state_direct(
        self,
        connected: tuple[HAClient, Any],
        metrics: MetricsStore,
    ) -> None:
        client, fake_mqtt = connected

        await client.update_state("switch_1", "dirigera/switch/foo/state", "ON")

        assert fake_mqtt.published == [("dirigera/switch/foo/state", "ON", False)]
        assert metrics.get(MetricName.MQTT_MESSAGES_PUBLISHED) == 1


# ── update_availability() error handling ────────────────────────────────────


class TestUpdateAvailabilityErrorHandling:
    @pytest.mark.unit
    async def test_reraises_bridge_error_unchanged(
        self,
        connected: tuple[HAClient, Any],
        monkeypatch: MonkeyPatch,
    ) -> None:
        """If the SDK itself raises a DirigeraBridgeError (as opposed to
        some other exception type), update_availability() must
        propagate it unchanged rather than re-wrapping it."""
        client, _ = connected
        entity = make_entity(unique_id="switch_avail_reraise_test")
        await client.register_entity(entity)

        async def failing_update_availability(_entity_arg: Entity, _online: bool) -> None:
            raise DirigeraBridgeError(ErrorCode.MQTT_PUBLISH_FAILED, "boom")

        assert client._sdk is not None
        monkeypatch.setattr(client._sdk, "update_availability", failing_update_availability)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.update_availability(entity, True)

        assert exc_info.value.code == ErrorCode.MQTT_PUBLISH_FAILED
