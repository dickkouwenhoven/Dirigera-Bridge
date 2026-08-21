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

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ha_mqtt_sdk import Entity

from app.config import Settings
from app.core.discovery_cache import DiscoveryCache
from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.event_bus import AsyncEventBus, DirigeraEvent, EventType
from app.core.lifecycle import LifecycleState, ServiceLifecycle
from app.core.metrics import MetricsStore
from app.core.state_cache import StateCache
from app.dirigera.models import DirigeraDevice
from app.mapping.command_mapper import CommandMapper
from app.mapping.device_mapper import DeviceMapper
from app.mapping.device_registry import DeviceContext
from app.mapping.domains import make_unique_id
from app.mapping.state_mapper import StateMapper
from app.orchestrator import Orchestrator

# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_entity() -> Entity:
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
def mock_device_mapper(mock_entity: Entity) -> MagicMock:
    """DeviceMapper that returns one mock entity per device."""
    mapper = MagicMock(spec=DeviceMapper)
    mapper.map_device.return_value = [mock_entity]
    mapper.map_devices.return_value = [mock_entity]
    mapper.supported_device_types.return_value = ["light"]
    return mapper


@pytest.fixture
def mock_rest_client(light_raw: dict[str, Any]) -> MagicMock:
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
    settings: Settings,
    event_bus: AsyncEventBus,
    lifecycle: ServiceLifecycle,
    metrics: MetricsStore,
    state_cache: StateCache,
    discovery_cache: DiscoveryCache,
    mock_ha_client: MagicMock,
    mock_ws_client: MagicMock,
    mock_rest_client: MagicMock,
    mock_device_mapper: MagicMock,
) -> Orchestrator:
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
    async def test_startup_transitions_to_running(
        self, orchestrator: Orchestrator, lifecycle: ServiceLifecycle
    ) -> None:
        """Startup transitions lifecycle from CREATED to RUNNING."""
        await orchestrator._startup()
        assert lifecycle.current_state == LifecycleState.RUNNING

    @pytest.mark.integration
    async def test_startup_survives_fast_websocket_connect_race(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        event_bus: AsyncEventBus,
        mock_ws_client: MagicMock,
        mock_rest_client: MagicMock,
    ) -> None:
        """
        Regression test for the real-world startup crash-loop: if the
        WebSocket connects fast enough that DIRIGERA_CONNECTED fires
        concurrently with _startup()'s own device-discovery pass (as
        happens for real on a local network), _startup() must still
        complete successfully — no duplicate registration pass, no
        crash on an invalid RUNNING -> RUNNING transition.
        """

        async def _fast_connect() -> None:
            # Simulate the real websocket client: connect() returns
            # almost immediately, then a background task fires
            # DIRIGERA_CONNECTED shortly after — often before
            # _startup()'s own device-discovery pass has finished.
            event = DirigeraEvent(event_type=EventType.DIRIGERA_CONNECTED, logical_id="")
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.ensure_future(event_bus.publish(event))
            )

        mock_ws_client.connect.side_effect = _fast_connect

        await orchestrator._startup()  # must not raise
        await asyncio.sleep(0.05)

        assert lifecycle.current_state == LifecycleState.RUNNING
        # get_devices should only have been called once — by _startup()
        # itself. The premature DIRIGERA_CONNECTED must not have
        # triggered a second, racing discovery pass.
        assert mock_rest_client.get_devices.await_count == 1

    @pytest.mark.integration
    async def test_startup_connects_ha_client(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
    ) -> None:
        """Startup calls ha_client.connect()."""
        await orchestrator._startup()
        mock_ha_client.connect.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_connects_ws_client(
        self,
        orchestrator: Orchestrator,
        mock_ws_client: MagicMock,
    ) -> None:
        """Startup calls ws_client.connect()."""
        await orchestrator._startup()
        mock_ws_client.connect.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_fetches_devices(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
        """Startup calls rest_client.get_devices()."""
        await orchestrator._startup()
        mock_rest_client.get_devices.assert_awaited_once()

    @pytest.mark.integration
    async def test_startup_registers_entity(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
    ) -> None:
        """Startup registers at least one entity."""
        await orchestrator._startup()
        mock_ha_client.register_entity.assert_awaited()

    @pytest.mark.integration
    async def test_startup_publishes_availability(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
    ) -> None:
        """Startup publishes initial availability for each entity."""
        await orchestrator._startup()
        mock_ha_client.update_availability.assert_awaited()

    @pytest.mark.integration
    async def test_startup_primes_state_cache(
        self,
        orchestrator: Orchestrator,
        state_cache: StateCache,
        light_raw: dict[str, Any],
    ) -> None:
        """Startup primes the state cache with device attributes."""
        await orchestrator._startup()
        # The light fixture has isOn=True — should be cached
        cached = state_cache.get("f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1", "isOn")
        assert cached is True

    @pytest.mark.integration
    async def test_startup_lifecycle_history(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """Lifecycle history records STARTING and RUNNING transitions."""
        await orchestrator._startup()
        states = [t.to_state for t in lifecycle.history]
        assert LifecycleState.STARTING in states
        assert LifecycleState.RUNNING in states

    @pytest.mark.integration
    async def test_startup_starts_metrics_task(self, orchestrator: Orchestrator) -> None:
        """Startup creates the metrics background task."""
        await orchestrator._startup()
        assert orchestrator._metrics_task is not None
        assert not orchestrator._metrics_task.done()
        # Clean up
        orchestrator._metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await orchestrator._metrics_task


# ── STATE_CHANGED event handling ──────────────────────────────────────────────


class TestOrchestratorStateChanged:
    @pytest.fixture(autouse=True)
    async def _startup(self, orchestrator: Orchestrator) -> None:
        """Run startup before each test in this class."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

    @pytest.mark.integration
    async def test_state_changed_calls_update_state_direct(
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        mock_ha_client: MagicMock,
        mock_entity: Entity,
    ) -> None:
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
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        mock_ha_client: MagicMock,
        state_cache: StateCache,
    ) -> None:
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
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        mock_ha_client: MagicMock,
        state_cache: StateCache,
    ) -> None:
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
    async def _startup(self, orchestrator: Orchestrator) -> None:
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

    @pytest.mark.integration
    async def test_dirigera_disconnected_marks_all_offline(
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        mock_ha_client: MagicMock,
    ) -> None:
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
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """DIRIGERA_DISCONNECTED transitions lifecycle to RECONNECTING."""
        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_DISCONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert lifecycle.current_state == LifecycleState.RECONNECTING

    @pytest.mark.integration
    async def test_dirigera_connected_triggers_rediscovery_after_outage(
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        mock_rest_client: MagicMock,
    ) -> None:
        """
        DIRIGERA_CONNECTED triggers re-discovery when recovering from a
        real outage (lifecycle was RECONNECTING).
        """
        # Simulate a real prior disconnect
        await lifecycle.transition(LifecycleState.RECONNECTING, reason="test: simulate disconnect")

        initial_call_count = mock_rest_client.get_devices.await_count

        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_CONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert mock_rest_client.get_devices.await_count > initial_call_count
        assert lifecycle.current_state == LifecycleState.RUNNING

    @pytest.mark.integration
    async def test_dirigera_connected_is_noop_when_not_reconnecting(
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        mock_rest_client: MagicMock,
    ) -> None:
        """
        Regression test: DIRIGERA_CONNECTED must NOT trigger re-discovery
        when the lifecycle is already RUNNING (e.g. the initial
        connection during startup) — _startup() already owns that path
        via its own sequential flow. Reacting here too previously raced
        _startup()'s own registration pass and crashed the service with
        an invalid RUNNING -> RUNNING transition.
        """
        assert lifecycle.current_state == LifecycleState.RUNNING
        initial_call_count = mock_rest_client.get_devices.await_count

        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_CONNECTED,
            logical_id="",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)

        assert mock_rest_client.get_devices.await_count == initial_call_count
        assert lifecycle.current_state == LifecycleState.RUNNING


# ── DEVICE_REMOVED ────────────────────────────────────────────────────────────


class TestOrchestratorDeviceRemoved:
    @pytest.fixture(autouse=True)
    async def _startup(self, orchestrator: Orchestrator) -> None:
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

    @pytest.mark.integration
    async def test_device_removed_clears_state_cache(
        self,
        orchestrator: Orchestrator,
        event_bus: AsyncEventBus,
        state_cache: StateCache,
    ) -> None:
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
        self, orchestrator: Orchestrator, event_bus: AsyncEventBus, discovery_cache: DiscoveryCache
    ) -> None:
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
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
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
    async def test_command_callback_off(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
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
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
        """Untranslatable command does not call send_command."""
        cb = orchestrator._make_command_callback(
            logical_id="dev_1",
            device_type="light",
        )

        await cb("topic", "TOGGLE")

        mock_rest_client.send_command.assert_not_awaited()

    @pytest.mark.integration
    async def test_command_callback_switch(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
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
    async def test_command_callback_blind_open(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
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
    async def test_shutdown_transitions_to_stopped(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """Shutdown sequence reaches STOPPED state."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        await orchestrator._shutdown()
        assert lifecycle.current_state == LifecycleState.STOPPED

    @pytest.mark.integration
    async def test_shutdown_stops_ws_client(
        self,
        orchestrator: Orchestrator,
        mock_ws_client: MagicMock,
    ) -> None:
        """Shutdown calls ws_client.stop()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        await orchestrator._shutdown()
        mock_ws_client.stop.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_stops_ha_client(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
    ) -> None:
        """Shutdown calls ha_client.stop()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        await orchestrator._shutdown()
        mock_ha_client.stop.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_closes_rest_client(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
        """Shutdown calls rest_client.close()."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        await orchestrator._shutdown()
        mock_rest_client.close.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_marks_entities_offline(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
        mock_entity: Entity,
    ) -> None:
        """Shutdown marks all registered entities offline."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        # Reset so we only count shutdown calls
        mock_ha_client.set_all_offline.reset_mock()

        await orchestrator._shutdown()
        mock_ha_client.set_all_offline.assert_awaited()

    @pytest.mark.integration
    async def test_shutdown_without_prior_startup(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """Shutdown without prior startup does not raise."""
        await orchestrator._shutdown()
        # Lifecycle goes straight to STOPPED from CREATED via STOPPING
        assert lifecycle.current_state in (
            LifecycleState.STOPPED,
            LifecycleState.STOPPING,
        )

    @pytest.mark.integration
    async def test_stop_request_transitions_lifecycle(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """stop() transitions lifecycle to STOPPING."""
        await orchestrator._startup()
        if orchestrator._metrics_task:
            orchestrator._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orchestrator._metrics_task

        await orchestrator.stop()
        assert lifecycle.current_state == LifecycleState.STOPPING


class TestOrchestratorUncoveredPaths:
    @pytest.mark.integration
    async def test_run_shuts_down_after_startup_error(self, orchestrator: Orchestrator) -> None:
        with (
            patch.object(
                orchestrator,
                "_startup",
                new_callable=AsyncMock,
                side_effect=DirigeraBridgeError(
                    ErrorCode.LIFECYCLE_STARTUP_FAILED,
                    "failed",
                ),
            ),
            patch.object(
                orchestrator,
                "_shutdown",
                new_callable=AsyncMock,
            ) as shutdown,
        ):
            await orchestrator.run()

            shutdown.assert_awaited_once()

    @pytest.mark.integration
    async def test_discovery_wraps_rest_error(
        self, orchestrator: Orchestrator, mock_rest_client: MagicMock
    ) -> None:
        mock_rest_client.get_devices.side_effect = DirigeraBridgeError(
            ErrorCode.REST_REQUEST_FAILED, "offline"
        )

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await orchestrator._discover_and_register_devices()

        assert exc_info.value.code == ErrorCode.LIFECYCLE_STARTUP_FAILED

    @pytest.mark.integration
    async def test_register_context_skips_empty_entity_list(
        self, orchestrator: Orchestrator, mock_device_mapper: MagicMock
    ) -> None:
        from app.mapping.device_registry import DeviceContext

        mock_device_mapper.map_device.return_value = []
        context = MagicMock(spec=DeviceContext, device_name="No entities", device_type="unknown")
        await orchestrator._register_context(context)
        assert orchestrator._entities == {}

    @pytest.mark.integration
    async def test_state_changed_routes_reachability(
        self,
        orchestrator: Orchestrator,
    ) -> None:
        with patch.object(
            orchestrator,
            "_on_device_reachable",
            new_callable=AsyncMock,
        ) as on_device_reachable:
            event = DirigeraEvent(
                EventType.STATE_CHANGED,
                "device_1",
                data={"attribute": "isReachable", "value": True},
            )

            await orchestrator._on_state_changed(event)

            on_device_reachable.assert_awaited_once_with(event)

    @pytest.mark.integration
    async def test_discovery_event_swallows_rediscovery_failure(
        self,
        orchestrator: Orchestrator,
    ) -> None:
        with patch.object(
            orchestrator,
            "_discover_and_register_devices",
            new_callable=AsyncMock,
            side_effect=DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                "offline",
            ),
        ):
            await orchestrator._on_device_discovered(
                DirigeraEvent(EventType.DEVICE_DISCOVERED, "device_1")
            )

    @pytest.mark.integration
    async def test_unreachable_updates_availability_and_reachability_state(
        self,
        orchestrator: Orchestrator,
    ) -> None:
        with (
            patch.object(
                orchestrator,
                "_set_device_availability",
                new_callable=AsyncMock,
            ) as set_device_availability,
            patch.object(
                orchestrator,
                attribute="_process_attribute_change",
                new_callable=AsyncMock,
            ),
        ):
            event = DirigeraEvent(
                EventType.DEVICE_UNREACHABLE,
                "device_1",
                data={"device_type": "light"},
            )

            await orchestrator._on_device_unreachable(event)
            set_device_availability.assert_awaited_once_with(
                "device_1",
                online=False,
            )
            assert orchestrator._last_reachable["device_1"] is False

    @pytest.mark.integration
    async def test_reachable_refreshes_once_and_handles_rest_failure(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
        with (
            patch.object(
                orchestrator,
                attribute="_set_device_availability",
                new_callable=AsyncMock,
            ),
            patch.object(
                orchestrator,
                attribute="_process_attribute_change",
                new_callable=AsyncMock,
            ),
        ):
            mock_rest_client.get_device = AsyncMock(
                side_effect=DirigeraBridgeError(
                    ErrorCode.REST_REQUEST_FAILED,
                    message="offline",
                )
            )

            event = DirigeraEvent(
                EventType.DEVICE_REACHABLE,
                "device_1",
                data={"device_type": "light"},
            )

            await orchestrator._on_device_reachable(event)
            await orchestrator._on_device_reachable(event)

            mock_rest_client.get_device.assert_awaited_once_with("device_1")

    @pytest.mark.integration
    async def test_command_callback_handles_rest_error(
        self,
        orchestrator: Orchestrator,
        mock_rest_client: MagicMock,
    ) -> None:
        command = MagicMock(
            logical_id="device_1",
            attributes={"isOn": True},
        )
        with patch.object(
            orchestrator._command_mapper,
            "map_command",
            return_value=command,
        ):
            mock_rest_client.send_command.side_effect = DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                message="offline",
            )

            await orchestrator._make_command_callback(logical_id="device_1", device_type="light")(
                "topic",
                "{}",
            )

    @pytest.mark.integration
    async def test_process_state_handles_missing_topic_and_publish_error(
        self,
        orchestrator: Orchestrator,
        mock_ha_client: MagicMock,
    ) -> None:
        entity = MagicMock()
        orchestrator._entities["dirigera_device_1"] = entity
        mapped_state = MagicMock(
            unique_id="dirigera_device_1",
            payload="ON",
        )

        with patch.object(
            orchestrator._state_mapper,
            "map_state",
            return_value=mapped_state,
        ):
            mock_ha_client.get_state_topic.return_value = None

            await orchestrator._process_attribute_change(
                logical_id="device_1",
                device_type="light",
                attribute="isOn",
                value=True,
            )

            mock_ha_client.get_state_topic.return_value = "state/topic"
            mock_ha_client.update_state_direct.side_effect = DirigeraBridgeError(
                ErrorCode.MQTT_PUBLISH_FAILED,
                message="offline",
            )

            await orchestrator._process_attribute_change(
                logical_id="device_1",
                device_type="light",
                attribute="isOn",
                value=False,
            )


"""
Coverage tests for app/orchestrator.py — targets the gaps reported by
`pytest --cov --cov-report=term-missing`:

    Missing: 183, 186, 190->197, 209->exit, 259, 353-354, 481, 531-534,
             595->601, 603-604, 620->626, 706-719, 742->exit, 745-753,
             765-766, 781->788, 789-791, 804-805, 810-811, 816-817,
             824->830, 905, 918-921

Deliberately defines NO fixtures of its own — it only consumes the ones
already in conftest.py (`orchestrator`, `lifecycle`, `state_cache`,
`discovery_cache`, `mock_ha_client`, `mock_ws_client`, `mock_rest_client`,
`mock_device_mapper`), so paste this straight into test_orchestrator_flow.py.

STRATEGY: `lifecycle` is a real ServiceLifecycle, so most branches are
driven by putting the real state machine into the state that naturally
produces them (e.g. RECONNECTING is only valid from RUNNING, so a fresh
CREATED lifecycle naturally exercises the "transition not allowed"
branch in _on_dirigera_disconnected — no mocking needed). For the small
number of branches the transition table can't reach on its own (e.g.
"can_transition(RUNNING) returned False mid-startup", which never
happens in valid real usage), a single method on the real `lifecycle`
instance is monkeypatched while everything else about it stays real.

ASSUMPTIONS — please flag if these don't match your actual code:
    - DeviceContext exposes: logical_id, device_type, device_name, is_reachable
    - DirigeraEvent exposes:  logical_id, data (a dict)
    - DirigeraDevice exposes: id, device_type, raw_attributes, is_reachable
    - StateCache.get_device_state(logical_id) returns a dict of the
      cached attribute -> value pairs for that device
    - Directly assigning `lifecycle._state = LifecycleState.X` is
      acceptable white-box test setup (bypasses transition() validation
      on purpose, to seed a starting state that would otherwise take
      several valid hops to reach)
"""


# ──────────────────────────────────────────────────────────────────────────
# Local helpers (no fixtures — just plain constructors)
# ──────────────────────────────────────────────────────────────────────────


def _make_event(logical_id: str, data: dict[str, Any] | None = None) -> DirigeraEvent:
    return cast(
        DirigeraEvent, cast(object, SimpleNamespace(logical_id=logical_id, data=data or {}))
    )


def _make_device_context(
    logical_id: str = "fff75d00_1",
    device_type: str = "light",
    device_name: str = "Test Light",
    is_reachable: bool = True,
) -> DeviceContext:
    return cast(
        DeviceContext,
        cast(
            object,
            SimpleNamespace(
                logical_id=logical_id,
                device_type=device_type,
                device_name=device_name,
                is_reachable=is_reachable,
            ),
        ),
    )


def _make_dirigera_device(
    device_id: str = "fff75d00_1",
    device_type: str = "light",
    raw_attributes: dict[str, Any] | None = None,
    is_reachable: bool = True,
) -> DirigeraDevice:
    return cast(
        DirigeraDevice,
        cast(
            object,
            SimpleNamespace(
                id=device_id,
                device_type=device_type,
                raw_attributes=raw_attributes or {},
                is_reachable=is_reachable,
            ),
        ),
    )


def _override_can_transition_for(
    lifecycle: ServiceLifecycle,
    monkeypatch: pytest.MonkeyPatch,
    forced_false_for: LifecycleState,
) -> None:
    """Force can_transition(forced_false_for) to return False while every
    other call is delegated to the real (still fully valid) implementation.
    """
    real_can_transition = lifecycle.can_transition

    def fake_can_transition(to_state: LifecycleState) -> bool:
        if to_state == forced_false_for:
            return False
        return real_can_transition(to_state)

    monkeypatch.setattr(lifecycle, "can_transition", fake_can_transition)


# ──────────────────────────────────────────────────────────────────────────
# run()  — lines 183, 186, 190->197
# ──────────────────────────────────────────────────────────────────────────


class TestRun:
    async def test_run_full_happy_path_reaches_run_until_stopped(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        mock_ha_client: MagicMock,
        mock_ws_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Real CREATED -> STARTING -> RUNNING happens naturally. Force
        # is_terminal() True so _run_until_stopped() doesn't spin forever
        # waiting for a STOPPED state that only _shutdown() (not yet run)
        # would set.
        monkeypatch.setattr(lifecycle, "is_terminal", lambda: True)

        await orchestrator.run()

        mock_ha_client.connect.assert_awaited_once()
        mock_ws_client.connect.assert_awaited_once()
        assert LifecycleState.RUNNING in [t.to_state for t in lifecycle.history]

    async def test_run_handles_cancelled_error(
        self, orchestrator: Orchestrator, mock_ha_client: MagicMock
    ) -> None:
        mock_ha_client.connect = AsyncMock(side_effect=asyncio.CancelledError())

        await orchestrator.run()  # must not propagate

        mock_ha_client.stop.assert_awaited_once()

    async def test_run_skips_failed_transition_when_already_terminal(
        self, orchestrator: Orchestrator, lifecycle: ServiceLifecycle
    ) -> None:
        # STOPPED is terminal: transition(STARTING) at step 1 of _startup()
        # raises DirigeraBridgeError(LIFECYCLE_INVALID_TRANSITION), which
        # run() catches; can_transition(FAILED) is then naturally False
        # because STOPPED has no valid outgoing transitions at all.
        lifecycle._state = LifecycleState.STOPPED

        await orchestrator.run()  # must not raise

        assert lifecycle.current_state == LifecycleState.STOPPED


# ──────────────────────────────────────────────────────────────────────────
# stop()  — line 209->exit
# ──────────────────────────────────────────────────────────────────────────


class TestStop:
    async def test_stop_skips_transition_when_already_terminal(
        self, orchestrator: Orchestrator, lifecycle: ServiceLifecycle
    ) -> None:
        lifecycle._state = LifecycleState.STOPPED

        await orchestrator.stop()

        assert lifecycle.current_state == LifecycleState.STOPPED

    async def test_stop_transitions_when_allowed(
        self, orchestrator: Orchestrator, lifecycle: ServiceLifecycle
    ) -> None:
        await orchestrator.stop()

        assert lifecycle.current_state == LifecycleState.STOPPING


# ──────────────────────────────────────────────────────────────────────────
# _startup()  — line 259
# ──────────────────────────────────────────────────────────────────────────


class TestStartup:
    async def test_startup_logs_warning_when_cannot_transition_to_running(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # In real usage this branch is unreachable (STARTING->RUNNING is
        # always valid), so we force just this one check to exercise the
        # defensive warning path; everything else about the lifecycle
        # (including the STOPPING/STOPPED transitions during shutdown)
        # stays real.
        _override_can_transition_for(lifecycle, monkeypatch, LifecycleState.RUNNING)
        monkeypatch.setattr(lifecycle, "is_terminal", lambda: True)

        await orchestrator.run()

        assert LifecycleState.RUNNING not in [t.to_state for t in lifecycle.history]


# ──────────────────────────────────────────────────────────────────────────
# _register_context()  — lines 353-354
# ──────────────────────────────────────────────────────────────────────────


class TestRegisterContext:
    async def test_logs_error_when_registration_fails(
        self,
        orchestrator: Orchestrator,
        mock_device_mapper: MagicMock,
        mock_ha_client: MagicMock,
    ) -> None:
        entity = MagicMock()
        entity.unique_id = "dirigera_fff75d00_1"
        mock_device_mapper.map_device = MagicMock(return_value=[entity])
        mock_ha_client.register_entity = AsyncMock(
            side_effect=DirigeraBridgeError(ErrorCode.LIFECYCLE_STARTUP_FAILED, "boom")
        )

        await orchestrator._register_context(_make_device_context())  # must not raise

        mock_ha_client.register_entity.assert_awaited_once()
        assert entity.unique_id not in orchestrator._entities


# ──────────────────────────────────────────────────────────────────────────
# _on_state_changed()  — line 481
# ──────────────────────────────────────────────────────────────────────────


class TestOnStateChanged:
    async def test_routes_to_unreachable_handler_when_value_false(
        self, orchestrator: Orchestrator
    ) -> None:
        event = _make_event(
            logical_id="fff75d00_1",
            data={"attribute": "isReachable", "value": False, "device_type": "light"},
        )

        await orchestrator._on_state_changed(event)

        assert orchestrator._last_reachable["fff75d00_1"] is False


# ──────────────────────────────────────────────────────────────────────────
# _on_device_removed()  — lines 531-534
# ──────────────────────────────────────────────────────────────────────────


class TestOnDeviceRemoved:
    async def test_logs_warning_when_availability_update_fails(
        self, orchestrator: Orchestrator, mock_ha_client: MagicMock
    ) -> None:
        entity = MagicMock()
        orchestrator._entities["dirigera_fff75d00_1"] = entity
        mock_ha_client.update_availability = AsyncMock(
            side_effect=DirigeraBridgeError(ErrorCode.LIFECYCLE_STARTUP_FAILED, "boom")
        )

        event = _make_event(logical_id="fff75d00_1")

        await orchestrator._on_device_removed(event)  # must not raise

        mock_ha_client.update_availability.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────────
# _on_dirigera_connected()  — lines 595->601, 603-604
# ──────────────────────────────────────────────────────────────────────────


class TestOnDirigeraConnected:
    async def test_reconnect_skips_transition_and_logs_rediscovery_failure(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        mock_rest_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # RECONNECTING -> RUNNING is normally valid, so we seed the state
        # directly and then force just the RUNNING check too False to
        # exercise the "skip transition" branch specifically.
        lifecycle._state = LifecycleState.RECONNECTING
        _override_can_transition_for(lifecycle, monkeypatch, LifecycleState.RUNNING)
        mock_rest_client.get_devices = AsyncMock(
            side_effect=DirigeraBridgeError(ErrorCode.LIFECYCLE_STARTUP_FAILED, "boom")
        )

        event = _make_event(logical_id="irrelevant")

        await orchestrator._on_dirigera_connected(event)  # must not raise

        assert LifecycleState.RUNNING not in [t.to_state for t in lifecycle.history]


# ──────────────────────────────────────────────────────────────────────────
# _on_dirigera_disconnected()  — line 620->626
# ──────────────────────────────────────────────────────────────────────────


class TestOnDirigeraDisconnected:
    async def test_skips_transition_when_not_allowed(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        mock_ha_client: MagicMock,
    ) -> None:
        # Fresh lifecycle is CREATED; RECONNECTING is only ever valid from
        # RUNNING, so this naturally exercises the "skip" branch with no
        # mocking required.
        event = _make_event(logical_id="irrelevant")

        await orchestrator._on_dirigera_disconnected(event)

        assert lifecycle.current_state == LifecycleState.CREATED
        mock_ha_client.set_all_offline.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────────
# _set_device_availability()  — lines 706-719
# ──────────────────────────────────────────────────────────────────────────


class TestSetDeviceAvailability:
    async def test_updates_matching_entities_and_skips_others(
        self, orchestrator: Orchestrator, mock_ha_client: MagicMock
    ) -> None:
        matching_entity = MagicMock()
        other_entity = MagicMock()
        orchestrator._entities["dirigera_fff75d00_1_battery"] = matching_entity
        orchestrator._entities["dirigera_other_device_1"] = other_entity

        await orchestrator._set_device_availability("fff75d00-1", online=True)

        mock_ha_client.update_availability.assert_awaited_once_with(matching_entity, online=True)

    async def test_logs_warning_when_availability_update_fails(
        self, orchestrator: Orchestrator, mock_ha_client: MagicMock
    ) -> None:
        entity = MagicMock()
        orchestrator._entities["dirigera_fff75d00_1_battery"] = entity
        mock_ha_client.update_availability = AsyncMock(
            side_effect=DirigeraBridgeError(ErrorCode.LIFECYCLE_STARTUP_FAILED, "boom")
        )

        await orchestrator._set_device_availability("fff75d00-1", online=False)  # no raise

        mock_ha_client.update_availability.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────────
# _metrics_loop()  — lines 742->exit, 745-753
# ──────────────────────────────────────────────────────────────────────────


class TestMetricsLoop:
    async def test_exits_immediately_when_already_stopping(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(lifecycle, "is_stopping", lambda: True)

        await orchestrator._metrics_loop()  # returns without sleeping

    async def test_breaks_after_sleep_when_stopping_flips_true(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(lifecycle, "is_stopping", MagicMock(side_effect=[False, True]))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("app.orchestrator.asyncio.sleep", sleep_mock)

        await orchestrator._metrics_loop()

        sleep_mock.assert_awaited_once()

    async def test_logs_snapshot_when_still_running_after_sleep(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(lifecycle, "is_stopping", MagicMock(side_effect=[False, False, True]))
        monkeypatch.setattr("app.orchestrator.asyncio.sleep", AsyncMock())

        await orchestrator._metrics_loop()  # must complete without hanging


# ──────────────────────────────────────────────────────────────────────────
# _run_until_stopped()  — lines 765-766
# ──────────────────────────────────────────────────────────────────────────


class TestRunUntilStopped:
    async def test_returns_immediately_when_already_terminal(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(lifecycle, "is_terminal", lambda: True)

        await orchestrator._run_until_stopped()  # must return without sleeping

    async def test_polls_until_terminal(
        self,
        orchestrator: Orchestrator,
        lifecycle: ServiceLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(lifecycle, "is_terminal", MagicMock(side_effect=[False, True]))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("app.orchestrator.asyncio.sleep", sleep_mock)

        await orchestrator._run_until_stopped()

        sleep_mock.assert_awaited_once_with(1)


# ──────────────────────────────────────────────────────────────────────────
# _shutdown()  — lines 781->788, 789-791, 804-805, 810-811, 816-817, 824->830
# ──────────────────────────────────────────────────────────────────────────


class TestShutdown:
    async def test_skips_transitions_when_already_terminal(
        self, orchestrator: Orchestrator, lifecycle: ServiceLifecycle
    ) -> None:
        # STOPPED is terminal, so both the STOPPING and (later) STOPPED
        # can_transition() checks are naturally False here — covers both
        # 781->788 and 824->830 in one pass.
        lifecycle._state = LifecycleState.STOPPED

        await orchestrator._shutdown()

        assert lifecycle.current_state == LifecycleState.STOPPED

    async def test_cancels_pending_metrics_task(self, orchestrator: Orchestrator) -> None:
        async def _never_ending() -> None:
            await asyncio.sleep(3600)

        task: asyncio.Task[None] = asyncio.create_task(_never_ending())
        orchestrator._metrics_task = task

        await orchestrator._shutdown()

        assert task.cancelled() or task.done()

    async def test_logs_warnings_when_clients_fail_to_stop(
        self,
        orchestrator: Orchestrator,
        mock_ws_client: MagicMock,
        mock_ha_client: MagicMock,
        mock_rest_client: MagicMock,
    ) -> None:
        mock_ws_client.stop = AsyncMock(side_effect=RuntimeError("ws boom"))
        mock_ha_client.stop = AsyncMock(side_effect=RuntimeError("ha boom"))
        mock_rest_client.close = AsyncMock(side_effect=RuntimeError("rest boom"))

        await orchestrator._shutdown()  # must not raise despite all three failing

        mock_ws_client.stop.assert_awaited_once()
        mock_ha_client.stop.assert_awaited_once()
        mock_rest_client.close.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────────
# _on_device_reachable()  — lines 905, 918-921
# ──────────────────────────────────────────────────────────────────────────


class TestOnDeviceReachable:
    async def test_refreshes_state_from_rest_when_newly_online(
        self, orchestrator: Orchestrator, mock_rest_client: MagicMock
    ) -> None:
        device = _make_dirigera_device(
            device_id="fff75d00_1",
            device_type="light",
            raw_attributes={"isOn": True, "lightLevel": 80},
        )
        mock_rest_client.get_device = AsyncMock(return_value=device)

        event = _make_event(logical_id="fff75d00_1", data={"device_type": "light"})

        await orchestrator._on_device_reachable(event)

        mock_rest_client.get_device.assert_awaited_once_with("fff75d00_1")
        assert orchestrator._last_reachable["fff75d00_1"] is True
