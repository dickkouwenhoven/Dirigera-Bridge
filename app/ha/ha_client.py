"""
ha_client.py

Thin async wrapper around the AsyncHASDK.

Role & Responsibility:
    The single point of contact between the bridge and the HASDK.
    All MQTT interactions — entity registration, state updates,
    availability updates, and command subscriptions — go through
    this class. No other module in the application touches the HASDK
    directly.

    This module owns the MQTT connection lifecycle: connecting,
    reconnecting with exponential backoff, and disconnecting cleanly.

What it does:
    - Creates and manages an AsyncMQTTClient (HASDK transport)
    - Creates and manages an AsyncHASDK (entity lifecycle)
    - Connects to the MQTT broker using settings from config.py
    - Registers HA entities via MQTT discovery (retained publish)
    - Publishes state updates to entity state topics
    - Publishes availability (online/offline) to availability topics
    - Subscribes to command topics and routes incoming commands to
      a registered async callback
    - Reconnects using retry_with_backoff from core.retry on failure
    - Tracks MQTT metrics (connect, publish, subscribe, errors)

Arguments / Configuration:
    settings  (Settings):        Injected application settings. Reads
                                 all MQTT_* fields and DISCOVERY_PREFIX.
    metrics   (MetricsStore):    Injected metrics store.
    lifecycle (ServiceLifecycle): Injected lifecycle for shutdown guard.

Used by:
    - app/orchestrator.py  (creates client, calls connect(),
                            register_entity(), update_state(),
                            update_availability(), stop())

Not responsible for:
    - Entity creation (device_mapper.py builds Entity objects)
    - State translation (state_mapper.py builds payloads)
    - Command translation (command_mapper.py translates payloads)
    - Discovery or state caching (core/discovery_cache, state_cache)

Design notes:
    - The AsyncHASDK is constructed after connect()
      succeeds — not at __init__ time — because aiomqtt requires a
      running event loop and an active connection before the manager
      can subscribe to topics.
    - Command routing is owned entirely by AsyncHASDK /
      AsyncEntityManager. Constructing AsyncHASDK(async_mqtt_client=...)
      registers the SDK's own message callback on the MQTT client, and
      passing command_callback to sdk.register() wires the per-entity
      subscription and dispatch internally. HAClient must not call
      set_message_callback() itself — doing so overwrites the SDK's
      routing and breaks command handling entirely. Callbacks passed
      in here are wrapped only to add metrics/logging, then handed to
      sdk.register() unchanged in shape.
    - Availability is published as retained so HA immediately sees
      the device as offline if the bridge disconnects unexpectedly
      (via Last Will and Testament set during connect).
    - The discovery_cache check is performed here before registering
      an entity — if the entity was already registered in this session
      the registration is skipped to avoid duplicate MQTT publishes.
    - ha_domains list passed to discovery_cache.register() is derived
      from the entity.domain.value string (caller-validates pattern
      confirmed in architecture review).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from ha_mqtt_sdk import MQTTSettings
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import AsyncMQTTClient
from ha_mqtt_sdk import AsyncHASDK

from ..config import Settings
from ..core.discovery_cache import DiscoveryCache
from ..core.errors import DirigeraBridgeError, ErrorCode
from ..core.lifecycle import ServiceLifecycle
from ..core.metrics import MetricName, MetricsStore
from ..core.retry import RetryConfig, retry_with_backoff

__all__ = [
    "HAClient",
]

logger = logging.getLogger(__name__)

# Type alias for async command callback
CommandCallback = Callable[[str, str], Awaitable[None]]


class HAClient:
    """
    Async MQTT client for Home Assistant integration via HASDK.

    Manages the full MQTT connection lifecycle and exposes a clean
    interface for entity registration, state updates, availability,
    and command routing.

    Args:
        settings  (Settings):         Application settings.
        metrics   (MetricsStore):     Metrics store for counters.
        lifecycle (ServiceLifecycle): Lifecycle for shutdown detection.
        discovery_cache (DiscoveryCache): Cache of registered entities.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if any argument
                             is not the correct type.
    """

    def __init__(
        self,
        settings: Settings,
        metrics: MetricsStore,
        lifecycle: ServiceLifecycle,
        discovery_cache: DiscoveryCache,
    ) -> None:

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(settings, Settings):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"HAClient: settings must be Settings, got {type(settings).__name__}",
            )
        if not isinstance(metrics, MetricsStore):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"HAClient: metrics must be MetricsStore, got {type(metrics).__name__}",
            )
        if not isinstance(lifecycle, ServiceLifecycle):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"HAClient: lifecycle must be ServiceLifecycle, "
                f"got {type(lifecycle).__name__}",
            )
        if not isinstance(discovery_cache, DiscoveryCache):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"HAClient: discovery_cache must be DiscoveryCache, "
                f"got {type(discovery_cache).__name__}",
            )

        self._settings = settings
        self._metrics = metrics
        self._lifecycle = lifecycle
        self._discovery_cache = discovery_cache

        # HASDK objects — created in connect()
        self._mqtt_client: Optional[AsyncMQTTClient] = None
        self._sdk: Optional[AsyncHASDK] = None

        # Stop event for retry loop interruption
        self._stop_event: asyncio.Event = asyncio.Event()

        # Retry config for MQTT reconnection
        self._retry_config = RetryConfig(
            initial_delay=settings.reconnect_delay_initial,
            max_delay=settings.reconnect_delay_max,
            multiplier=2.0,
            jitter_max=1.0,
            max_attempts=None,
        )

        logger.debug(
            "HAClient initialised (broker=%s:%d, client_id=%s)",
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_client_id,
        )

    # ── Public API — lifecycle ────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Connect to the MQTT broker.

        Builds the AsyncMQTTClient and AsyncHASDK, then
        connects. Retries with exponential backoff on failure.

        Raises:
            DirigeraBridgeError: MQTT_CONNECTION_FAILED if the initial
                                 connection cannot be established after
                                 exhausting retries (retries are infinite
                                 by default — this only raises if the
                                 stop_event is set before connecting).
        """

        logger.info(
            "HAClient: connecting to MQTT broker at %s:%d",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )

        self._stop_event.clear()
        self._metrics.increment(MetricName.MQTT_CONNECT_ATTEMPTS)

        async for attempt in retry_with_backoff(
            self._retry_config,
            stop_event=self._stop_event,
        ):
            if self._lifecycle.is_stopping():
                logger.info("HAClient: lifecycle is stopping — aborting connect")
                return

            try:
                await self._build_and_connect()
                self._metrics.increment(MetricName.MQTT_CONNECT_SUCCESS)
                logger.info(
                    "HAClient: connected to MQTT broker (attempt %d)",
                    attempt,
                )
                return

            except Exception as exc:
                if attempt > 1:
                    self._metrics.increment(MetricName.MQTT_RECONNECT_ATTEMPTS)
                logger.warning(
                    "HAClient: MQTT connect attempt %d failed: %s",
                    attempt,
                    exc,
                )
                self._metrics.increment(MetricName.ERROR_MQTT)

    async def stop(self) -> None:
        """
        Disconnect from the MQTT broker and release resources.

        Sets the stop event to interrupt any ongoing retry loop, then
        disconnects the MQTT client cleanly. Safe to call even if
        connect() was never called.
        """

        logger.info("HAClient: stopping")

        self._stop_event.set()

        if self._mqtt_client is not None:
            try:
                await self._mqtt_client.disconnect()
                logger.info("HAClient: MQTT client disconnected")
            except Exception as exc:
                logger.warning("HAClient: error during disconnect: %s", exc)
            finally:
                self._mqtt_client = None
                self._sdk = None

        logger.info("HAClient: stopped")

    @property
    def is_connected(self) -> bool:
        """
        Return True if the MQTT client is currently connected.

        Returns:
            bool: True if connected.
        """

        return self._mqtt_client is not None and self._sdk is not None

    # ── Public API — entity operations ────────────────────────────────────

    async def register_entity(
        self,
        entity: Entity,
        command_callback: Optional[CommandCallback] = None,
    ) -> None:
        """
        Register an HA entity via MQTT discovery.

        Checks the discovery cache first — if the entity is already
        registered in this session the registration is skipped to avoid
        duplicate MQTT publishes.

        Also sets up the command subscription and callback for entities
        that support commands (e.g. lights, switches, blinds).

        Args:
            entity (Entity):              HASDK Entity to register.
            command_callback (callable):  Optional async callback called
                                          when HA sends a command for
                                          this entity. Signature:
                                          async def cb(topic, payload).

        Raises:
            DirigeraBridgeError: MQTT_REGISTRATION_FAILED if the MQTT
                                 client is not connected.
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if entity is
                                 not an Entity instance.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(entity, Entity):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"register_entity: entity must be Entity, got {type(entity).__name__}",
            )

        self._require_connected("register_entity")

        # ── Check discovery cache ─────────────────────────────────────────
        if self._discovery_cache.is_registered(entity.unique_id):
            logger.debug(
                "register_entity: '%s' already registered — skipping",
                entity.unique_id,
            )
            self._metrics.increment(MetricName.ENTITY_ALREADY_REGISTERED)
            return

        # ── Register via HASDK ────────────────────────────────────────────
        try:
            assert self._sdk is not None

            wrapped_callback: Optional[CommandCallback] = None
            if command_callback is not None:
                wrapped_callback = self._wrap_command_callback(
                    entity.unique_id, command_callback
                )

            # command_callback is handed straight to the SDK — it owns
            # the command_topic subscription and topic → callback
            # routing internally (via AsyncEntityManager). HAClient has
            # no business knowing about command topics at all.
            await self._sdk.register(entity, command_callback=wrapped_callback)

            # ── Update discovery cache ────────────────────────────────────
            self._discovery_cache.register(
                logical_id=entity.unique_id,
                relation_id=entity.unique_id,
                ha_domains=[entity.domain.value],
                device_name=entity.name,
            )

            self._metrics.increment(MetricName.ENTITY_REGISTERED)
            self._metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED)

            logger.info(
                "register_entity: registered '%s' (domain=%s)",
                entity.unique_id,
                entity.domain.value,
            )

        except DirigeraBridgeError:
            raise

        except Exception as exc:
            self._metrics.increment(MetricName.MQTT_PUBLISH_ERRORS)
            self._metrics.increment(MetricName.ERROR_MQTT)
            raise DirigeraBridgeError(
                ErrorCode.MQTT_REGISTRATION_FAILED,
                f"Failed to register entity '{entity.unique_id}': {exc}",
                cause=exc,
            )

    async def update_state(
        self,
        unique_id: str,
        state_topic: str,
        payload: str,
    ) -> None:
        """
        Publish a state update to an entity's MQTT state topic.

        Args:
            unique_id (str): The entity's unique_id (used to look up
                             the Entity in the HASDK manager).
            payload (str):   The state payload string to publish.

        Raises:
            DirigeraBridgeError: MQTT_PUBLISH_FAILED if publish fails.
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if unique_id
                                 is not a non-empty string.
                                 :param payload:
                                 :param unique_id:
                                 :param state_topic:
        """

        if not isinstance(unique_id, str) or not unique_id.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "update_state: unique_id must be a non-empty string",
            )

        if not isinstance(state_topic, str) or not state_topic.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "update_state: state_topic must be a non-empty string",
            )

        self._require_connected("update_state")

        await self.update_state_direct(state_topic, payload)

        logger.debug(
            "update_state: published '%s' → %r",
            unique_id,
            payload[:80],
        )

    def get_state_topic(self, entity: Entity) -> Optional[str]:
        """
        Resolve the MQTT state topic for an entity.

        Entity itself carries no topic information — topics are
        derived from (domain, unique_id, discovery_prefix) via the
        SDK's shared entity_factory, the same code path
        AsyncEntityManager uses internally for update_state() and
        update_availability(). HAClient delegates here instead of
        duplicating HA's topic-naming scheme.

        Args:
            entity (Entity): The entity to resolve a state topic for.

        Returns:
            Optional[str]: The state topic, or None if this entity's
                           domain does not support state updates
                           (e.g. button, event).
        """

        if not isinstance(entity, Entity):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"get_state_topic: entity must be Entity, got {type(entity).__name__}",
            )

        # Local import: entity_factory is an internal SDK module (not
        # part of __all__) shared by both EntityManager implementations
        # for exactly this purpose — building topics without any
        # publish side effects.
        from ha_mqtt_sdk.core.entity_factory import build_registration

        registration = build_registration(entity, self._settings.discovery_prefix)
        return registration.state_topic

    async def update_state_direct(
        self,
        state_topic: str,
        payload: str,
    ) -> None:
        """
        Publish a state payload directly to an MQTT topic.

        Used as a lower-level alternative to update_state() when the
        full Entity object is not available. Publishes directly via
        the underlying MQTT client.

        Args:
            state_topic (str): Full MQTT state topic string.
            payload (str):     State payload string to publish.

        Raises:
            DirigeraBridgeError: MQTT_PUBLISH_FAILED if publish fails.
        """

        self._require_connected("update_state_direct")

        try:
            assert self._mqtt_client is not None
            await self._mqtt_client.publish(
                topic=state_topic,
                payload=payload,
                retain=False,
            )

            self._metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED)

            logger.debug(
                "update_state_direct: published to %s → %r",
                state_topic,
                payload[:80],
            )

        except Exception as exc:
            self._metrics.increment(MetricName.MQTT_PUBLISH_ERRORS)
            self._metrics.increment(MetricName.ERROR_MQTT)
            raise DirigeraBridgeError(
                ErrorCode.MQTT_PUBLISH_FAILED,
                f"Failed to publish to topic '{state_topic}': {exc}",
                cause=exc,
            )

    async def update_availability(
        self,
        entity: Entity,
        online: bool,
    ) -> None:
        """
        Publish device availability (online/offline) to MQTT.

        Published as retained so HA immediately sees the correct
        availability state on reconnect.

        Args:
            entity (Entity): The entity whose availability changed.
            online (bool):   True = online, False = offline.

        Raises:
            DirigeraBridgeError: MQTT_PUBLISH_FAILED if publish fails.
        """

        if not isinstance(entity, Entity):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"update_availability: entity must be Entity, "
                f"got {type(entity).__name__}",
            )

        self._require_connected("update_availability")

        try:
            assert self._sdk is not None
            await self._sdk.update_availability(entity, online)

            metric = (
                MetricName.ENTITY_AVAILABILITY_ONLINE
                if online
                else MetricName.ENTITY_AVAILABILITY_OFFLINE
            )
            self._metrics.increment(metric)
            self._metrics.increment(MetricName.MQTT_MESSAGES_PUBLISHED)

            logger.debug(
                "update_availability: '%s' → %s",
                entity.unique_id,
                "online" if online else "offline",
            )

        except DirigeraBridgeError:
            raise

        except Exception as exc:
            self._metrics.increment(MetricName.MQTT_PUBLISH_ERRORS)
            self._metrics.increment(MetricName.ERROR_MQTT)
            raise DirigeraBridgeError(
                ErrorCode.MQTT_PUBLISH_FAILED,
                f"Failed to publish availability for '{entity.unique_id}': {exc}",
                cause=exc,
            )

    async def set_all_offline(self, entities: List[Entity]) -> None:
        """
        Mark all given entities as offline.

        Called during shutdown or Dirigera disconnection to ensure
        HA shows all devices as unavailable rather than stale.

        Args:
            entities (List[Entity]): Entities to mark offline.
        """

        for entity in entities:
            try:
                await self.update_availability(entity, online=False)
            except DirigeraBridgeError as exc:
                logger.warning(
                    "set_all_offline: failed for '%s': %s",
                    entity.unique_id,
                    exc,
                )

    # ── Internal ─────────────────────────────────────────────────────────

    async def _build_and_connect(self) -> None:
        """
        Build the HASDK client and manager objects and connect.

        Separated from connect() so the retry loop can call it on
        each attempt without duplicating the construction logic.
        """

        mqtt_config = MQTTSettings(
            host=self._settings.mqtt_host,
            port=self._settings.mqtt_port,
            username=self._settings.mqtt_user,
            password=self._settings.mqtt_password,
            client_id=self._settings.mqtt_client_id,
            keepalive=self._settings.mqtt_keepalive,
            discovery_prefix=self._settings.discovery_prefix,
        )

        self._mqtt_client = AsyncMQTTClient(config=mqtt_config)

        # Constructing AsyncHASDK with an injected async_mqtt_client
        # causes AsyncEntityManager to call
        # self._mqtt_client.set_message_callback(...) internally,
        # wiring up the SDK's own topic -> callback routing. HAClient
        # must NOT call set_message_callback() again after this — doing
        # so would overwrite the SDK's handler and silently break all
        # command routing (the mqtt client awaits the callback, so a
        # non-awaitable replacement also raises at the first message).
        self._sdk = AsyncHASDK(async_mqtt_client=self._mqtt_client)

        assert self._mqtt_client is not None
        assert self._sdk is not None

        await self._mqtt_client.connect()

        logger.debug("HAClient: HASDK objects built and connected")

    def _wrap_command_callback(
        self,
        unique_id: str,
        callback: CommandCallback,
    ) -> CommandCallback:
        """
        Wrap an application-level command callback with metrics and
        logging, without re-implementing MQTT topic routing.

        The SDK (AsyncEntityManager) already resolves which entity a
        command topic belongs to and invokes the exact callback passed
        to sdk.register() for that entity — so no topic lookup is
        needed here at all.

        Args:
            unique_id (str):            Entity unique_id, for log context.
            callback (CommandCallback): The application-level handler.

        Returns:
            CommandCallback: Wrapped callable to hand to sdk.register().
        """

        async def _wrapped(topic: str, payload: str) -> None:
            self._metrics.increment(MetricName.MQTT_MESSAGES_RECEIVED)

            logger.debug(
                "HAClient: command received for '%s' on topic '%s': %r",
                unique_id,
                topic,
                payload[:80],
            )

            try:
                await callback(topic, payload)
            except Exception as exc:
                logger.error(
                    "HAClient: error in command callback for '%s' (topic '%s'): %s",
                    unique_id,
                    topic,
                    exc,
                )
                self._metrics.increment(MetricName.ERROR_MQTT)

        return _wrapped

    def _require_connected(self, operation: str) -> None:
        """
        Guard that raises if the MQTT client is not connected.

        Args:
            operation (str): Operation name for the error message.

        Raises:
            DirigeraBridgeError: MQTT_CONNECTION_FAILED if not connected.
        """

        if self._mqtt_client is None or self._sdk is None:
            raise DirigeraBridgeError(
                ErrorCode.MQTT_CONNECTION_FAILED,
                f"HAClient.{operation}: MQTT client is not connected — "
                f"call connect() first",
            )
