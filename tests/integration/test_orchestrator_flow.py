"""
tests/integration/test_orchestrator_flow.py

Integration tests for app/orchestrator.py

All external dependencies (HAClient, DirigeraWebSocketClient,
DirigeraRestClient) are replaced with lightweight mock objects.
The core infrastructure (EventBus, Lifecycle, Metrics, StateCache,
DiscoveryCache) uses real objects so the event routing and state
machine logic is actually exercised.

Covers:
    - Startup sequence — all steps execute in correct order
    - Startup — lifecycle transitions CREATED → STARTING → RUNNING
    - Startup — entities registered for each device type
    - Startup — initial availability published for each entity
    - Startup — state cache primed from device attributes
    - Runtime — STATE_CHANGED event → update_state_direct() called
    - Runtime — STATE_CHANGED deduplication (unchanged value skipped)
    - Runtime — DIRIGERA_DISCONNECTED → set_all_offline() called
    - Runtime — DIRIGERA_CONNECTED → re-discovery triggered
    - Runtime — DEVICE_REMOVED → caches cleared, entities offline
    - Runtime — command callback → rest_client.send_command() called
    - Shutdown — all entities marked offline
    - Shutdown — lifecycle reaches STOPPED
    - Shutdown — ws_client.stop() and ha_client.stop() called
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.event_bus import DirigeraEvent, EventType
from app.core.lifecycle import LifecycleState
from app.mapping.command_mapper import CommandMapper
from app.mapping.device_mapper import DeviceMapper
from app.mapping.domains import make_unique_id
from app.mapping.state_mapper import StateMapper
from app.orchestrator import Orchestrator


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_entity():
    """A minimal mock Entity object."""
    entity = MagicMock()
    entity.unique_id = "dirigera_light_abc_1"
    entity.domain = MagicMock()
    entity.domain.value = "light"
    entity.name = "Test Light"
    entity.state_topic = "dirigera/light/dirigera_light_abc_1/state"
    entity.command_topic = "dirigera/light/dirigera_light_abc_1/set"
    return entity


@pytest.fixture
def mock_device_mapper(mock_entity):
    """DeviceMapper that returns one mock entity per device."""
    mapper = MagicMock(spec=DeviceMapper)
    mapper.map_device.return_value = [mock_entity]
    mapper.map_devices.return_value = [mock_entity]
    mapper.supported_device_types.return_value = ["light"]
    return mapper


@pytest.fixture
def mock_rest_client(light_raw):
    """DirigeraRestClient that returns one light device."""
    from app.dirigera.models import DirigeraDevice

    device = DirigeraDevice.model_validate(light_raw)

    client = MagicMock()
    client.get_devices = AsyncMock(return_value=[device])
    client.send_command = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def orchestrator(
    settings,
    event_bus,
    lifecycle,
    metrics,
    state_cache,
    discovery_cache,
    mock_ha_client,
    mock_ws_client,
    mock_rest_client,
    mock_device_mapper,
):
    """Fully wired Orchestrator with all external deps mocked."""
    return Orchestrator(
        settings=settings,
        event_bus=event_bus,
        lifecycle=lifecycle,
        metrics=metrics,
        state_cache=state_cache,
        discovery_cache=discovery_cache,
        ha_client=mock_ha_client,
        ws_client=mock_ws_client,
        rest_client=mock_rest_client,
        device_mapper=mock_device_mapper,
        state_mapper=StateMapper(),
        command_mapper=CommandMapper(),
    )


# ── Startup sequence ──────────────────────────────────────────────────────────


class TestOrchestratorStartup:
    @pytest.mark.integration
    async def test_startup_transitions_to_running(self, orchestrator, lifecycle):
        """Startup transitions lifecycle from CREATED to RUNNING."""
        await orchestrator._startup()
        assert lifecycle.current_state == LifecycleState.RUNNING

    @pytest.mark.integration
    async def test_startup_connects_ha_client(self, orchestrator, mock_ha_client):
        """Startup calls ha_client.connect()."""
        await orchestrator._startup()
        mock_ha_client.connect.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_connects_ws_client(self, orchestrator, mock_ws_client):
        """Startup calls ws_client.connect()."""
        await orchestrator._startup()
        mock_ws_client.connect.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_fetches_devices(self, orchestrator, mock_rest_client):
        """Startup calls rest_client.get_devices()."""
        await orchestrator._startup()
        mock_rest_client.get_devices.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_registers_entity(self, orchestrator, mock_ha_client):
        """Startup registers at least one entity."""
        await orchestrator._startup()
        mock_ha_client.register_entity.assert_awaited()

    @pytest.mark.integration
    async def test_startup_publishes_availability(self, orchestrator, mock_ha_client):
        """Startup publishes initial availability for each entity."""
        await orchestrator._startup()
        mock_ha_client.update_availability.assert_awaited()

    @pytest.mark.integration
    async def test_startup_primes_state_cache(
        self, orchestrator, state_cache, light_raw
    ):
        """Startup primes the state cache with device attributes."""
        await orchestrator._startup()
        # The light fixture has isOn=True — should be cached
        cached = state_cache.get("f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1", "isOn")
        assert cached is True

    @pytest.mark.integration
    async def test_startup_lifecycle_history(self, orchestrator, lifecycle):
        """Lifecycle history records STARTING and RUNNING transitions."""
        await orchestrator._startup()
        states = [t.to_state for t in lifecycle.history]
        assert LifecycleState.STARTING in states
        assert LifecycleState.RUNNING in states

    @pytest.mark.integration
    async def test_startup_starts_metrics_task(self, orchestrator):
        """Startup creates the metrics background task."""
        await orchestrator._startup()
        assert orchestrator._metrics_task is not None
        assert not orchestrator._metrics_task.done()
        # Clean up
        orchestrator._metrics_task.cancel()
        try:
            await orchestrator._metrics_task
        except asyncio.CancelledError:
            pass


# ── STATE_CHANGED event handling ──────────────────────────────────────────────


class TestOrchestratorStateChanged:
    @pytest.fixture(autouse=True)
    async def _startup(self, orchestrator):
        """Run startup before each test in this class."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.integration
    async def test_state_changed_calls_update_state_direct(
        self, orchestrator, event_bus, mock_ha_client, mock_entity
    ):
        """STATE_CHANGED event triggers update_state_direct()."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

        # Inject entity into orchestrator registry, keyed by the exact
        # unique_id the real StateMapper computes for this logical_id
        # (StateMapper is real here, not mocked). mock_entity.unique_id
        # is an unrelated placeholder — keying on it would mean
        # _on_state_changed's entity lookup always misses and returns
        # early before ever reaching update_state_direct().
        orchestrator._entities[make_unique_id(lid)] = mock_entity

        # Clear state cache so the value is seen as changed
        orchestrator._state_cache.clear()

        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id=lid,
            data={
                "attribute": "isOn",
                "value": False,
                "device_type": "light",
            },
        )
        await event_bus.publish(event)

        # Allow event loop to process
        await asyncio.sleep(0.05)

        mock_ha_client.update_state_direct.assert_awaited()

    @pytest.mark.integration
    async def test_state_changed_deduplication(
        self, orchestrator, event_bus, mock_ha_client, state_cache
    ):
        """Duplicate state value is not forwarded to HA."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

        # Pre-populate cache with current value
        state_cache.set(lid, "isOn", True)

        # Reset call count
        mock_ha_client.update_state_direct.reset_mock()

        # Publish same value — should be deduped
        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id=lid,
            data={
                "attribute": "isOn",
                "value": True,  # same as cached
                "device_type": "light",
            },
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        mock_ha_client.update_state_direct.assert_not_awaited()

    @pytest.mark.integration
    async def test_state_changed_internal_attribute_not_forwarded(
        self, orchestrator, event_bus, mock_ha_client, state_cache
    ):
        """Internal attributes (otaStatus etc.) are not forwarded."""
        state_cache.clear()
        mock_ha_client.update_state_direct.reset_mock()

        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id="f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1",
            data={
                "attribute": "otaStatus",
                "value": "upToDate",
                "device_type": "light",
            },
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        mock_ha_client.update_state_direct.assert_not_awaited()


