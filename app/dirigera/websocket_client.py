"""
websocket_client.py

Async WebSocket client for the Dirigera hub real-time event stream.

Role & Responsibility:
    Owns and maintains the persistent WebSocket connection to the
    Dirigera hub. Listens for real-time device state change events,
    parses them into typed DirigeraWebSocketEvent models, and publishes
    domain events onto the AsyncEventBus for the rest of the application
    to consume.

    This is the only module that knows the Dirigera WebSocket URL,
    authentication scheme, ping/pong keepalive behavior, and
    reconnection logic.

What it does:
    - Establishes an authenticated WebSocket connection to the hub
      at wss://{ip}:8443/v1/events
    - Sends periodic pings to keep the connection alive and detects
      dead connections via pong timeout
    - Receives raw JSON messages, validates them into
      DirigeraWebSocketEvent models, and publishes typed DirigeraEvent
      objects onto the event bus
    - Publishes DIRIGERA_CONNECTED / DIRIGERA_DISCONNECTED events so
      the orchestrator can react to connection state changes
    - Reconnects automatically using RetryConfig / retry_with_backoff
      from core.retry, stopping when the lifecycle signals shutdown
    - Tracks WebSocket metrics (messages received, errors, reconnects)

Arguments / Configuration:
    settings (Settings):       Injected application settings. Reads
                               ws_ping_interval, ws_ping_timeout,
                               dirigera_ip, dirigera_token,
                               reconnect_delay_initial,
                               reconnect_delay_max.
    event_bus (AsyncEventBus): Injected event bus. All parsed events
                               are published here.
    lifecycle (ServiceLifecycle): Injected lifecycle. The reconnect
                               loop checks lifecycle.is_stopping() to
                               know when to exit cleanly.
    metrics (MetricsStore):    Injected metrics store.

Used by:
    - app/orchestrator.py   (creates client, calls connect() and
                             stop() during startup and shutdown)

Not responsible for:
    - Processing or mapping events (that is the mapping layer)
    - Sending commands to devices (that is rest_client.py)
    - MQTT publishing (that is ha_client.py)
    - Fetching the initial device list (that is rest_client.py)

Design notes:
    - Uses the websockets library (not aiohttp) for the WebSocket
      connection. websockets provides native async/await support and
      explicit ping/pong control.
    - Dirigera uses a self-signed TLS certificate. ssl_context is
      created with check_hostname=False and CERT_NONE — intentional
      for local hub communication, same as rest_client.py.
    - The WebSocket URL is wss://{ip}:8443/v1/events
    - Authentication is via the Authorization header (Bearer token),
      sent as an additional_headers dict to websockets.connect().
    - Ping/pong is managed manually (ping_interval=None passed to
      websockets) so we control the timing precisely using
      ws_ping_interval and ws_ping_timeout from settings.
    - A stop_event (asyncio.Event) is used to interrupt the reconnect
      backoff sleep cleanly during shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Optional

import websockets
import websockets.exceptions
from websockets import ClientConnection
from websockets.protocol import State

from ..config import Settings
from ..core.errors import DirigeraBridgeError, ErrorCode
from ..core.event_bus import AsyncEventBus, DirigeraEvent, EventType
from ..core.lifecycle import ServiceLifecycle
from ..core.metrics import MetricName, MetricsStore
from ..core.retry import RetryConfig, retry_with_backoff
from .models import DirigeraWebSocketEvent

__all__ = [
    "DirigeraWebSocketClient",
]

logger = logging.getLogger(__name__)

# Dirigera WebSocket endpoint
_WS_PORT = 8443
_WS_PATH = "/v1/events"


class DirigeraWebSocketClient:
    """
    Async WebSocket client for the Dirigera hub event stream.

    Maintains a persistent connection with automatic reconnection.
    All incoming device events are published onto the AsyncEventBus
    as typed DirigeraEvent objects.

    Args:
        settings  (Settings):        Application settings.
        event_bus (AsyncEventBus):   Event bus for publishing events.
        lifecycle (ServiceLifecycle):Lifecycle for shutdown detection.
        metrics   (MetricsStore):    Metrics store for counters.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if any argument
                             is not the correct type.
    """

    def __init__(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
    ) -> None:

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(settings, Settings):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraWebSocketClient: settings must be Settings, "
                f"got {type(settings).__name__}",
            )

        if not isinstance(event_bus, AsyncEventBus):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraWebSocketClient: event_bus must be "
                f"AsyncEventBus, got {type(event_bus).__name__}",
            )

        if not isinstance(lifecycle, ServiceLifecycle):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraWebSocketClient: lifecycle must be "
                f"ServiceLifecycle, got {type(lifecycle).__name__}",
            )

        if not isinstance(metrics, MetricsStore):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraWebSocketClient: metrics must be "
                f"MetricsStore, got {type(metrics).__name__}",
            )

        self._settings = settings
        self._event_bus = event_bus
        self._lifecycle = lifecycle
        self._metrics = metrics

        # WebSocket connection — set while connected
        self._ws: Optional[ClientConnection] = None

        # Signals the reconnect loop to stop cleanly during shutdown
        self._stop_event: asyncio.Event = asyncio.Event()

        # Background tasks
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

        self._ws_url = f"wss://{settings.dirigera_ip}:{_WS_PORT}{_WS_PATH}"

        self._retry_config = RetryConfig(
            initial_delay=settings.reconnect_delay_initial,
            max_delay=settings.reconnect_delay_max,
            multiplier=2.0,
            jitter_max=1.0,
            max_attempts=None,  # retry indefinitely until stop
        )

        logger.debug(
            "DirigeraWebSocketClient initialised (url=%s)",
            self._ws_url,
        )

    # ── Public API ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Start the WebSocket connection and the reconnect loop.

        Returns immediately after launching the connection loop as a
        background asyncio Task. The task manages the connection,
        listens for events, and reconnects automatically on failure.

        The caller (orchestrator) should await stop() to shut down
        cleanly.

        Raises:
            DirigeraBridgeError: WS_CONNECTION_FAILED if the initial
                                 connection attempt fails and cannot
                                 be retried.
        """

        logger.info(
            "DirigeraWebSocketClient: starting connection to %s",
            self._ws_url,
        )

        self._stop_event.clear()

        self._listen_task = asyncio.create_task(
            self._connection_loop(),
            name="dirigera-ws-connection-loop",
        )

        logger.debug("DirigeraWebSocketClient: connection loop task started")

    async def stop(self) -> None:
        """
        Signal the connection loop to stop and wait for it to exit.

        Sets the stop event (which interrupts any backoff sleep) and
        cancels all background tasks. Safe to call even if connect()
        was never called.
        """

        logger.info("DirigeraWebSocketClient: stopping")

        self._stop_event.set()

        # ── Cancel ping task ──────────────────────────────────────────────
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        # ── Close WebSocket ───────────────────────────────────────────────
        await self._close_ws()

        # ── Cancel connection loop ────────────────────────────────────────
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        logger.info("DirigeraWebSocketClient: stopped")

    @property
    def is_connected(self) -> bool:
        """
        Return True if the WebSocket connection is currently open.

        Returns:
            bool: True if connected.
        """

        return self._ws is not None and self._ws.state is State.OPEN

    # ── Internal — connection lifecycle ──────────────────────────────────

    async def _connection_loop(self) -> None:
        """
        Main reconnect loop. Runs until stop_event is set.

        Uses retry_with_backoff to space out reconnection attempts.
        On each attempt it tries to open the WebSocket, start the
        ping task, and then listens for events. If the connection
        drops, the loop sleeps and tries again.
        """

        async for attempt in retry_with_backoff(
            self._retry_config,
            stop_event=self._stop_event,
        ):
            if self._lifecycle.is_stopping():
                logger.info(
                    "DirigeraWebSocketClient: lifecycle is stopping — "
                    "exiting connection loop"
                )
                return

            # ── Validation ────────────────────────────────────────────────
            if attempt > 1:
                self._metrics.increment(MetricName.WS_RECONNECT_ATTEMPTS)
                logger.info(
                    "DirigeraWebSocketClient: reconnect attempt %d",
                    attempt,
                )
            else:
                self._metrics.increment(MetricName.WS_CONNECT_ATTEMPTS)
                logger.info(
                    "DirigeraWebSocketClient: connect attempt %d",
                    attempt,
                )

            # ── Try to connect and listen ─────────────────────────────────
            try:
                await self._connect_and_listen()

                # If _connect_and_listen returns normally it means
                # the connection was closed cleanly (e.g. stop() was
                # called). Exit the loop.
                if self._stop_event.is_set():
                    return

                # Connection dropped unexpectedly — fall through to retry
                logger.warning(
                    "DirigeraWebSocketClient: connection closed "
                    "unexpectedly — will retry"
                )
                self._metrics.increment(MetricName.WS_DISCONNECTS)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "DirigeraWebSocketClient: connection attempt %d failed: %s",
                    attempt,
                    exc,
                )
                self._metrics.increment(MetricName.WS_DISCONNECTS)
                self._metrics.increment(MetricName.ERROR_WS)

                # Publish disconnected event so orchestrator can react
                await self._publish_connection_event(EventType.DIRIGERA_DISCONNECTED)

                # Fall through — retry_with_backoff will sleep then
                # yield the next attempt number

    async def _connect_and_listen(self) -> None:
        """
        Open the WebSocket connection, start the ping task, and
        listen for incoming events until the connection closes.

        Publishes DIRIGERA_CONNECTED on successful open and
        DIRIGERA_DISCONNECTED on close.

        Raises:
            Any exception from websockets.connect() or the listen loop
            propagates to _connection_loop() which handles retry logic.
        """

        ssl_ctx = _build_ssl_context()

        logger.debug(
            "DirigeraWebSocketClient: opening WebSocket to %s",
            self._ws_url,
        )

        async with websockets.connect(
            self._ws_url,
            ssl=ssl_ctx,
            additional_headers={"Authorization": f"Bearer {self._settings.dirigera_token}"},
            ping_interval=None,  # we manage ping/pong manually
            ping_timeout=None,
            open_timeout=10,
        ) as ws:
            self._ws = ws
            self._metrics.increment(MetricName.WS_CONNECT_SUCCESS)

            logger.info(
                "DirigeraWebSocketClient: connected to %s",
                self._ws_url,
            )

            # ── Publish connected event ───────────────────────────────────
            await self._publish_connection_event(EventType.DIRIGERA_CONNECTED)

            # ── Start ping keepalive task ─────────────────────────────────
            self._ping_task = asyncio.create_task(
                self._ping_loop(ws),
                name="dirigera-ws-ping",
            )

            try:
                # ── Listen for messages ───────────────────────────────────
                await self._listen_loop(ws)

            finally:
                # ── Cancel ping task when connection closes ───────────────
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()
                    try:
                        await self._ping_task
                    except asyncio.CancelledError:
                        pass

                self._ws = None

        # ── Publish disconnected event ────────────────────────────────────
        await self._publish_connection_event(EventType.DIRIGERA_DISCONNECTED)

        logger.info("DirigeraWebSocketClient: WebSocket connection closed")

    async def _listen_loop(
        self,
        ws: ClientConnection,
    ) -> None:
        """
        Inner message receive loop. Runs until the WebSocket closes.

        Reads each incoming message, parses it, and publishes the
        appropriate DirigeraEvent onto the event bus.

        Args:
            ws: The open WebSocket connection.
        """

        async for raw_message in ws:
            if self._stop_event.is_set():
                return

            # ── Normalize to str (websockets v11+ yields str | bytes) ─────
            if isinstance(raw_message, bytes):
                message = raw_message.decode("utf-8")
            else:
                message = raw_message

            logger.debug(
                "DirigeraWebSocketClient: raw message received (len=%d)",
                len(message),
            )

            # ── Parse and dispatch ────────────────────────────────────────
            await self._handle_message(message)

    # ── Internal — message handling ───────────────────────────────────────

    async def _handle_message(self, raw_message: str) -> None:
        """
        Parse a raw WebSocket message string and publish the
        appropriate domain event(s) onto the event bus.

        Silently skips messages that cannot be parsed rather than
        crashing the listen loop — one malformed message must never
        disconnect the entire bridge.

        Args:
            raw_message (str): Raw JSON string from the WebSocket.
        """

        # Counts every message that reaches this point, regardless of
        # whether it goes on to parse successfully — "received" means
        # arrived, not "successfully handled". Moved here (from
        # _listen_loop) so this metric is testable at the same boundary
        # as the rest of this method, without needing a live socket
        # iterator.
        self._metrics.increment(MetricName.WS_MESSAGES_RECEIVED)

        # ── Parse JSON ────────────────────────────────────────────────────
        try:
            raw_dict = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            self._metrics.increment(MetricName.WS_MESSAGES_PARSE_ERROR)
            self._metrics.increment(MetricName.ERROR_WS)
            logger.warning(
                "DirigeraWebSocketClient: failed to parse JSON "
                "message: %s — raw: %.200s",
                exc,
                raw_message,
            )
            return

        # ── Validate into model ───────────────────────────────────────────
        try:
            ws_event = DirigeraWebSocketEvent.model_validate(raw_dict)
        except Exception as exc:
            self._metrics.increment(MetricName.WS_MESSAGES_PARSE_ERROR)
            self._metrics.increment(MetricName.ERROR_WS)
            logger.warning(
                "DirigeraWebSocketClient: failed to validate WebSocket event model: %s",
                exc,
            )
            return

        # ── Dispatch by event type ────────────────────────────────────────
        if ws_event.is_state_change:
            await self._dispatch_state_change(ws_event)

        elif ws_event.is_device_added:
            await self._dispatch_device_added(ws_event)

        elif ws_event.is_device_removed:
            await self._dispatch_device_removed(ws_event)

        else:
            logger.debug(
                "DirigeraWebSocketClient: unhandled event type '%s' — ignoring",
                ws_event.type,
            )

    async def _dispatch_state_change(
        self,
        ws_event: DirigeraWebSocketEvent,
    ) -> None:
        """
        Publish a STATE_CHANGED event for each changed attribute in
        the WebSocket event.

        Dirigera may include multiple changed attributes in a single
        event (e.g. isOn + lightLevel changed together). Each attribute
        change is published as a separate DirigeraEvent so the mapping
        layer can handle them individually.

        Args:
            ws_event: Validated WebSocket event with is_state_change=True.
        """

        if ws_event.data is None:
            logger.debug(
                "DirigeraWebSocketClient: state change event has "
                "no data block — ignoring"
            )
            return

        data = ws_event.data
        logical_id = data.id
        relation_id = data.physical_id
        changed = data.changed_attributes

        if not changed:
            logger.debug(
                "DirigeraWebSocketClient: state change event for %s "
                "has no changed attributes — ignoring",
                logical_id,
            )
            return

        logger.debug(
            "DirigeraWebSocketClient: state change for %s (device_type=%s): %s",
            logical_id,
            data.device_type,
            changed,
        )

        # ── Publish one event per changed attribute ───────────────────────
        for attribute, value in changed.items():
            event = DirigeraEvent(
                event_type=EventType.STATE_CHANGED,
                logical_id=logical_id,
                relation_id=relation_id,
                data={
                    "attribute": attribute,
                    "value": value,
                    "device_type": data.device_type,
                },
            )
            await self._event_bus.publish(event)

    async def _dispatch_device_added(
        self,
        ws_event: DirigeraWebSocketEvent,
    ) -> None:
        """
        Publish a DEVICE_DISCOVERED event when a new device is paired
        to the hub.

        The orchestrator subscribes to this event and triggers a
        re-fetch of the full device list to pick up the new device.

        Args:
            ws_event: Validated WebSocket event with is_device_added=True.
        """

        logical_id = ws_event.data.id if ws_event.data else ""
        relation_id = ws_event.data.physical_id if ws_event.data else ""

        logger.info(
            "DirigeraWebSocketClient: device added — logical_id=%s",
            logical_id,
        )

        event = DirigeraEvent(
            event_type=EventType.DEVICE_DISCOVERED,
            logical_id=logical_id,
            relation_id=relation_id,
            data={"raw_event": ws_event.model_dump()},
        )
        await self._event_bus.publish(event)

    async def _dispatch_device_removed(
        self,
        ws_event: DirigeraWebSocketEvent,
    ) -> None:
        """
        Publish a DEVICE_REMOVED event when a device is unpaired from
        the hub.

        The orchestrator subscribes to this event and removes the
        device from the discovery and state caches.

        Args:
            ws_event: Validated WebSocket event with
                      is_device_removed=True.
        """

        logical_id = ws_event.data.id if ws_event.data else ""
        relation_id = ws_event.data.physical_id if ws_event.data else ""

        logger.info(
            "DirigeraWebSocketClient: device removed — logical_id=%s",
            logical_id,
        )

        event = DirigeraEvent(
            event_type=EventType.DEVICE_REMOVED,
            logical_id=logical_id,
            relation_id=relation_id,
            data={},
        )
        await self._event_bus.publish(event)

    # ── Internal — ping keepalive ─────────────────────────────────────────

    async def _ping_loop(
        self,
        ws: ClientConnection,
    ) -> None:
        """
        Send periodic WebSocket ping frames and wait for pong replies.

        Runs as a background task alongside _listen_loop. If a pong
        is not received within ws_ping_timeout seconds, the connection
        is closed and the reconnect loop takes over.

        Args:
            ws: The open WebSocket connection.
        """

        ping_interval = self._settings.ws_ping_interval
        ping_timeout = self._settings.ws_ping_timeout

        logger.debug(
            "DirigeraWebSocketClient: ping loop started (interval=%ds, timeout=%ds)",
            ping_interval,
            ping_timeout,
        )

        while not self._stop_event.is_set():
            await asyncio.sleep(ping_interval)

            if self._stop_event.is_set():
                return

            # ── Send ping and await pong ──────────────────────────────────
            try:
                pong_waiter = await ws.ping()

                await asyncio.wait_for(
                    pong_waiter,
                    timeout=ping_timeout,
                )

                logger.debug("DirigeraWebSocketClient: pong received")

            except asyncio.TimeoutError:
                logger.warning(
                    "DirigeraWebSocketClient: pong not received within "
                    "%ds — closing connection to trigger reconnect",
                    ping_timeout,
                )
                self._metrics.increment(MetricName.WS_DISCONNECTS)
                await ws.close()
                return

            except websockets.exceptions.ConnectionClosed:
                logger.debug(
                    "DirigeraWebSocketClient: connection closed during "
                    "ping — ping loop exiting"
                )
                return

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "DirigeraWebSocketClient: unexpected error in ping loop: %s",
                    exc,
                )
                return

    # ── Internal — helpers ────────────────────────────────────────────────

    async def _publish_connection_event(
        self,
        event_type: EventType,
    ) -> None:
        """
        Publish a DIRIGERA_CONNECTED or DIRIGERA_DISCONNECTED event.

        Args:
            event_type (EventType): DIRIGERA_CONNECTED or
                                    DIRIGERA_DISCONNECTED.
        """

        event = DirigeraEvent(
            event_type=event_type,
            logical_id="",
            relation_id="",
            data={},
        )
        await self._event_bus.publish(event)

    async def _close_ws(self) -> None:
        """
        Close the WebSocket connection gracefully if it is open.

        Safe to call even if the connection is not open (no-op).
        """

        if self._ws is not None and self._ws.state is State.OPEN:
            try:
                await self._ws.close()
                logger.debug("DirigeraWebSocketClient: WebSocket closed")
            except Exception as exc:
                logger.debug(
                    "DirigeraWebSocketClient: error closing WebSocket (ignored): %s",
                    exc,
                )
            finally:
                self._ws = None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_ssl_context() -> ssl.SSLContext:
    """
    Build an SSL context that accepts the Dirigera hub's self-signed
    TLS certificate.

    check_hostname and certificate verification are intentionally
    disabled because Dirigera uses a self-signed cert that cannot be
    verified against a public certificate authority. This is expected
    and safe for local network communication.

    Returns:
        ssl.SSLContext: Configured SSL context.
    """

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    return ctx
