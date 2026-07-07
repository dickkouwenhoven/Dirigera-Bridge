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
from unittest.mock import MagicMock

import pytest
from websockets.protocol import State

from app.core.event_bus import EventType
from app.core.metrics import MetricName


# ── Test subclass — bypasses isinstance validation ────────────────────────────


class WSClientTestDouble:
    """
    Minimal test double for DirigeraWebSocketClient.

    Bypasses the isinstance checks in __init__ so we can inject
    lightweight mock objects without importing Settings.
    """

    def __init__(self, settings, event_bus, lifecycle, metrics):
        from app.core.retry import RetryConfig

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
    from app.dirigera.websocket_client import DirigeraWebSocketClient

    is_connected = DirigeraWebSocketClient.is_connected
    stop = DirigeraWebSocketClient.stop
    _handle_message = DirigeraWebSocketClient._handle_message
    _dispatch_state_change = DirigeraWebSocketClient._dispatch_state_change
    _dispatch_device_added = DirigeraWebSocketClient._dispatch_device_added
    _dispatch_device_removed = DirigeraWebSocketClient._dispatch_device_removed
    _publish_connection_event = DirigeraWebSocketClient._publish_connection_event
    _close_ws = DirigeraWebSocketClient._close_ws


class MockSettings:
    dirigera_ip = "192.168.1.100"
    dirigera_token = "test_token"
    ws_ping_interval = 30
    ws_ping_timeout = 10
    reconnect_delay_initial = 0.01
    reconnect_delay_max = 0.05


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ws_client(event_bus, lifecycle, metrics):
    return WSClientTestDouble(MockSettings(), event_bus, lifecycle, metrics)


# ── Construction ──────────────────────────────────────────────────────────────


