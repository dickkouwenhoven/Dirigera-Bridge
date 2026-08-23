"""
main.py

Application entrypoint for the Dirigera MQTT Bridge.

Role & Responsibility:

    Following the Composition Root pattern: all object creation happens
    here, all wiring happens here, and then run() is called once.
    The CLI/Docker composition root. Loads .env, configures logging,
    delegates dependency wiring to dirigera_bridge.factory.build_orchestrator(),
    and runs the result under asyncio with SIGINT/SIGTERM handling.

What it does:
    1. Configures structured logging from the LOG_LEVEL setting
    2. Loads and validates all settings from .env
    3. Calls build_orchestrator() to construct and wire dependencies
    4. Runs the Orchestrator under asyncio, handling SIGINT/SIGTERM
       for clean shutdown

Arguments / Configuration:
    All configuration is read from .env via dirigera_bridge.config.load_settings().
    No command-line arguments are required.

Not responsible for:
    - Dependency construction and wiring (that is factory.py)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .config import load_settings
from .core.errors import DirigeraBridgeError
from .factory import SERVICE_NAME, SERVICE_VERSION, build_orchestrator


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
        format="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)-40s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # Suppress noisy third-party loggers at WARNING level
    for noisy_logger in ("asyncio", "aiohttp", "websockets", "aiomqtt"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


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
    main()  # pragma: no cover
