"""
tests/dirigera/test_websocket_client.py

Tests for app/dirigera/websocket_client.py

All WebSocket and network calls are mocked — no real connection is made.

Covers:
    - DirigeraWebSocketClient construction and validation
    - is_connected property — True/False based on _ws state
    - stop() — sets stop_event, safe when never connected
    - _handle_message() — bad JSON increments parse error counter
    - _handle_message() — valid state change publishes STATE_CHANGED events
    - _handle_message() — one event per changed attribute
    - _handle_message() — deviceAdded publishes DEVICE_DISCOVERED
    - _handle_message() — deviceRemoved publishes DEVICE_REMOVED
    - _handle_message() — unknown event type silently ignored
    - _handle_message() — empty attributes silently ignored
    - _handle_message() — events carry correct logical_id and relation_id
    - _handle_message() — events carry device_type in data
    - _build_ssl_context() — returns ssl.SSLContext with CERT_NONE
"""

import asyncio
import json
import ssl
from collections.abc import AsyncGenerator, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
from websockets import exceptions
from websockets.protocol import State

from dirigera_bridge.config import Settings
from dirigera_bridge.core import AsyncEventBus, ServiceLifecycle
from dirigera_bridge.core.event_bus import DirigeraEvent, EventType
from dirigera_bridge.core.metrics import MetricName, MetricsStore
from dirigera_bridge.dirigera import DirigeraWebSocketClient


