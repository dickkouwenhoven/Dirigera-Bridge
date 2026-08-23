"""
factory.py

Public composition-root factory for the Dirigera MQTT Bridge.

Role & Responsibility:
    Constructs every dependency and wires them into an Orchestrator,
    given a validated Settings object. This is the Composition Root
    for the *library* — it knows about every dependency simultaneously,
    exactly like dirigera_bridge.main did before this was split out,
    but it takes no responsibility for where Settings came from, how
    logging is configured, or how the process is run.

    That separation is what makes this reusable: dirigera_bridge.main
    (the CLI/Docker application) calls build_orchestrator() after
    loading .env and configuring logging. A consumer embedding this
    bridge inside a larger multi-bridge application — one that also
    wires up e.g. a Philips Hue bridge — can call build_orchestrator()
    directly with a Settings object they constructed themselves,
    without pulling in any of main.py's CLI-specific concerns
    (signal handlers, stdout logging, sys.exit() on startup failure).

    Each Orchestrator built here is fully self-contained: HAClient
    owns its own MQTT connection (see ha/ha_client.py), so running
    multiple bridges concurrently means running multiple independent
    build_orchestrator() results side by side — no shared connection
    state to coordinate.

Not responsible for:
    - Loading or validating Settings (see config.py)
    - Logging configuration (see main.py)
    - Process lifecycle: signal handling, event loop, sys.exit()
      (see main.py)
"""

from __future__ import annotations

import logging

from .config import Settings
from .core.discovery_cache import DiscoveryCache
from .core.event_bus import AsyncEventBus
from .core.lifecycle import ServiceLifecycle
from .core.metrics import MetricsStore
from .core.state_cache import StateCache
from .dirigera.rest_client import DirigeraRestClient
from .dirigera.websocket_client import DirigeraWebSocketClient
from .ha.ha_client import HAClient
from .mapping.command_mapper import CommandMapper
from .mapping.device_mapper import DeviceMapper
from .mapping.state_mapper import StateMapper
from .orchestrator import Orchestrator

# Hardcoded here — not an env variable — so it cannot be accidentally
# overridden. Imported by main.py for its own startup/shutdown log lines,
# so this remains the single place these two values are defined.
SERVICE_VERSION = "1.0.0"
SERVICE_NAME = "dirigera-mqtt-bridge"

logger = logging.getLogger(__name__)


def build_orchestrator(settings: Settings) -> Orchestrator:
    """
    Construct all application dependencies and wire them into the
    Orchestrator.

    This function is the Composition Root — every object in the
    dependency graph is created here, in dependency order, and
    injected into the objects that need it. Safe to call directly
    from outside this package: it has no side effects beyond object
    construction (no I/O, no env access, no logging configuration).

    Args:
        settings (Settings): Validated application settings. Load
            these yourself via dirigera_bridge.config.load_settings()
            (reads .env) or construct a Settings instance directly.

    Returns:
        Orchestrator: Fully wired orchestrator, not yet running.
            Call await orchestrator.run() to start it and
            await orchestrator.stop() for a clean shutdown.
    """

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
