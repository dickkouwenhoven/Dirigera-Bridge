"""
event_bus.py

Async internal publish/subscribe event bus.

Role & Responsibility:
    The EventBus is the connective tissue between the Dirigera layer
    and the rest of the application. It decouples event producers
    (WebSocket client) from event consumers (orchestrator, state cache,
    metrics) so that neither side needs to know about the other.

    This is an in-process bus only — it never touches MQTT or any
    network transport. All communication across process boundaries
    (Dirigera ↔ HA) goes through the dedicated transport layers.

What it does:
    - Defines the DirigeraEvent dataclass: the single event type that
    flows through the bus
    - Defines EventType enum: all event categories the application
    produces and consumes
    - Provides AsyncEventBus with subscribe(), unsubscribe(),
    publish(), and publish_nowait() methods
    - Calls all registered async handlers for an event type
    concurrently using asyncio.gather()
    - Isolates handler errors so one failing handler never prevents
    others from receiving the event

Arguments / Configuration:
    No runtime configuration. Instantiated once by the orchestrator
    and injected into all layers that need it.

Used by:
    - app/orchestrator.py            (creates the bus, subscribes handlers)
    - app/dirigera/websocket_client.py    (publishes events)
    - app/core/state_cache.py        (subscribes to state events)
    - app/core/metrics.py            (subscribes to all events for counting)

Not responsible for:
    - MQTT publishing (ha_client.py)
    - Persistence (nothing is stored beyond the subscriber registry)
    - Cross-process or cross-thread communication
    - Event ordering guarantees beyond asyncio task scheduling
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "EventType",
    "DirigeraEvent",
    "AsyncEventBus",
]

logger = logging.getLogger(__name__)

# Type alias for async event handler callbacks
AsyncHandler = Callable[["DirigeraEvent"], Awaitable[None]]


# ── Event types ───────────────────────────────────────────────────────────────


@unique
class EventType(str, Enum):
    """
    All event types that flow through the internal event bus.

    Categories:
        DEVICE_*    Events about the full device state received from
                Dirigera at startup (initial discovery sync)
        STATE_*        Real-time state change events from Dirigera WebSocket
        AVAILABILITY_*    Device reachability change events
        COMMAND_*    Commands received from Home Assistant via MQTT
        CONNECTION_*    WebSocket or MQTT connection lifecycle events
    """

    # ── Device discovery (startup) ────────────────────────────────────────
    DEVICE_DISCOVERED = "DEVICE_DISCOVERED"
    DEVICE_REMOVED = "DEVICE_REMOVED"

    # ── Real-time state changes (WebSocket stream) ────────────────────────
    STATE_CHANGED = "STATE_CHANGED"

    # ── Availability ──────────────────────────────────────────────────────
    DEVICE_REACHABLE = "DEVICE_REACHABLE"
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"

    # ── Commands from HA (MQTT inbound) ───────────────────────────────────
    COMMAND_RECEIVED = "COMMAND_RECEIVED"

    # ── Connection lifecycle ──────────────────────────────────────────────
    DIRIGERA_CONNECTED = "DIRIGERA_CONNECTED"
    DIRIGERA_DISCONNECTED = "DIRIGERA_DISCONNECTED"
    MQTT_CONNECTED = "MQTT_CONNECTED"
    MQTT_DISCONNECTED = "MQTT_DISCONNECTED"


# ── Event dataclass ───────────────────────────────────────────────────────────


@dataclass
class DirigeraEvent:
    """
    Single event type that flows through the internal event bus.

    Every event published on the bus is a DirigeraEvent regardless of
    type. Consumers inspect event_type to decide whether to act, and
    read data for the event-specific payload.

    Args:
        event_type (EventType):
            The category of this event.

        logical_id (str):
            The Dirigera logical device id (e.g. "fff75d00-..._1").
            Always present except for CONNECTION_* events where it
            is set to an empty string.

        data (dict):
            Event-specific payload. Content depends on event_type:

            STATE_CHANGED:
                {"attribute": str, "value": Any}
                The attribute name that changed and its new value.

            DEVICE_DISCOVERED:
                Full raw Dirigera device dict as returned by REST API.

            DEVICE_REACHABLE / DEVICE_UNREACHABLE:
                {"is_reachable": bool}

            COMMAND_RECEIVED:
                {"topic": str, "payload": str}
                The raw MQTT command topic and payload string.

            DIRIGERA_CONNECTED / DIRIGERA_DISCONNECTED:
                {} (empty — the event_type alone carries the meaning)

            MQTT_CONNECTED / MQTT_DISCONNECTED:
                {} (empty)

        relation_id (str):
            The Dirigera physical device relation id. Set to the same
            value as logical_id for single-deviceType devices, and to
            the shared relationId for multi-deviceType devices.
            Empty string for CONNECTION_* events.
    """

    event_type: EventType
    logical_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    relation_id: str = ""

    def __post_init__(self) -> None:
        """Validate fields immediately after construction."""

        if not isinstance(self.event_type, EventType):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraEvent.event_type must be EventType, "
                f"got {type(self.event_type).__name__}",
            )

        if not isinstance(self.logical_id, str):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraEvent.logical_id must be str, "
                f"got {type(self.logical_id).__name__}",
            )

        if not isinstance(self.data, dict):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraEvent.data must be dict, got {type(self.data).__name__}",
            )

        if not isinstance(self.relation_id, str):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraEvent.relation_id must be str, "
                f"got {type(self.relation_id).__name__}",
            )


# ── Event bus ─────────────────────────────────────────────────────────────────


class AsyncEventBus:
    """
    Async in-process publish/subscribe event bus.

    Handlers are async callables registered per EventType. When an
    event is published all handlers for that type are called
    concurrently via asyncio.gather(). Handler errors are caught and
    logged individually — one failing handler never blocks the others.

    Instantiate once and inject into all components that need it.
    The bus itself holds no application state beyond the registry.
    """

    def __init__(self) -> None:
        # Registry: EventType → list of async handlers
        self._handlers: Dict[EventType, List[AsyncHandler]] = {
            event_type: [] for event_type in EventType
        }
        logger.debug("AsyncEventBus initialised")

    # ── Public API ────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: EventType,
        handler: AsyncHandler,
    ) -> None:
        """
        Register an async handler for a specific event type.

        The same handler can be registered for multiple event types
        by calling subscribe() once per type. Registering the same
        handler for the same type twice has no effect (idempotent).

        Args:
            event_type (EventType): The event type to subscribe to.
            handler (AsyncHandler): Async callable with signature
                        async def handler(event: DirigeraEvent)

        Raises:
            DirigeraBridgeError: If event_type is not an EventType or
            handler is not callable.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(event_type, EventType):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"subscribe: event_type must be EventType, "
                f"got {type(event_type).__name__}",
            )

        if not callable(handler):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "subscribe: handler must be callable",
            )

        # ── Register (idempotent) ─────────────────────────────────────────
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(
                "Subscribed handler '%s' to event type '%s'",
                getattr(handler, "__name__", repr(handler)),
                event_type.value,
            )
        else:
            logger.debug(
                "Handler '%s' already subscribed to '%s' — skipping",
                getattr(handler, "__name__", repr(handler)),
                event_type.value,
            )

    def unsubscribe(
        self,
        event_type: EventType,
        handler: AsyncHandler,
    ) -> None:
        """
        Remove a previously registered handler for an event type.

        Safe to call even if the handler is not currently registered
        (no-op in that case).

        Args:
            event_type (EventType): The event type to unsubscribe from.
            handler (AsyncHandler): The handler to remove.

        Raises:
            DirigeraBridgeError: If event_type is not an EventType.
        """

        if not isinstance(event_type, EventType):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"unsubscribe: event_type must be EventType, "
                f"got {type(event_type).__name__}",
            )

        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
            logger.debug(
                "Unsubscribed handler '%s' from event type '%s'",
                getattr(handler, "__name__", repr(handler)),
                event_type.value,
            )

    async def publish(self, event: DirigeraEvent) -> None:
        """
        Publish an event to all registered handlers for its type.

        All handlers for the event type are called concurrently using
        asyncio.gather(return_exceptions=True). Each handler error is
        caught, logged, and discarded — one failing handler never
        prevents others from receiving the event.

        This is a coroutine and must be awaited.

        Args:
            event (DirigeraEvent): The event to publish.

        Raises:
            DirigeraBridgeError: If event is not a DirigeraEvent instance.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(event, DirigeraEvent):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"publish: event must be DirigeraEvent, got {type(event).__name__}",
            )

        handlers = self._handlers.get(event.event_type, [])

        if not handlers:
            logger.debug(
                "No handlers for event type '%s' (logical_id=%s) — skipping",
                event.event_type.value,
                event.logical_id,
            )
            return

        logger.debug(
            "Publishing '%s' to %d handler(s) (logical_id=%s)",
            event.event_type.value,
            len(handlers),
            event.logical_id,
        )

        # ── Dispatch concurrently ─────────────────────────────────────────
        results = await asyncio.gather(
            *[handler(event) for handler in handlers],
            return_exceptions=True,
        )

        # ── Log any handler errors without re-raising ─────────────────────
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    "Handler '%s' raised an error for event '%s' (logical_id=%s): %s",
                    getattr(handler, "__name__", repr(handler)),
                    event.event_type.value,
                    event.logical_id,
                    result,
                )

    def publish_nowait(self, event: DirigeraEvent) -> None:
        """
        Schedule an event for publishing without awaiting completion.

        Creates an asyncio Task so the caller does not need to await.
        Use this in non-async contexts or fire-and-forget scenarios.
        The task is named for debuggability.

        Args:
            event (DirigeraEvent): The event to publish.

        Raises:
            DirigeraBridgeError: If event is not a DirigeraEvent instance.
        """

        if not isinstance(event, DirigeraEvent):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"publish_nowait: event must be DirigeraEvent, "
                f"got {type(event).__name__}",
            )

        asyncio.create_task(
            self.publish(event),
            name=f"event-{event.event_type.value}-{event.logical_id}",
        )

        logger.debug(
            "Scheduled publish_nowait for '%s' (logical_id=%s)",
            event.event_type.value,
            event.logical_id,
        )

    def subscriber_count(self, event_type: EventType) -> int:
        """
        Return the number of handlers registered for an event type.

        Primarily used in tests and health checks.

        Args:
            event_type (EventType): The event type to query.

        Returns:
            int: Number of registered handlers.

        Raises:
            DirigeraBridgeError: If event_type is not an EventType.
        """

        if not isinstance(event_type, EventType):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"subscriber_count: event_type must be EventType, "
                f"got {type(event_type).__name__}",
            )

        return len(self._handlers[event_type])

    def clear(self, event_type: Optional[EventType] = None) -> None:
        """
        Remove all handlers, either for a specific event type or all.

        Primarily used in tests to reset state between test cases.

        Args:
            event_type (EventType | None): If provided, clears only
            handlers for that type. If None, clears all handlers
            for all event types.
        """

        if event_type is not None:
            if not isinstance(event_type, EventType):
                raise DirigeraBridgeError(
                    ErrorCode.INTERNAL_INVALID_ARGUMENT,
                    f"clear: event_type must be EventType or None, "
                    f"got {type(event_type).__name__}",
                )
            self._handlers[event_type].clear()
            logger.debug("Cleared handlers for '%s'", event_type.value)
        else:
            for et in EventType:
                self._handlers[et].clear()
            logger.debug("Cleared all event bus handlers")