# ── DIRIGERA_DISCONNECTED / CONNECTED ─────────────────────────────────────────


class TestOrchestratorConnectionEvents:
    @pytest.fixture(autouse=True)
    async def _startup(self, orchestrator):
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.integration
    async def test_dirigera_disconnected_marks_all_offline(
        self, orchestrator, event_bus, mock_ha_client
    ):
        """DIRIGERA_DISCONNECTED calls set_all_offline()."""
        mock_ha_client.set_all_offline.reset_mock()

        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_DISCONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        mock_ha_client.set_all_offline.assert_awaited()

    @pytest.mark.integration
    async def test_dirigera_disconnected_transitions_to_reconnecting(
        self, orchestrator, event_bus, lifecycle
    ):
        """DIRIGERA_DISCONNECTED transitions lifecycle to RECONNECTING."""
        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_DISCONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert lifecycle.current_state == LifecycleState.RECONNECTING

    @pytest.mark.integration
    async def test_dirigera_connected_triggers_rediscovery(
        self, orchestrator, event_bus, mock_rest_client
    ):
        """DIRIGERA_CONNECTED triggers re-discovery (get_devices called again)."""
        initial_call_count = mock_rest_client.get_devices.await_count

        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_CONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert mock_rest_client.get_devices.await_count > initial_call_count


# ── DEVICE_REMOVED ────────────────────────────────────────────────────────────


