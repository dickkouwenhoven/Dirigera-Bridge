"""
main.py

Application entrypoint for the Dirigera MQTT Bridge.

Role & Responsibility:
    Constructs every dependency, wires them together, and hands
    control to the Orchestrator. This is the only file that knows
    about all dependencies simultaneously — the Orchestrator receives
    them via constructor injection and never imports from this file.

    Following the Composition Root pattern: all object creation happens
    here, all wiring happens here, and then run() is called once.

What it does:
    1. Configures structured logging from the LOG_LEVEL setting
    2. Loads and validates all settings from .env
    3. Constructs every core, dirigera, mapping, and HA object
    4. Creates the Orchestrator with all dependencies injected
    5. Runs the Orchestrator under asyncio, handling SIGINT/SIGTERM
       for clean shutdown

Arguments / Configuration:
    All configuration is read from .env via app.config.load_settings().
    No command-line arguments are required.

Not responsible for:
    - Any business logic (that is the Orchestrator)
    - Any network I/O (that is the layer clients)
    - Configuration validation (that is config.py)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from app.config import load_settings, Settings
from app.core.discovery_cache import DiscoveryCache
from app.core.errors import DirigeraBridgeError
from app.core.event_bus import AsyncEventBus
from app.core.lifecycle import ServiceLifecycle
from app.core.metrics import MetricsStore
from app.core.state_cache import StateCache
from app.dirigera.rest_client import DirigeraRestClient
from app.dirigera.websocket_client import DirigeraWebSocketClient
from app.ha.ha_client import HAClient
from app.mapping.command_mapper import CommandMapper
from app.mapping.device_mapper import DeviceMapper
from app.mapping.state_mapper import StateMapper
from app.orchestrator import Orchestrator

# ── Service version ───────────────────────────────────────────────────────────
# Hardcoded here — not an env variable — so it cannot be accidentally overridden.
SERVICE_VERSION = "1.0.0"
SERVICE_NAME = "dirigera-mqtt-bridge"


def configure_logging(log_level: str) -> None:
    """
    Configure structured logging for the entire application.

    Sets up a single stream handler to stdout with a consistent format
    that includes timestamp, level, logger name, and message. All
    application loggers use __name__ so the logger hierarchy mirrors
    the module hierarchy.

    Args:
        log_level (str): Python logging level string (e.g. 'INFO').
    """

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=("%(asctime)s.%(msecs)03d %(levelname)-8s %(name)-40s %(message)s"),
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # Suppress noisy third-party loggers at WARNING level
    for noisy_logger in ("asyncio", "aiohttp", "websockets", "aiomqtt"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def build_orchestrator(settings: Settings) -> Orchestrator:
    """
    Construct all application dependencies and wire them into the
    Orchestrator.

    This function is the Composition Root — every object in the
    application is created here, in dependency order, and injected
    into the objects that need it.

    Args:
        settings (Settings): Validated application settings.

    Returns:
        Orchestrator: Fully wired orchestrator ready to run.
    """

    logger = logging.getLogger(__name__)
    logger.info(
        "%s v%s — building dependency graph",
        SERVICE_NAME,
        SERVICE_VERSION,
    )

    # ── Core infrastructure ───────────────────────────────────────────────
    event_bus = AsyncEventBus()
    lifecycle = ServiceLifecycle()
    metrics = MetricsStore()
    state_cache = StateCache()
    discovery_cache = DiscoveryCache()

    # ── Dirigera layer ────────────────────────────────────────────────────
    rest_client = DirigeraRestClient(
        settings=settings,
        metrics=metrics,
    )

    ws_client = DirigeraWebSocketClient(
        settings=settings,
        event_bus=event_bus,
        lifecycle=lifecycle,
        metrics=metrics,
    )

    # ── HA / MQTT layer ───────────────────────────────────────────────────
    ha_client = HAClient(
        settings=settings,
        metrics=metrics,
        lifecycle=lifecycle,
        discovery_cache=discovery_cache,
    )

    # ── Mapping layer ─────────────────────────────────────────────────────
    device_mapper = DeviceMapper(metrics=metrics)
    state_mapper = StateMapper()
    command_mapper = CommandMapper()

    # ── Orchestrator ──────────────────────────────────────────────────────
    orchestrator = Orchestrator(
        settings=settings,
        event_bus=event_bus,
        lifecycle=lifecycle,
        metrics=metrics,
        state_cache=state_cache,
        discovery_cache=discovery_cache,
        ha_client=ha_client,
        ws_client=ws_client,
        rest_client=rest_client,
        device_mapper=device_mapper,
        state_mapper=state_mapper,
        command_mapper=command_mapper,
    )

    logger.info(
        "%s v%s — dependency graph built successfully",
        SERVICE_NAME,
        SERVICE_VERSION,
    )

    return orchestrator


async def async_main() -> None:
    """
    Async entry point.

    Loads settings, builds the orchestrator, registers OS signal
    handlers for clean shutdown, and runs the orchestrator.
    """

    logger = logging.getLogger(__name__)

    # ── Load and validate settings ────────────────────────────────────────
    try:
        settings = load_settings()
    except DirigeraBridgeError as exc:
        # configure_logging was called before this with default INFO
        # so this will always reach the log handler
        logging.critical("Configuration error: %s", exc)
        sys.exit(1)

    # ── Reconfigure logging with the loaded log level ─────────────────────
    configure_logging(settings.log_level)

    logger.info("=" * 60)
    logger.info(
        "%s v%s starting",
        SERVICE_NAME,
        SERVICE_VERSION,
    )
    logger.info(
        "Dirigera hub : %s",
        settings.dirigera_ip,
    )
    logger.info(
        "MQTT broker  : %s:%d (client_id=%s)",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_client_id,
    )
    logger.info(
        "Base topic   : %s",
        settings.mqtt_base_topic,
    )
    logger.info(
        "Discovery    : %s",
        settings.discovery_prefix,
    )
    logger.info("=" * 60)

    # ── Build orchestrator ────────────────────────────────────────────────
    try:
        orchestrator = build_orchestrator(settings)
    except DirigeraBridgeError as exc:
        logging.critical("Failed to build orchestrator: %s", exc)
        sys.exit(1)

    # ── Register OS signal handlers for clean shutdown ────────────────────
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig_name: str) -> None:
        logger.info(
            "Signal %s received — requesting graceful shutdown",
            sig_name,
        )
        asyncio.ensure_future(orchestrator.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown, sig.name)

    # ── Run ───────────────────────────────────────────────────────────────
    logger.info("Handing control to Orchestrator — bridge starting")

    try:
        await orchestrator.run()
    except Exception as exc:
        logger.critical(
            "Unhandled exception in orchestrator: %s",
            exc,
            exc_info=True,
        )
        sys.exit(1)

    logger.info("%s v%s stopped cleanly", SERVICE_NAME, SERVICE_VERSION)


def main() -> None:
    """
    Synchronous entry point — called by Docker CMD or direct execution.

    Sets up minimal logging before settings are loaded (so any config
    errors are visible), then hands off to asyncio.run().
    """

    # Minimal logging before settings load — reconfigured after
    configure_logging("INFO")

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        # asyncio.run() surfaces KeyboardInterrupt if the loop is
        # interrupted before signal handlers are registered
        logging.getLogger(__name__).info("KeyboardInterrupt — bridge stopped")


if __name__ == "__main__":
    main()