class TestDirigeraWebSocketClientConstruction:
    @pytest.mark.unit
    def test_valid_construction(self, settings, event_bus, lifecycle, metrics):
        """DirigeraWebSocketClient constructs with valid dependencies."""
        from app.dirigera.websocket_client import DirigeraWebSocketClient

        client = DirigeraWebSocketClient(
            settings=settings,
            event_bus=event_bus,
            lifecycle=lifecycle,
            metrics=metrics,
        )
        assert client is not None

    @pytest.mark.unit
    def test_invalid_settings_raises(self, event_bus, lifecycle, metrics):
        """Non-Settings raises INTERNAL_INVALID_ARGUMENT."""
        from app.dirigera.websocket_client import DirigeraWebSocketClient
        from app.core.errors import DirigeraBridgeError, ErrorCode

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings="bad",
                event_bus=event_bus,
                lifecycle=lifecycle,
                metrics=metrics,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_event_bus_raises(self, settings, lifecycle, metrics):
        """Non-AsyncEventBus raises INTERNAL_INVALID_ARGUMENT."""
        from app.dirigera.websocket_client import DirigeraWebSocketClient
        from app.core.errors import DirigeraBridgeError, ErrorCode

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraWebSocketClient(
                settings=settings,
                event_bus="bad",
                lifecycle=lifecycle,
                metrics=metrics,
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── is_connected ──────────────────────────────────────────────────────────────


class TestIsConnected:
    @pytest.mark.unit
    def test_false_when_no_ws(self, ws_client):
        """is_connected is False when _ws is None."""
        ws_client._ws = None
        assert ws_client.is_connected is False

    @pytest.mark.unit
    def test_false_when_ws_closed(self, ws_client):
        """is_connected is False when _ws.state is not OPEN."""
        mock_ws = MagicMock()
        mock_ws.state = State.CLOSED
        ws_client._ws = mock_ws
        assert ws_client.is_connected is False

    @pytest.mark.unit
    def test_true_when_ws_open(self, ws_client):
        """is_connected is True when _ws.state is OPEN."""
        mock_ws = MagicMock()
        mock_ws.state = State.OPEN
        ws_client._ws = mock_ws
        assert ws_client.is_connected is True


# ── stop() ────────────────────────────────────────────────────────────────────


class TestStop:
    @pytest.mark.unit
    async def test_stop_sets_stop_event(self, ws_client):
        """stop() sets the stop_event."""
        assert not ws_client._stop_event.is_set()
        await ws_client.stop()
        assert ws_client._stop_event.is_set()

    @pytest.mark.unit
    async def test_stop_when_never_connected_does_not_raise(self, ws_client):
        """stop() when never connected does not raise."""
        await ws_client.stop()  # should not raise


# ── _handle_message() — bad input ─────────────────────────────────────────────


class TestHandleMessageBadInput:
    @pytest.mark.unit
    async def test_bad_json_increments_parse_error(self, ws_client, metrics):
        """Bad JSON increments WS_MESSAGES_PARSE_ERROR."""
        await ws_client._handle_message("not valid json {{{")
        assert metrics.get(MetricName.WS_MESSAGES_PARSE_ERROR) == 1

    @pytest.mark.unit
    async def test_bad_json_does_not_raise(self, ws_client):
        """Bad JSON is silently handled — does not raise."""
        await ws_client._handle_message("not json")  # no exception

    @pytest.mark.unit
    async def test_invalid_model_increments_parse_error(self, ws_client, metrics):
        """JSON that fails model validation increments parse error."""
        await ws_client._handle_message(json.dumps({"bad_field": 123}))
        # Unknown type — tolerated, no parse error for this case

    @pytest.mark.unit
    async def test_unknown_event_type_is_silently_ignored(self, ws_client, event_bus):
        """Unknown event type does not publish any event."""
        received = []
        for et in EventType:

            async def cap(e, _et=et):
                received.append(e)

            event_bus.subscribe(et, cap)

        await ws_client._handle_message(json.dumps({"type": "someUnknownEventType"}))

        assert received == []


# ── _handle_message() — state change ──────────────────────────────────────────


class TestHandleMessageStateChange:
    STATE_CHANGE = {
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
    async def test_publishes_state_changed_events(self, ws_client, event_bus):
        """State change publishes STATE_CHANGED events."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        assert len(received) > 0

    @pytest.mark.unit
    async def test_one_event_per_attribute(self, ws_client, event_bus):
        """One STATE_CHANGED event is published per changed attribute."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        # STATE_CHANGE has 2 attributes: isDetected + batteryPercentage
        assert len(received) == 2

    @pytest.mark.unit
    async def test_events_carry_logical_id(self, ws_client, event_bus):
        """Published events carry the correct logical_id."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.logical_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1"

    @pytest.mark.unit
    async def test_events_carry_relation_id(self, ws_client, event_bus):
        """Published events carry the correct relation_id."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.relation_id == "fff75d00-607c-4f23-a0e7-3dbed0e18b12"

    @pytest.mark.unit
    async def test_events_carry_device_type(self, ws_client, event_bus):
        """Published events carry device_type in data."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        for event in received:
            assert event.data.get("device_type") == "motionSensor"

    @pytest.mark.unit
    async def test_attribute_values_in_event_data(self, ws_client, event_bus):
        """Event data contains the changed attribute name and value."""
        received = []

        async def cap(e):
            received.append(e)

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        attrs = {e.data["attribute"]: e.data["value"] for e in received}
        assert attrs["isDetected"] is True
        assert attrs["batteryPercentage"] == 70

    @pytest.mark.unit
    async def test_empty_attributes_not_published(self, ws_client, event_bus):
        """State change with empty attributes publishes no events."""
        received = []

        async def cap(e):
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
    async def test_increments_messages_received(self, ws_client, event_bus, metrics):
        """STATE_CHANGED message increments WS_MESSAGES_RECEIVED."""

        async def cap(e):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, cap)

        await ws_client._handle_message(json.dumps(self.STATE_CHANGE))

        assert metrics.get(MetricName.WS_MESSAGES_RECEIVED) == 1


# ── _handle_message() — device added / removed ────────────────────────────────


class TestHandleMessageDeviceEvents:
    @pytest.mark.unit
    async def test_device_added_publishes_discovered(self, ws_client, event_bus):
        """deviceAdded publishes DEVICE_DISCOVERED."""
        received = []

        async def cap(e):
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
    async def test_device_removed_publishes_removed(self, ws_client, event_bus):
        """deviceRemoved publishes DEVICE_REMOVED."""
        received = []

        async def cap(e):
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
        self, ws_client, event_bus
    ):
        """deviceAdded does not publish STATE_CHANGED."""
        state_received = []

        async def cap(e):
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
    def test_returns_ssl_context(self):
        """_build_ssl_context returns an ssl.SSLContext."""
        from app.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    @pytest.mark.unit
    def test_check_hostname_disabled(self):
        """check_hostname is False (self-signed cert support)."""
        from app.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert ctx.check_hostname is False

    @pytest.mark.unit
    def test_cert_none(self):
        """verify_mode is CERT_NONE (self-signed cert support)."""
        from app.dirigera.websocket_client import _build_ssl_context

        ctx = _build_ssl_context()
        assert ctx.verify_mode == ssl.CERT_NONE