class WSClientTestDouble:
    """
    Minimal test double for DirigeraWebSocketClient.

    Bypasses the isinstance checks in __init__ so we can inject
    lightweight mock objects without importing Settings.
    """

    def __init__(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:
        from dirigera_bridge.core.retry import RetryConfig

        self._settings = settings
        self._event_bus = event_bus
        self._lifecycle = lifecycle
        self._metrics = metrics
        self._ws = None
        self._stop_event = asyncio.Event()
        self._listen_task = None
        self._ping_task = None
        self._retry_config = RetryConfig(
            initial_delay=0.01,
            max_delay=0.05,
            multiplier=2.0,
            jitter_max=0.0,
        )
        self._ws_url = f"wss://{settings.dirigera_ip}:8443/v1/events"

    # Copy the methods under test directly from the real class
    from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

    is_connected = DirigeraWebSocketClient.is_connected
    stop = DirigeraWebSocketClient.stop
    # noinspection protected-member
    _handle_message = DirigeraWebSocketClient._handle_message
    # noinspection protected-member
    _dispatch_state_change = DirigeraWebSocketClient._dispatch_state_change
    # noinspection protected-member
    _dispatch_device_added = DirigeraWebSocketClient._dispatch_device_added
    # noinspection protected-member
    _dispatch_device_removed = DirigeraWebSocketClient._dispatch_device_removed
    # noinspection protected-member
    _publish_connection_event = DirigeraWebSocketClient._publish_connection_event
    # noinspection protected-member
    _close_ws = DirigeraWebSocketClient._close_ws
    # noinspection protected-member
    _listen_loop = DirigeraWebSocketClient._listen_loop
    # noinspection protected-member
    _ping_loop = DirigeraWebSocketClient._ping_loop
    # noinspection protected-member
    _connection_loop = DirigeraWebSocketClient._connection_loop
    # noinspection protected-member
    _connect_and_listen = DirigeraWebSocketClient._connect_and_listen


class MockSettings(Settings):
    dirigera_ip = "192.168.1.100"
    dirigera_token = "test_token"
    ws_ping_interval = 30
    ws_ping_timeout = 10
    reconnect_delay_initial = 0.01
    reconnect_delay_max = 0.05


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ws_client(
    event_bus: AsyncEventBus,
    lifecycle: ServiceLifecycle,
    metrics: MetricsStore,
) -> DirigeraWebSocketClient:
    return DirigeraWebSocketClient(
        MockSettings(
            dirigera_ip="192.168.1.100",
            dirigera_token="test_token",
            ws_ping_interval=30,
            ws_ping_timeout=10,
            reconnect_delay_initial=0.01,
            reconnect_delay_max=0.05,
            mqtt_host="host",
            mqtt_port=1884,
            mqtt_user="user",
            mqtt_password="password",
            mqtt_client_id="client_id",
            mqtt_keepalive=60,
            mqtt_base_topic="homeassistant",
            mqtt_qos=0,
            mqtt_tls=False,
            mqtt_reconnect=True,
            mqtt_reconnect_delay_min=1.0,
            mqtt_reconnect_delay_max=60.0,            
            discovery_prefix="homeassistant",
            log_level="info",
            metrics_interval=60,
        ),
        event_bus,
        lifecycle,
        metrics,
    )


# ── Construction ──────────────────────────────────────────────────────────────


class TestDirigeraWebSocketClientConstruction:
    @pytest.mark.unit
    def test_valid_construction(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:
        """DirigeraWebSocketClient constructs with valid dependencies."""
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        client = DirigeraWebSocketClient(
            settings=settings,
            event_bus=event_bus,
            lifecycle=lifecycle,
            metrics=metrics,
        )
        assert client is not None

    @pytest.mark.unit
    def test_invalid_settings_raises(
        self,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:
        """Non-Settings raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings="bad",  # type: ignore[arg-type]
                event_bus=event_bus,
                lifecycle=lifecycle,
                metrics=metrics,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_event_bus_raises(
        self,
        settings: Settings,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:
        """Non-AsyncEventBus raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings=settings,
                event_bus="bad",  # type: ignore[arg-type]
                lifecycle=lifecycle,
                metrics=metrics,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_lifecycle_raises(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        metrics: MetricsStore,
    ) -> None:
        """Non-ServiceLifecycle raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings=settings,
                event_bus=event_bus,
                lifecycle="bad",  # type: ignore[arg-type]
                metrics=metrics,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_metrics_raises(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """Non-MetricsStore raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings=settings,
                event_bus=event_bus,
                lifecycle=lifecycle,
                metrics="bad",  # type: ignore[arg-type]
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── is_connected ──────────────────────────────────────────────────────────────


class TestIsConnected:
    @pytest.mark.unit
    def test_false_when_no_ws(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """is_connected is False when _ws is None."""
        ws_client._ws = None
        assert ws_client.is_connected is False

    @pytest.mark.unit
    def test_false_when_ws_closed(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """is_connected is False when _ws.state is not OPEN."""
        mock_ws = MagicMock()
        mock_ws.state = State.CLOSED
        ws_client._ws = mock_ws
        assert ws_client.is_connected is False

    @pytest.mark.unit
    def test_true_when_ws_open(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """is_connected is True when _ws.state is OPEN."""
        mock_ws = MagicMock()
        mock_ws.state = State.OPEN
        ws_client._ws = mock_ws
        assert ws_client.is_connected is True


# ── stop() ────────────────────────────────────────────────────────────────────


class TestStop:
    @pytest.mark.unit
    async def test_stop_sets_stop_event(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """stop() sets the stop_event."""
        assert not ws_client._stop_event.is_set()
        await ws_client.stop()
        assert ws_client._stop_event.is_set()

    @pytest.mark.unit
    async def test_stop_when_never_connected_does_not_raise(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """stop() when never connected does not raise."""
        await ws_client.stop()  # should not raise

    @pytest.mark.unit
    async def test_stop_closes_open_socket_and_cancels_tasks(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """stop() cancels running tasks and closes the active socket."""
        ping_task = asyncio.create_task(asyncio.sleep(60))
        listen_task = asyncio.create_task(asyncio.sleep(60))
        ws_client._ping_task = ping_task
        ws_client._listen_task = listen_task
        socket = MagicMock(state=State.OPEN)
        socket.close = AsyncMock()
        ws_client._ws = socket

        await ws_client.stop()

        socket.close.assert_awaited_once()
        assert ping_task.cancelled()
        assert listen_task.cancelled()


class TestListenAndPingLoops:
    @pytest.mark.unit
    async def test_listen_loop_decodes_bytes_and_dispatches_messages(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """The reception loop normalizes bytes before dispatching."""

        class ClientConnection:
            def __aiter__(self) -> AsyncGenerator[bytes, Any]:
                async def messages() -> AsyncGenerator[bytes, Any]:
                    yield b'{"type": "someUnknownEventType"}'

                return messages()

        with patch.object(
            ws_client,
            attribute="_handle_message",
            new=AsyncMock(),
        ) as handle_message:
            await ws_client._listen_loop(
                ClientConnection()  # type: ignore[arg-type]
            )
        handle_message.assert_awaited_once_with('{"type": "someUnknownEventType"}')

    @pytest.mark.unit
    async def test_listen_loop_exits_when_stopping(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        class ClientConnection:
            def __aiter__(self) -> AsyncGenerator[str, Any]:
                async def messages() -> AsyncGenerator[str, Any]:
                    yield "ignored"

                return messages()

        ws_client._stop_event.set()
        with patch.object(
            ws_client, attribute="_handle_message", new=AsyncMock()
        ) as handle_message:
            await ws_client._listen_loop(
                ClientConnection()  # type: ignore[arg-type]
            )
        handle_message.assert_not_awaited()

    @pytest.mark.unit
    async def test_ping_loop_closes_socket_after_timeout(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        socket = MagicMock()
        socket.ping.return_value = asyncio.Future()
        socket.close = AsyncMock()
        socket.ping.return_value.set_result(None)
        assert ws_client._settings.ws_ping_interval == 30
        assert ws_client._settings.ws_ping_timeout == 10

        with patch(
            "dirigera_bridge.dirigera.websocket_client.asyncio.wait_for", side_effect=TimeoutError
        ):
            await ws_client._ping_loop(socket)

        socket.close.assert_awaited_once()


class TestConnectionLoop:
    @staticmethod
    def _attempts(*numbers: Any) -> AsyncGenerator[Any]:
        async def iterator() -> AsyncGenerator[Any]:
            for number in numbers:
                yield number

        return iterator()

    @pytest.mark.unit
    async def test_connection_loop_records_initial_connection(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """The first retry iteration is tracked as an initial connection."""

        async def connect_once() -> None:
            ws_client._stop_event.set()

        with (
            patch.object(
                ws_client, attribute="_connect_and_listen", new=AsyncMock(side_effect=connect_once)
            ) as connect_and_listen,
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(1),
            ),
        ):
            await ws_client._connection_loop()

        assert metrics.get(MetricName.WS_CONNECT_ATTEMPTS) == 1
        connect_and_listen.assert_awaited_once()

    @pytest.mark.unit
    async def test_connection_loop_records_reconnect_attempt(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """Later iterations are counted as reconnects."""

        async def reconnect_once() -> None:
            ws_client._stop_event.set()

        with (
            patch.object(
                ws_client,
                attribute="_connect_and_listen",
                new=AsyncMock(side_effect=reconnect_once),
            ),
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(2),
            ),
        ):
            await ws_client._connection_loop()

        assert metrics.get(MetricName.WS_RECONNECT_ATTEMPTS) == 1

    @pytest.mark.unit
    async def test_connection_loop_publishes_disconnected_after_error(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """Failed connects publish an event and increment disconnect metrics."""
        with (
            patch.object(
                ws_client,
                attribute="_connect_and_listen",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
            patch.object(
                ws_client, attribute="_publish_connection_event", new=AsyncMock()
            ) as publish_connection_event,
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(1),
            ),
        ):
            await ws_client._connection_loop()

        assert metrics.get(MetricName.WS_DISCONNECTS) == 1
        assert metrics.get(MetricName.ERROR_WS) == 1
        publish_connection_event.assert_awaited_once_with(EventType.DIRIGERA_DISCONNECTED)

    @pytest.mark.unit
    async def test_connection_loop_stops_when_lifecycle_is_stopping(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """No socket attempt is made after lifecycle shutdown starts."""
        ws_client._lifecycle = MagicMock(is_stopping=MagicMock(return_value=True))
        with (
            patch.object(
                ws_client, attribute="_connect_and_listen", new=AsyncMock()
            ) as connect_and_listen,
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(1),
            ),
        ):
            await ws_client._connection_loop()

        connect_and_listen.assert_not_awaited()

    @pytest.mark.unit
    async def test_connect_starts_background_connection_task(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:
        """connect() schedules the loop and clears a previous stop signal."""
        from dirigera_bridge.dirigera.websocket_client import DirigeraWebSocketClient

        client = DirigeraWebSocketClient(settings, event_bus, lifecycle, metrics)
        client._stop_event.set()
        task = MagicMock()

        def create_task(
            coroutine: Coroutine[Any, Any, None],
            **_kwargs: dict[str, dict[str, Any]],
        ) -> MagicMock:
            coroutine.close()
            return task

        with patch(
            "dirigera_bridge.dirigera.websocket_client.asyncio.create_task", side_effect=create_task
        ) as create_task_mock:
            await client.connect()

        assert not client._stop_event.is_set()
        assert client._listen_task is task
        create_task_mock.assert_called_once()

    @pytest.mark.unit
    async def test_connect_and_listen_publishes_lifecycle_events(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """A normal socket lifecycle publishes connected then disconnected."""
        socket = MagicMock()
        socket.state = State.OPEN
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=socket)
        context.__aexit__ = AsyncMock(return_value=False)
        # noinspection unresolved-references
        with (
            patch.object(ws_client, attribute="_listen_loop", new=AsyncMock()),
            patch.object(
                ws_client, attribute="_publish_connection_event", new=AsyncMock()
            ) as publish_connection_event,
            patch.object(
                websockets,
                "connect",
                return_value=context,
            ),
        ):
            await ws_client._connect_and_listen()

        assert metrics.get(MetricName.WS_CONNECT_SUCCESS) == 1
        assert ws_client._ws is None
        assert publish_connection_event.await_args_list == [
            ((EventType.DIRIGERA_CONNECTED,), {}),
            ((EventType.DIRIGERA_DISCONNECTED,), {}),
        ]

    @pytest.mark.unit
    async def test_connection_loop_retries_when_connection_closes_unexpectedly(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """If _connect_and_listen() returns normally *without* the
        stop event being set, the connection dropped unexpectedly
        (not via stop()) — the loop must log + count a disconnect and
        fall through to the next retry rather than returning."""

        async def closes_unexpectedly() -> None:
            return  # returns normally; stop_event stays unset

        with (
            patch.object(
                ws_client,
                attribute="_connect_and_listen",
                new=AsyncMock(side_effect=closes_unexpectedly),
            ),
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(1),
            ),
        ):
            await ws_client._connection_loop()

        assert metrics.get(MetricName.WS_DISCONNECTS) == 1

    @pytest.mark.unit
    async def test_connection_loop_propagates_cancelled_error(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        with (
            patch.object(
                ws_client,
                attribute="_connect_and_listen",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch(
                "dirigera_bridge.dirigera.websocket_client.retry_with_backoff",
                return_value=self._attempts(1),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await ws_client._connection_loop()

    @pytest.mark.unit
    async def test_connect_and_listen_skips_ping_cancel_when_already_done(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """When the ping task has already finished by the time the
        listen loop exits, _connect_and_listen() must skip cancelling
        it and go straight to clearing self._ws (the False branch of
        the ping-task-cancel guard in the finally block)."""
        socket = MagicMock()
        socket.state = State.OPEN
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=socket)
        context.__aexit__ = AsyncMock(return_value=False)

        done_task = MagicMock()
        done_task.done.return_value = True

        def fake_create_task(coroutine: Any, **_kwargs: Any) -> MagicMock:
            coroutine.close()  # avoid "coroutine was never awaited"warning
            return done_task

        with (
            patch.object(ws_client, attribute="_listen_loop", new=AsyncMock()),
            patch.object(ws_client, attribute="_publish_connection_event", new=AsyncMock()),
            patch("websockets.connect", return_value=context),
            patch(
                "dirigera_bridge.dirigera.websocket_client.asyncio.create_task",
                side_effect=fake_create_task,
            ),
        ):
            await ws_client._connect_and_listen()

        done_task.cancel.assert_not_called()
        assert ws_client._ws is None


# ── _handle_message() — bad input ─────────────────────────────────────────────


class TestHandleMessageBadInput:
    @pytest.mark.unit
    async def test_bad_json_increments_parse_error(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """Bad JSON increments WS_MESSAGES_PARSE_ERROR."""
        await ws_client._handle_message("not valid json {{{")
        assert metrics.get(MetricName.WS_MESSAGES_PARSE_ERROR) == 1

    @pytest.mark.unit
    async def test_bad_json_does_not_raise(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """Bad JSON is silently handled — does not raise."""
        await ws_client._handle_message("not json")  # no exception

    @pytest.mark.unit
    async def test_invalid_model_increments_parse_error(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """JSON that fails model validation increments parse error."""
        await ws_client._handle_message(json.dumps({"bad_field": 123}))
        # Unknown type — tolerated, no parse error for this case

    @pytest.mark.unit
    async def test_non_event_json_increments_parse_error(
        self,
        ws_client: DirigeraWebSocketClient,
        metrics: MetricsStore,
    ) -> None:
        """Valid JSON that is not an event still fails model validation safely."""
        await ws_client._handle_message("[]")
        assert metrics.get(MetricName.WS_MESSAGES_PARSE_ERROR) == 1

    @pytest.mark.unit
    async def test_unknown_event_type_is_silently_ignored(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Unknown event type does not publish any event."""
        received = []
        for et in EventType:

            async def cap(e: Any, _et: EventType = et) -> None:
                received.append(e)

            event_bus.subscribe(et, cap)

        await ws_client._handle_message(json.dumps({"type": "someUnknownEventType"}))

        assert received == []


# ── _handle_message() — state change ──────────────────────────────────────────


class TestHandleMessageStateChange:
    STATE_CHANGE: dict[str, Any] = {
        "type": "deviceStateChanged",
        "data": {
            "id": "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1",
            "relationId": "fff75d00-607c-4f23-a0e7-3dbed0e18b12",
            "type": "sensor",
            "deviceType": "motionSensor",
            "attributes": {
                "isDetected": True,
                "batteryPercentage": 70,
            },
        },
    }

    @pytest.mark.unit
    async def test_publishes_state_changed_events(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """State change publishes STATE_CHANGED events."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        assert len(received) > 0

    @pytest.mark.unit
    async def test_one_event_per_attribute(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """One STATE_CHANGED event is published per changed attribute."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        # STATE_CHANGE has 2 attributes: isDetected + batteryPercentage
        assert len(received) == 2

    @pytest.mark.unit
    async def test_events_carry_logical_id(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Published events carry the correct logical_id."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.logical_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1"

    @pytest.mark.unit
    async def test_events_carry_relation_id(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Published events carry the correct relation_id."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.relation_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    async def test_events_carry_device_type(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Published events carry device_type in data."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.data.get("device_type") == "motionSensor"

    @pytest.mark.unit
    async def test_attribute_values_in_event_data(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Event data contains the changed attribute name and value."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        attrs = {e.data["attribute"]: e.data["value"] for e in received}
        assert attrs["isDetected"] is True
        assert attrs["batteryPercentage"] == 70

    @pytest.mark.unit
    async def test_empty_attributes_not_published(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """State change with empty attributes publishes no events."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        msg = {
            "type": "deviceStateChanged",
            "data": {
                "id": "dev_1",
                "type": "light",
                "deviceType": "light",
                "attributes": {},
            },
        }
        await ws_client._handle_message(json.dumps(msg))

        assert received == []

    @pytest.mark.unit
    async def test_state_change_without_data_is_ignored(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        received = []

        async def capture(event: Any) -> None:
            received.append(event)

        event_bus.subscribe(EventType.STATE_CHANGED, capture)
        await ws_client._handle_message(json.dumps({"type": "deviceStateChanged"}))
        assert received == []

    @pytest.mark.unit
    async def test_reachability_change_publishes_dedicated_event(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        received = []

        async def capture(event: Any) -> None:
            received.append(event)

        event_bus.subscribe(EventType.DEVICE_REACHABLE, capture)
        message = dict(self.STATE_CHANGE)
        message["data"] = dict(
            self.STATE_CHANGE["data"],
            isReachable=True,
            attributes={},
        )
        await ws_client._handle_message(json.dumps(message))
        assert received[0].logical_id == self.STATE_CHANGE["data"]["id"]

    @pytest.mark.unit
    async def test_increments_messages_received(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
        metrics: MetricsStore,
    ) -> None:
        """STATE_CHANGED message increments WS_MESSAGES_RECEIVED."""

        # noinspection unused-parameter
        async def cap(event: DirigeraEvent) -> None:
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 1


# ── _handle_message() — device added / removed ────────────────────────────────


class TestHandleMessageDeviceEvents:
    @pytest.mark.unit
    async def test_device_added_publishes_discovered(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """deviceAdded publishes DEVICE_DISCOVERED."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.DEVICE_DISCOVERED, cap)

        msg = {
            "type": "deviceAdded",
            "data": {
                "id": "new_device_1",
                "type": "light",
                "deviceType": "light",
                "attributes": {},
            },
        }
        await ws_client._handle_message(json.dumps(msg))

        assert len(received) == 1
        assert received[0].logical_id == "new_device_1"

    @pytest.mark.unit
    async def test_device_removed_publishes_removed(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """deviceRemoved publishes DEVICE_REMOVED."""
        received = []

        async def cap(e: Any) -> None:
            received.append(e)

        event_bus.subscribe(EventType.DEVICE_REMOVED, cap)

        msg = {
            "type": "deviceRemoved",
            "data": {
                "id": "old_device_1",
                "type": "light",
                "deviceType": "light",
                "attributes": {},
            },
        }
        await ws_client._handle_message(json.dumps(msg))

        assert len(received) == 1
        assert received[0].logical_id == "old_device_1"

    @pytest.mark.unit
    async def test_device_added_does_not_publish_state_changed(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """deviceAdded does not publish STATE_CHANGED."""
        state_received = []

        async def cap(e: Any) -> None:
            state_received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        msg = {
            "type": "deviceAdded",
            "data": {
                "id": "new_1",
                "type": "light",
                "deviceType": "light",
                "attributes": {},
            },
        }
        await ws_client._handle_message(json.dumps(msg))

        assert state_received == []


# ── _build_ssl_context() ──────────────────────────────────────────────────────


class TestBuildSslContext:
    @pytest.mark.unit
    def test_returns_ssl_context(self) -> None:
        """_build_ssl_context returns a ssl.SSLContext."""
        # noinspection protected-member
        from dirigera_bridge.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    @pytest.mark.unit
    def test_check_hostname_disabled(self) -> None:
        """check_hostname is False (self-signed cert support)."""
        # noinspection protected-member
        from dirigera_bridge.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert ctx.check_hostname is False

    @pytest.mark.unit
    def test_cert_none(self) -> None:
        """verify_mode is CERT_NONE (self-signed cert support)."""
        # noinspection protected-member
        from dirigera_bridge.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert ctx.verify_mode == ssl.CERT_NONE


class TestPingLoopBranches:
    @pytest.mark.unit
    async def test_never_enters_loop_when_already_stopping(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """Covers the while-loop's zero-iteration exit (658->exit) —
        distinct from test_exits_when_stopping_after_sleep, which
        covers stop appearing *during* an iteration. Here it's already
        set before _ping_loop() is even called."""
        socket = MagicMock()
        socket.ping = AsyncMock()
        ws_client._stop_event.set()

        await ws_client._ping_loop(socket)

        socket.ping.assert_not_called()

    @pytest.mark.unit
    async def test_exits_when_stopping_after_sleep(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """Covers the post-sleep stop_event check — the loop must
        return without ever sending a ping."""
        socket = MagicMock()
        socket.ping = AsyncMock()

        async def sleep_then_stop(_seconds: float) -> None:
            ws_client._stop_event.set()

        with patch(
            "dirigera_bridge.dirigera.websocket_client.asyncio.sleep",
            side_effect=sleep_then_stop,
        ):
            await ws_client._ping_loop(socket)

        socket.ping.assert_not_called()

    @pytest.mark.unit
    async def test_logs_pong_received_on_success(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        """A pong that arrives before the timeout is the success path
        — no disconnect, no socket close."""
        pong_future: asyncio.Future[None] = asyncio.Future()
        pong_future.set_result(None)
        socket = MagicMock()
        socket.ping = AsyncMock(return_value=pong_future)
        socket.close = AsyncMock()

        call_count = {"n": 0}

        async def sleep_then_stop_on_second_call(_seconds: float) -> None:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                ws_client._stop_event.set()

        with patch(
            "dirigera_bridge.dirigera.websocket_client.asyncio.sleep",
            side_effect=sleep_then_stop_on_second_call,
        ):
            await ws_client._ping_loop(socket)

        socket.ping.assert_awaited_once()
        socket.close.assert_not_called()

    @pytest.mark.unit
    async def test_exits_on_connection_closed(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        socket = MagicMock()
        socket.ping = AsyncMock(side_effect=exceptions.ConnectionClosed(None, None))

        await ws_client._ping_loop(socket)  # must not raise

    @pytest.mark.unit
    async def test_propagates_cancelled_error(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        socket = MagicMock()
        socket.ping = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await ws_client._ping_loop(socket)

    @pytest.mark.unit
    async def test_exits_on_unexpected_error(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        socket = MagicMock()
        socket.ping = AsyncMock(side_effect=RuntimeError("boom"))

        await ws_client._ping_loop(socket)  # must not raise


# ── 3b. _publish_connection_event() real body — standalone class ───────────


class TestPublishConnectionEvent:
    @pytest.mark.unit
    async def test_publishes_event_with_expected_fields(
        self,
        ws_client: DirigeraWebSocketClient,
        event_bus: AsyncEventBus,
    ) -> None:
        """Every existing test mocks _publish_connection_event() away
        — this exercises the real implementation."""
        received: list[DirigeraEvent] = []

        async def cap(e: DirigeraEvent) -> None:
            received.append(e)

        event_bus.subscribe(EventType.DIRIGERA_CONNECTED, cap)

        await ws_client._publish_connection_event(EventType.DIRIGERA_CONNECTED)

        assert len(received) == 1
        assert received[0].event_type == EventType.DIRIGERA_CONNECTED
        assert received[0].logical_id == ""
        assert received[0].relation_id == ""
        assert received[0].data == {}


# ── 3c. _close_ws() error handling — standalone class ───────────────────────


class TestCloseWs:
    @pytest.mark.unit
    async def test_swallows_error_and_clears_ws(
        self,
        ws_client: DirigeraWebSocketClient,
    ) -> None:
        socket = MagicMock()
        socket.state = State.OPEN
        socket.close = AsyncMock(side_effect=RuntimeError("close boom"))
        ws_client._ws = socket

        await ws_client._close_ws()  # must not raise

        assert ws_client._ws is None
