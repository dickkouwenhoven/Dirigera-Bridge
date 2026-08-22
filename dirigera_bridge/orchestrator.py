"""
orchestrator.py

Service orchestrator — wires all layers together and manages the
full lifecycle of the Dirigera MQTT Bridge.

Role & Responsibility:
    The orchestrator is the only module in the application that imports
    from all four layers (Dirigera, mapping, HA, core). It owns the
    startup sequence, shutdown sequence, event routing, and the
    periodic tasks (metrics logging).

    Every other module has a single, narrow responsibility. The
    orchestrator is the glue — it creates, connects, and coordinates.

What it does:
    Startup sequence:
        1. Transition lifecycle: CREATED → STARTING
        2. Subscribe to all event bus events
        3. Connect HAClient (MQTT) with retry
        4. Connect DirigeraWebSocketClient with retry
        5. Fetch all devices via DirigeraRestClient.get_devices()
        6. Build DeviceContexts via device_registry
        7. Map DeviceContexts to entities via DeviceMapper
        8. Register all entities via HAClient
        9. Prime state cache with current device state
        10. Transition lifecycle: STARTING → RUNNING
        11. Start periodic metrics logging task

    Runtime event routing:
        STATE_CHANGED       → state_mapper → ha_client.update_state_direct()
        DEVICE_DISCOVERED   → re-fetch devices, map, register new ones
        DEVICE_REMOVED      → unregister from caches, mark offline
        DEVICE_REACHABLE    → mark device entities online
        DEVICE_UNREACHABLE  → mark device entities offline
        DIRIGERA_CONNECTED  → only acted on when recovering from
                              RECONNECTING (re-discover devices,
                              transition RUNNING); a no-op during the
                              initial connection at startup, since
                              _startup() already owns that path —
                              see _on_dirigera_connected()
        DIRIGERA_DISCONNECTED → mark all offline, transition RECONNECTING

    Shutdown sequence:
        1. Transition lifecycle → STOPPING
        2. Cancel metrics task
        3. Set all entities offline
        4. Stop WebSocket client
        5. Stop HAClient (MQTT)
        6. Close REST client
        7. Log final metrics snapshot
        8. Transition lifecycle → STOPPED

Arguments / Configuration:
    All dependencies are injected — the orchestrator never imports
    config directly, never creates network connections itself, and
    never instantiates its own dependencies.

Used by:
    - main.py  (creates all dependencies, passes to Orchestrator,
                calls run())

Not responsible for:
    - Creating dependencies (main.py does that)
    - Configuration loading (config.py)
    - Any network I/O directly (delegates to ws_client, rest_client,
      ha_client)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from _collections_abc import Awaitable, Callable
from typing import Any

from ha_mqtt_sdk import Entity

from .config import Settings
from .core.discovery_cache import DiscoveryCache
from .core.errors import DirigeraBridgeError, ErrorCode
from .core.event_bus import AsyncEventBus, DirigeraEvent, EventType
from .core.lifecycle import LifecycleState, ServiceLifecycle
from .core.metrics import MetricName, MetricsStore
from .core.state_cache import StateCache
from .dirigera.models import DirigeraDevice
from .dirigera.rest_client import DirigeraRestClient
from .dirigera.websocket_client import DirigeraWebSocketClient
from .ha.ha_client import HAClient
from .mapping.command_mapper import CommandMapper
from .mapping.device_mapper import DeviceMapper
from .mapping.device_registry import DeviceContext, build_device_contexts
from .mapping.state_mapper import StateMapper

__all__ = [
    "Orchestrator",
]

logger = logging.getLogger(__name__)

# Type alias for per-device command callbacks
_CommandCallback = Callable[[str, str], Awaitable[None]]


# noinspection GrazieStyle
class Orchestrator:
    """
    Wires all application layers together and drives the service
    lifecycle from startup through runtime to shut down.

    Args:
        settings        (Settings):                 Application config.
        event_bus       (AsyncEventBus):            Internal event bus.
        lifecycle       (ServiceLifecycle):         Lifecycle FSM.
        metrics         (MetricsStore):             Metrics store.
        state_cache     (StateCache):               Device state cache.
        discovery_cache (DiscoveryCache):           Entity discovery cache.
        ha_client       (HAClient):                 MQTT/HASDK wrapper.
        ws_client       (DirigeraWebSocketClient):  WebSocket client.
        rest_client     (DirigeraRestClient):       REST API client.
        device_mapper   (DeviceMapper):             Device → entity mapper.
        state_mapper    (StateMapper):              State translator.
        command_mapper  (CommandMapper):            Command translator.
    """

    def __init__(
        self,
        settings: Settings,
        event_bus: AsyncEventBus,
        lifecycle: ServiceLifecycle,
        metrics: MetricsStore,
        state_cache: StateCache,
        discovery_cache: DiscoveryCache,
        ha_client: HAClient,
        ws_client: DirigeraWebSocketClient,
        rest_client: DirigeraRestClient,
        device_mapper: DeviceMapper,
        state_mapper: StateMapper,
        command_mapper: CommandMapper,
    ) -> None:

        self._settings = settings
        self._event_bus = event_bus
        self._lifecycle = lifecycle
        self._metrics = metrics
        self._state_cache = state_cache
        self._discovery_cache = discovery_cache
        self._ha_client = ha_client
        self._ws_client = ws_client
        self._rest_client = rest_client
        self._device_mapper = device_mapper
        self._state_mapper = state_mapper
        self._command_mapper = command_mapper

        # Registered entities: unique_id → Entity
        self._entities: dict[str, Entity] = {}

        # Background task handle
        self._metrics_task: asyncio.Task[None] | None = None

        # Dedicated tracking dict
        self._last_reachable: dict[str, bool] = {}

        logger.debug("Orchestrator initialised")

    # ── Public API ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Start the bridge service and run until stop() is called.

        Main entry point called by main.py. Performs the full startup
        sequence then waits until the lifecycle reaches a terminal state.

        Raises:
            DirigeraBridgeError: LIFECYCLE_STARTUP_FAILED if startup
                                 fails fatally.
        """

        try:
            await self._startup()
            await self._run_until_stopped()

        except asyncio.CancelledError:
            logger.info("Orchestrator: run() cancelled — shutting down")

        except DirigeraBridgeError as exc:
            logger.exception("Orchestrator: fatal error during run: %s", exc)
            if self._lifecycle.can_transition(LifecycleState.FAILED):
                await self._lifecycle.transition(
                    LifecycleState.FAILED,
                    reason=str(exc),
                )

        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """
        Request a graceful shutdown of the bridge service.

        Transitions the lifecycle to STOPPING which causes the
        run() wait loop to exit and proceed to _shutdown().
        """

        logger.info("Orchestrator: stop requested")

        if self._lifecycle.can_transition(LifecycleState.STOPPING):
            await self._lifecycle.transition(
                LifecycleState.STOPPING,
                reason="stop() called",
            )

    # ── Startup ───────────────────────────────────────────────────────────

    async def _startup(self) -> None:
        """
        Execute the full startup sequence.

        Raises:
            DirigeraBridgeError: On any fatal startup failure.
        """

        logger.info("Orchestrator: starting up")

        # ── Step 1: CREATED → STARTING ────────────────────────────────────
        await self._lifecycle.transition(
            LifecycleState.STARTING,
            reason="startup initiated",
        )

        # ── Step 2: subscribe to eventbus ────────────────────────────────
        self._subscribe_events()

        # ── Step 3: connect MQTT ──────────────────────────────────────────
        logger.info("Orchestrator: connecting to MQTT broker")
        await self._ha_client.connect()

        # ── Step 4: connect Dirigera WebSocket ────────────────────────────
        logger.info("Orchestrator: connecting to Dirigera WebSocket")
        await self._ws_client.connect()

        # ── Step 5–9: fetch devices, map, register, prime cache ───────────
        await self._discover_and_register_devices()

        # ── Step 10: STARTING → RUNNING ───────────────────────────────────
        if self._lifecycle.can_transition(LifecycleState.RUNNING):
            await self._lifecycle.transition(
                LifecycleState.RUNNING,
                reason="startup complete",
            )
        else:
            # Defensive only — should not happen now that
            # _on_dirigera_connected() no longer reacts to the initial
            # connection during startup. Logged rather than raised so
            # a future regression degrades to a warning instead of a
            # full crash-loop.
            logger.warning(
                "Orchestrator: expected to transition to RUNNING at end "
                "of startup, but current state is '%s' — skipping",
                self._lifecycle.current_state.value,
            )

        # ── Step 11: start metrics loop ───────────────────────────────────
        self._metrics_task = asyncio.create_task(
            self._metrics_loop(),
            name="metrics-loop",
        )

        logger.info(
            "Orchestrator: startup complete — bridge is RUNNING. Supported device types: %s",
            self._device_mapper.supported_device_types(),
        )

    async def _discover_and_register_devices(self) -> None:
        """
        Fetch all devices from Dirigera, map to HA entities, register
        them, and prime the state cache.
        """

        logger.info("Orchestrator: fetching device list from Dirigera")

        # ── Fetch ─────────────────────────────────────────────────────────
        try:
            devices = await self._rest_client.get_devices()
        except DirigeraBridgeError as exc:
            raise DirigeraBridgeError(
                ErrorCode.LIFECYCLE_STARTUP_FAILED,
                f"Failed to fetch devices from Dirigera: {exc}",
                cause=exc,
            ) from exc

        # ── Build DeviceContexts ──────────────────────────────────────────
        regular_contexts, gateway_contexts = build_device_contexts(devices)
        all_contexts = regular_contexts + gateway_contexts

        logger.info(
            "Orchestrator: %d regular + %d gateway device(s) to process",
            len(regular_contexts),
            len(gateway_contexts),
        )

        # ── Map and register ──────────────────────────────────────────────
        for context in all_contexts:
            await self._register_context(context)

        # ── Publish initial state ─────────────────────────────────────────────
        await self._publish_initial_state(devices)

        logger.info(
            "Orchestrator: device discovery complete — %d entity(ies) registered",
            len(self._entities),
        )

    async def _register_context(self, context: DeviceContext) -> None:
        """
        Map a DeviceContext to entities and register each in HA.

        Args:
            context (DeviceContext): Normalised device context.
        """

        entities = self._device_mapper.map_device(context)

        if not entities:
            logger.debug(
                "Orchestrator: no entities for '%s' (device_type=%s) — skipping",
                context.device_name,
                context.device_type,
            )
            return

        for entity in entities:
            try:
                await self._ha_client.register_entity(
                    entity=entity,
                    command_callback=self._make_command_callback(
                        logical_id=context.logical_id,
                        device_type=context.device_type,
                    ),
                )

                # Store registered entity for state update routing
                self._entities[entity.unique_id] = entity

                # Set initial availability
                await self._ha_client.update_availability(
                    entity,
                    online=context.is_reachable,
                )

            except DirigeraBridgeError as exc:
                logger.error(
                    "Orchestrator: failed to register entity '%s': %s",
                    entity.unique_id,
                    exc,
                )

    async def _publish_initial_state(
        self,
        devices: list[DirigeraDevice],
    ) -> None:
        """
        Publish every device's current REST-discovered attributes to
        HA, once, at startup.

        Replaces the old _prime_state_chache(), which only wrote
        values into the state cache silently - HA would then only
        ever learn a value once a live WebSocket event happened to
        arrive for it. For fast-changing attributes (e.q.
        illuminance) this self-corrected within seconds and was
        invisible. For anything slow-changing or purely event-driven
        (motion, water leak, battery percentage - which can go days
        between real changes), HA was left showing nothing at all
        until the first real-world change happened to occur after
        the bridge started - confirmed from real HA MQTT debug panels
        showing exactly 0 state messages received for these entities
        despite correct registration and availability.

        Also, explicitly publishes each device's own is_reachable
        field, separate from the raw_attributes loop below -
        isReachable is a top-level sibling field on Dirigera's device
        JSON, never nested inside "attributes" (confirmed from real
        captured REST payloads: {'id': ..., 'isReachable': False,
        ..., 'attributes': {...}}), so the ordinary loop never covers
        it. Some domains (gateway, speaker) expose this as their own
        dedicated "_reachable" sub-entity - without this, that
        sub-entity had no value source at all until a live
        DEVICE_REACHABLE/UNREACHABLE event happened to fire, which
        for some devices (e.g. the gateway reporting its own
        reachability to itself) may never actually happen over the
        WebSocket at all.

        Reuses _process_attribute_change(..., force=True) - the same
        dedup-bypass path already proven correct for
        _on_device_reachable()'s reconnect refresh - so this needs no
        new publish logic, and each attribute is still individually
        mapped and dropped correctly if unmapped/internal
        (map_state() returning None), exactly as during live
        operation.

        Uses the devices list already fetched by
        _discover_and_register_devices() (no extra REST calls), and
        runs AFTER registration so self._entities looks up succeed.

        Args:
            devices: Raw device list from rest_client.get_devices().
        """

        total_attrs = 0
        for device in devices:
            await self._process_attribute_change(
                logical_id=device.id,
                device_type=device.device_type,
                attribute="isReachable",
                value=device.is_reachable,
                force=True,
            )

            for attr, value in device.raw_attributes.items():
                await self._process_attribute_change(
                    logical_id=device.id,
                    device_type=device.device_type,
                    attribute=attr,
                    value=value,
                    force=True,
                )
                total_attrs += 1

        logger.info(
            "Orchestrator: initial state published - %d attribute(s) across %d device(s)",
            total_attrs,
            len(devices),
        )

    # ── Event subscriptions ───────────────────────────────────────────────

    def _subscribe_events(self) -> None:
        """Subscribe all orchestrator handlers to the internal event bus."""

        subscriptions = [
            (EventType.STATE_CHANGED, self._on_state_changed),
            (EventType.DEVICE_DISCOVERED, self._on_device_discovered),
            (EventType.DEVICE_REMOVED, self._on_device_removed),
            (EventType.DEVICE_REACHABLE, self._on_device_reachable),
            (EventType.DEVICE_UNREACHABLE, self._on_device_unreachable),
            (EventType.DIRIGERA_CONNECTED, self._on_dirigera_connected),
            (EventType.DIRIGERA_DISCONNECTED, self._on_dirigera_disconnected),
        ]

        for event_type, handler in subscriptions:
            self._event_bus.subscribe(event_type, handler)

        logger.debug(
            "Orchestrator: %d event bus subscription(s) registered",
            len(subscriptions),
        )

    # ── Event handlers ────────────────────────────────────────────────────

    async def _on_state_changed(self, event: DirigeraEvent) -> None:
        """
        Handle STATE_CHANGED from the Dirigera WebSocket.

        Most attributes follow the normal map-and-publish path via
        _process_attribute_change(). The special attribute
        'isReachable' indicates that a device has become reachable or
        unreachable and requires orchestration logic instead of a normal
        Home Assistant state update.
        """

        attribute = event.data.get("attribute", "")
        value = event.data.get("value")

        # Device reachability changed
        if attribute == "isReachable":
            if value:
                await self._on_device_reachable(event)
            else:
                await self._on_device_unreachable(event)
            return

        # Normal attribute change
        await self._process_attribute_change(
            logical_id=event.logical_id,
            device_type=event.data.get("device_type", ""),
            attribute=attribute,
            value=value,
        )

    async def _on_device_discovered(self, event: DirigeraEvent) -> None:
        """
        Handle DEVICE_DISCOVERED — new device paired to the hub.

        Re-fetches the full device list and registers any new devices.
        Simpler and more reliable than parsing the partial WebSocket
        event payload.
        """

        logger.info(
            "Orchestrator: new device discovered (logical_id=%s) — re-fetching device list",
            event.logical_id,
        )

        try:
            await self._discover_and_register_devices()
        except DirigeraBridgeError as exc:
            logger.error(
                "Orchestrator: re-discovery after new device failed: %s",
                exc,
            )

    async def _on_device_removed(self, event: DirigeraEvent) -> None:
        """
        Handle DEVICE_REMOVED — device unpaired from the hub.

        Marks entities offline and removes them from both caches.
        """

        logical_id = event.logical_id

        logger.info(
            "Orchestrator: device removed (logical_id=%s)",
            logical_id,
        )

        # ── Mark affected entities offline ────────────────────────────────
        for unique_id, entity in list(self._entities.items()):
            if logical_id in unique_id:
                try:
                    await self._ha_client.update_availability(entity, online=False)
                except DirigeraBridgeError as exc:
                    logger.warning(
                        "Orchestrator: failed to mark entity offline after removal: %s",
                        exc,
                    )

        # ── Clean up caches ───────────────────────────────────────────────
        self._state_cache.clear_device(logical_id)
        self._discovery_cache.unregister(logical_id)

    async def _on_device_unreachable(self, event: DirigeraEvent) -> None:
        """
        Handle DEVICE_UNREACHABLE — mark device entities offline and
        republish isReachable for domains with a dedicated
        "_reachable" sub-entity (see _on_device_reachable()'s
        docstring)."""
        logger.info("DEVICE_UNREACHABLE: %s", event.logical_id)
        await self._set_device_availability(event.logical_id, online=False)

        device_type = event.data.get("device_type", "") if event.data else ""

        await self._process_attribute_change(
            logical_id=event.logical_id,
            device_type=device_type,
            attribute="isReachable",
            value=False,
        )

        self._last_reachable[event.logical_id] = False

    async def _on_dirigera_connected(self, _event: DirigeraEvent) -> None:
        """
        Handle DIRIGERA_CONNECTED.

        This event fires in two genuinely different situations that
        the websocket client itself cannot distinguish (it has no
        lifecycle visibility, by design — see module docstring):
            1. The very first connection during normal startup.
               _startup() already owns discovery/registration and the
               STARTING -> RUNNING transition for this case, via its
               own sequential flow.
            2. The connection being restored after a real outage
               (lifecycle was RECONNECTING). This is the only case
               that needs action here.

        Reacting to case 1 as well-used to race _startup()'s own
        sequential registration pass — both would fetch and register
        the same devices concurrently, causing "already registered"
        errors, and _startup()'s own final STARTING -> RUNNING
        transition would then fail since the event handler had
        already (incorrectly) made that transition first.
        """

        if self._lifecycle.current_state != LifecycleState.RECONNECTING:
            logger.debug(
                "Orchestrator: Dirigera WebSocket connected during startup "
                "— no action needed here, _startup() owns this path"
            )
            return

        logger.info("Orchestrator: Dirigera WebSocket reconnected after outage")

        if self._lifecycle.can_transition(LifecycleState.RUNNING):
            await self._lifecycle.transition(
                LifecycleState.RUNNING,
                reason="Dirigera reconnected",
            )

        try:
            await self._discover_and_register_devices()
        except DirigeraBridgeError as exc:
            logger.error(
                "Orchestrator: re-discovery after reconnect failed: %s",
                exc,
            )

    async def _on_dirigera_disconnected(self, _event: DirigeraEvent) -> None:
        """
        Handle DIRIGERA_DISCONNECTED — WebSocket connection lost.

        Marks all entities unavailable in HA and transitions to
        RECONNECTING. The WebSocket client handles the actual reconnect
        loop — the orchestrator only reacts to the outcome.
        """

        logger.warning("Orchestrator: Dirigera WebSocket disconnected")

        if self._lifecycle.can_transition(LifecycleState.RECONNECTING):
            await self._lifecycle.transition(
                LifecycleState.RECONNECTING,
                reason="Dirigera disconnected",
            )

        await self._ha_client.set_all_offline(list(self._entities.values()))

    # ── Command handling ──────────────────────────────────────────────────

    def _make_command_callback(
        self,
        logical_id: str,
        device_type: str,
    ) -> _CommandCallback:
        """
        Create a per-device async command callback.

        The callback is registered with ha_client and called when HA
        sends a command on the entity's command_topic.

        Args:
            logical_id (str):  Dirigera logical device id.
            device_type (str): Dirigera deviceType for command routing.

        Returns:
            Async callable with signature (topic: str, payload: str).
        """

        async def _handle_command(topic: str, payload: str) -> None:

            logger.debug(
                "Orchestrator: command received for %s (device_type=%s, payload=%r)",
                logical_id,
                device_type,
                topic,
                payload[:80],
            )

            # ── Map to Dirigera payload ───────────────────────────────────
            cmd = self._command_mapper.map_command(
                logical_id=logical_id,
                device_type=device_type,
                command_payload=payload,
            )

            if cmd is None:
                logger.debug(
                    "Orchestrator: command not translatable for %s",
                    logical_id,
                )
                return

            logger.info("Send_command: logical_id=%s attributes=%s", logical_id, cmd.attributes)
            # ── Send to Dirigera REST API ─────────────────────────────────
            try:
                await self._rest_client.send_command(
                    logical_id=cmd.logical_id,
                    attributes=cmd.attributes,
                )
                self._metrics.increment(MetricName.MAPPING_COMMANDS_TRANSLATED)

            except DirigeraBridgeError as exc:
                logger.error(
                    "Orchestrator: Dirigera REST command failed for %s: %s",
                    logical_id,
                    exc,
                )
                self._metrics.increment(MetricName.ERROR_REST)

        return _handle_command

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _set_device_availability(
        self,
        logical_id: str,
        online: bool,
    ) -> None:
        """
        Set availability for all entities belonging to a logical device.

        Args:
            logical_id (str): Dirigera logical device id.
            online (bool):    True = online, False = offline.
        """
        logical_id = logical_id.replace("-", "_")
        for unique_id, entity in self._entities.items():
            logger.info("logical_id is: %s", logical_id)
            logger.info("unique_id  is: %s", unique_id)
            if logical_id in unique_id:
                logger.info(
                    "Availability: %s -> %s",
                    unique_id,
                    "online" if online else "offline",
                )
                try:
                    await self._ha_client.update_availability(entity, online=online)
                except DirigeraBridgeError as exc:
                    logger.warning(
                        "Orchestrator: availability update failed for '%s': %s",
                        unique_id,
                        exc,
                    )

    # ── Periodic tasks ────────────────────────────────────────────────────

    async def _metrics_loop(self) -> None:
        """
        Periodically emit a metrics snapshot to the log.

        Runs every setting.metrics_interval seconds until the
        lifecycle signals stopping.
        """

        interval = self._settings.metrics_interval

        logger.debug(
            "Orchestrator: metrics loop started (interval=%ds)",
            interval,
        )

        while not self._lifecycle.is_stopping():
            await asyncio.sleep(interval)

            if self._lifecycle.is_stopping():
                break

            logger.info(
                "Orchestrator: metrics snapshot (state=%s, entities=%d)",
                self._lifecycle.current_state.value,
                len(self._entities),
            )
            self._metrics.log_snapshot()

    # ── Run loop ──────────────────────────────────────────────────────────

    async def _run_until_stopped(self) -> None:
        """
        Wait until the lifecycle reaches a terminal state.

        Polls every second. In production this loop exits when stop()
        is called and the lifecycle transitions to STOPPED via _shutdown().
        """

        while not self._lifecycle.is_terminal():
            await asyncio.sleep(1)

    # ── Shutdown ──────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        """
        Execute the full shutdown sequence.

        Always called by run() in its finally block — shutdown happens
        even if startup raised. Safe to call multiple times.
        """

        logger.info("Orchestrator: shutting down")

        # ── Transition to STOPPING if not already ─────────────────────────
        if self._lifecycle.can_transition(LifecycleState.STOPPING):
            await self._lifecycle.transition(
                LifecycleState.STOPPING,
                reason="shutdown initiated",
            )

        # ── Cancel metrics task ───────────────────────────────────────────
        if self._metrics_task and not self._metrics_task.done():
            self._metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._metrics_task

        # ── Mark all entities offline ─────────────────────────────────────
        if self._entities:
            logger.info(
                "Orchestrator: marking %d entity(ies) offline",
                len(self._entities),
            )
            await self._ha_client.set_all_offline(list(self._entities.values()))

        # ── Stop WebSocket client ─────────────────────────────────────────
        try:
            await self._ws_client.stop()
        except Exception as exc:
            logger.warning("Orchestrator: error stopping WebSocket client: %s", exc)

        # ── Stop MQTT / HAClient ──────────────────────────────────────────
        try:
            await self._ha_client.stop()
        except Exception as exc:
            logger.warning("Orchestrator: error stopping HAClient: %s", exc)

        # ── Close REST client ─────────────────────────────────────────────
        try:
            await self._rest_client.close()
        except Exception as exc:
            logger.warning("Orchestrator: error closing REST client: %s", exc)

        # ── Final metrics snapshot ────────────────────────────────────────
        logger.info("Orchestrator: final metrics snapshot:")
        self._metrics.log_snapshot(include_zeros=False)

        # ── STOPPING → STOPPED ────────────────────────────────────────────
        if self._lifecycle.can_transition(LifecycleState.STOPPED):
            await self._lifecycle.transition(
                LifecycleState.STOPPED,
                reason="shutdown complete",
            )

        logger.info("Orchestrator: shutdown complete")

    async def _on_device_reachable(self, event: DirigeraEvent) -> None:
        """
        Handle DEVICE_REACHABLE - mark device entities online AND
        refresh their real current state from Dirigera.

        Marking availability alone isn't enough: a device that lost
        and regained power (e.g. a light on a wall switch) can come
        back with a real physical state that differs from whatever
        Dirigera last had cached before the outage - and if
        Dirigera's own cached isOn value happens to already match,
        it never emits a fresh WebSocket event, so HA would otherwise
        show the stale value indefinitely. This matches observed
        real behavior: toggling the device via the IKEA app (which
        DOES force a genuine change on Dirigera's side) was the only
        thing that got HA to catch up.

        Only THIS device is re-fetched (GET /v1/devices/{id}), not
        the whole fleet - see rest_client.get_device()'s docstring.

        Each attribute goes through _process_attribute_change(), the
        same dedup-and-publish path as a live WebSocket event, so a
        reconnect where nothing really changed produces no MQTT
        traffic at all.

        Only refreshes state on a genuine offline -> online edge, not
        every message where Dirigera includes isReachable=true
        alongside an ordinary change (confirmed from real captured
        traffic that this rides along with unrelated isOn changes
        routinely, not just reconnects) - otherwise every single
        toggle while already online would trigger a redundant REST
        fetch and duplicate publish on top of the normal
        STATE_CHANGED-driven one for the same event.

        Also republishes isReachable as an ordinary attribute via
        _process_attribute_change() (normal, non-forced dedup) - some
        domains (gateway, speaker) expose their own dedicated
        "_reachable" binary_sensor sub-entity, separate from the
        availability_topic greyout, and that sub-entity's value comes
        from this same isReachable signal. This was broken when
        isReachable was removed from the generic STATE_CHANGED
        attribute loop to fix availability - confirmed from real HA
        MQTT debug data showing zero state messages ever received for
        the gateway's Reachable entity after that fix landed.

        Edge-detection for the REST refresh below uses its own
        _last_reachable dict, NOT state_cache, specifically to avoid
        colliding with the ordinary dedup used by the
        _process_attribute_change() call just above it - both would
        otherwise consume the same "did isReachable change" signal.
        """
        logger.info("DEVICE_REACHABLE: %s", event.logical_id)
        await self._set_device_availability(event.logical_id, online=True)

        device_type = event.data.get("device_type", "") if event.data else ""

        await self._process_attribute_change(
            logical_id=event.logical_id,
            device_type=device_type,
            attribute="isReachable",
            value=True,
        )

        already_reachable = self._last_reachable.get(event.logical_id) is True
        self._last_reachable[event.logical_id] = True
        if already_reachable:
            logger.debug(
                "Orchestrator: %s already reachable - skipping refresh",
                event.logical_id,
            )
            return

        try:
            device = await self._rest_client.get_device(event.logical_id)
            logger.info(
                "REST refresh: %s attributes=%s",
                event.logical_id,
                device.raw_attributes,
            )
        except DirigeraBridgeError as exc:
            logger.warning(
                "Orchestrator: could not refresh state for reconnected device %s: %s",
                event.logical_id,
                exc,
            )
            return

        logger.info("raw_attributes contains %d items", len(device.raw_attributes))
        logger.info("Entering refresh loop")
        for attribute, value in device.raw_attributes.items():
            await self._process_attribute_change(
                logical_id=event.logical_id,
                device_type=device.device_type,
                attribute=attribute,
                value=value,
                force=True,
            )

    async def _process_attribute_change(
        self,
        logical_id: str,
        device_type: str,
        attribute: str,
        value: Any,
        force: bool = False,
    ) -> None:
        """
        Dedupe an attribute value against the cache, map it to an HA
        payload, and publish it — only if the value actually changed.

        Extracted from _on_state_changed() so the exact same logic
        can be reused by _on_device_reachable()'s reconnect refresh:
        looping this per-attribute over a freshly re-fetched device
        means a reconnect only republishes what's genuinely different
        from what HA already has (via the state_cache dedup below),
        not the device's unchanged fields and not every other device.

        force: when True, always maps and publishes regardless of
        what the cache thinks changed - still updates the cache
        either way. Used by _on_device_reachable()'s reconnect
        refresh: the cache only reflects what THIS bridge last wrote
        locally, not what HA actually received. Confirmed against
        real captured logs that reconnects REST-refreshed isOn
        value can exactly match the cache's stale value even though
        HA never received it - normal dedup would then wrongly
        suppress the exact publishing this refresh exists to send.
        """
        # ── Deduplication ─────────────────────────────────────────────────
        logger.info("PROCESS %s %s=%r", logical_id, attribute, value)
        changed = self._state_cache.set(logical_id, attribute, value)
        logger.info("DEDUP %s %s changed=%s force=%s", logical_id, attribute, changed, force)
        if not changed and not force:
            logger.debug(
                "Orchestrator: unchanged state for %s.%s = %r — skipping",
                logical_id,
                attribute,
                value,
            )
            return

        # ── Map to HA payload ─────────────────────────────────────────────
        state_payload = self._state_mapper.map_state(
            logical_id=logical_id,
            device_type=device_type,
            attribute=attribute,
            value=value,
            device_attributes=self._state_cache.get_device_state(logical_id),
        )
        logger.info(
            "MAPPER %s %s -> %r",
            logical_id,
            attribute,
            state_payload,
        )
        if state_payload is None:
            return  # Internal Dirigera attribute — do not forward

        # ── Publish to HA ─────────────────────────────────────────────────
        entity = self._entities.get(state_payload.unique_id)
        if entity is None:
            logger.debug(
                "Orchestrator: no entity for unique_id=%s",
                state_payload.unique_id,
            )
            return

        state_topic = self._ha_client.get_state_topic(entity)
        if not state_topic:
            logger.debug(
                "Orchestrator: no state_topic for unique_id=%s",
                state_payload.unique_id,
            )
            return
        logger.info(
            "MQTT %s payload=%s",
            state_payload.unique_id,
            state_payload.payload,
        )
        try:
            await self._ha_client.update_state_direct(
                state_topic=state_topic,
                payload=state_payload.payload,
            )
            self._metrics.increment(MetricName.MAPPING_STATE_UPDATES)
        except DirigeraBridgeError as exc:
            logger.error(
                "Orchestrator: failed to publish state for %s: %s",
                logical_id,
                exc,
            )
            self._metrics.increment(MetricName.ERROR_MQTT)