class TestOrchestratorDeviceRemoved:
    @pytest.fixture(autouse=True)
    async def _startup(self, orchestrator):
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.integration
    async def test_device_removed_clears_state_cache(
        self, orchestrator, event_bus, state_cache
    ):
        """DEVICE_REMOVED clears the device from the state cache."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"
        state_cache.set(lid, "isOn", True)

        event = DirigeraEvent(
            event_type=EventType.DEVICE_REMOVED,
            logical_id=lid,
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert state_cache.get(lid, "isOn") is None

    @pytest.mark.integration
    async def test_device_removed_unregisters_from_discovery_cache(
        self, orchestrator, event_bus, discovery_cache
    ):
        """DEVICE_REMOVED unregisters the device from discovery cache."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"

        # Register first
        discovery_cache.register(
            logical_id=lid,
            relation_id=lid,
            ha_domains=["light"],
            device_name="Test",
        )

        event = DirigeraEvent(
            event_type=EventType.DEVICE_REMOVED,
            logical_id=lid,
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert not discovery_cache.is_registered(lid)


# ── Command callback ──────────────────────────────────────────────────────────


class TestOrchestratorCommandCallback:
    @pytest.mark.integration
    async def test_command_callback_calls_send_command(
        self, orchestrator, mock_rest_client
    ):
        """Command callback for a light translates and sends to Dirigera."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"
        cb = orchestrator._make_command_callback(
            logical_id=lid,
            device_type="light",
        )

        await cb("some/topic", "ON")

        mock_rest_client.send_command.assert_awaited_once_with(
            logical_id=lid,
            attributes={"isOn": True},
        )

    @pytest.mark.integration
    async def test_command_callback_off(self, orchestrator, mock_rest_client):
        """'OFF' command callback sends isOn: False to Dirigera."""
        lid = "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1"
        cb = orchestrator._make_command_callback(
            logical_id=lid,
            device_type="light",
        )

        await cb("some/topic", "OFF")

        mock_rest_client.send_command.assert_awaited_once_with(
            logical_id=lid,
            attributes={"isOn": False},
        )

    @pytest.mark.integration
    async def test_untranslatable_command_not_sent(
        self, orchestrator, mock_rest_client
    ):
        """Untranslatable command does not call send_command."""
        cb = orchestrator._make_command_callback(
            logical_id="dev_1",
            device_type="light",
        )

        await cb("topic", "TOGGLE")

        mock_rest_client.send_command.assert_not_awaited()

    @pytest.mark.integration
    async def test_command_callback_switch(self, orchestrator, mock_rest_client):
        """Switch command callback sends correct Dirigera payload."""
        lid = "switch_1"
        cb = orchestrator._make_command_callback(
            logical_id=lid,
            device_type="switch",
        )

        await cb("topic", "ON")

        mock_rest_client.send_command.assert_awaited_once_with(
            logical_id=lid,
            attributes={"isOn": True},
        )

    @pytest.mark.integration
    async def test_command_callback_blind_open(self, orchestrator, mock_rest_client):
        """Blind OPEN command sends currentLevel: 0 (Dirigera convention)."""
        lid = "blind_1"
        cb = orchestrator._make_command_callback(
            logical_id=lid,
            device_type="blind",
        )

        await cb("topic", "OPEN")

        mock_rest_client.send_command.assert_awaited_once_with(
            logical_id=lid,
            attributes={"currentLevel": 0},
        )


# ── Shutdown sequence ─────────────────────────────────────────────────────────


class TestOrchestratorShutdown:
    @pytest.mark.integration
    async def test_shutdown_transitions_to_stopped(self, orchestrator, lifecycle):
        """Shutdown sequence reaches STOPPED state."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        await orchestrator._shutdown()
        assert lifecycle.current_state == LifecycleState.STOPPED

    @pytest.mark.integration
    async def test_shutdown_stops_ws_client(self, orchestrator, mock_ws_client):
        """Shutdown calls ws_client.stop()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        await orchestrator._shutdown()
        mock_ws_client.stop.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_stops_ha_client(self, orchestrator, mock_ha_client):
        """Shutdown calls ha_client.stop()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        await orchestrator._shutdown()
        mock_ha_client.stop.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_closes_rest_client(self, orchestrator, mock_rest_client):
        """Shutdown calls rest_client.close()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        await orchestrator._shutdown()
        mock_rest_client.close.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_marks_entities_offline(
        self, orchestrator, mock_ha_client, mock_entity
    ):
        """Shutdown marks all registered entities offline."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        # Reset so we only count shutdown calls
        mock_ha_client.set_all_offline.reset_mock()

        await orchestrator._shutdown()
        mock_ha_client.set_all_offline.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_without_prior_startup(self, orchestrator, lifecycle):
        """Shutdown without prior startup does not raise."""
        await orchestrator._shutdown()
        # Lifecycle goes straight to STOPPED from CREATED via STOPPING
        assert lifecycle.current_state in (
            LifecycleState.STOPPED,
            LifecycleState.STOPPING,
        )

    @pytest.mark.integration
    async def test_stop_request_transitions_lifecycle(self, orchestrator, lifecycle):
        """stop() transitions lifecycle to STOPPING."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            try:
                await orchestrator._metrics_task
            except asyncio.CancelledError:
                pass

        await orchestrator.stop()
        assert lifecycle.current_state == LifecycleState.STOPPING
