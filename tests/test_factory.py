"""
Tests for dirigera_bridge.factory.

factory.build_orchestrator() is the library's Composition Root: it
constructs every dependency and wires them into the Orchestrator,
given a validated Settings object. Moved out of dirigera_bridge.main
so it can be imported and called directly by a consumer embedding
this bridge in a larger application — these tests verify the wiring
in isolation from main.py's CLI concerns (logging setup, signal
handling, .env loading), which are tested separately in test_main.py.
"""

from unittest.mock import MagicMock, patch

import dirigera_bridge.factory as factory_module


def test_build_orchestrator() -> None:
    """
    Verify that build_orchestrator() creates and wires all dependencies.

    This is the most important test for the Composition Root. Each concrete
    dependency is replaced by a mock so that no real network connections or
    external resources are created.

    The test verifies that:

    1. Core infrastructure objects are created.
    2. Dirigera clients receive the correct dependencies.
    3. The Home Assistant client receives the correct dependencies.
    4. Mapping objects are created correctly.
    5. The Orchestrator receives every required dependency.
    6. The resulting Orchestrator is returned.
    """

    # Settings are passed through to the clients and Orchestrator. The actual
    # Settings object is not relevant to this wiring test.
    settings = MagicMock()

    # Replace every dependency constructed by build_orchestrator() with a
    # mock. This keeps the test completely isolated from external resources.
    with (
        patch("dirigera_bridge.factory.AsyncEventBus") as event_bus,
        patch("dirigera_bridge.factory.ServiceLifecycle") as lifecycle,
        patch("dirigera_bridge.factory.MetricsStore") as metrics,
        patch("dirigera_bridge.factory.StateCache") as state_cache,
        patch("dirigera_bridge.factory.DiscoveryCache") as discovery_cache,
        patch("dirigera_bridge.factory.DirigeraRestClient") as rest_client,
        patch("dirigera_bridge.factory.DirigeraWebSocketClient") as ws_client,
        patch("dirigera_bridge.factory.HAClient") as ha_client,
        patch("dirigera_bridge.factory.DeviceMapper") as device_mapper,
        patch("dirigera_bridge.factory.StateMapper") as state_mapper,
        patch("dirigera_bridge.factory.CommandMapper") as command_mapper,
        patch("dirigera_bridge.factory.Orchestrator") as orchestrator,
    ):
        result = factory_module.build_orchestrator(settings)

    # The function should return the Orchestrator that it constructed.
    assert result is orchestrator.return_value

    # Verify that the REST client receives the validated settings and metrics.
    rest_client.assert_called_once_with(
        settings=settings,
        metrics=metrics.return_value,
    )

    # Verify that the WebSocket client receives the shared event bus,
    # lifecycle manager, metrics store, and settings.
    ws_client.assert_called_once_with(
        settings=settings,
        event_bus=event_bus.return_value,
        lifecycle=lifecycle.return_value,
        metrics=metrics.return_value,
    )

    # Verify the Home Assistant client receives the shared metrics,
    # lifecycle, discovery cache, and settings.
    ha_client.assert_called_once_with(
        settings=settings,
        metrics=metrics.return_value,
        lifecycle=lifecycle.return_value,
        discovery_cache=discovery_cache.return_value,
    )

    # DeviceMapper requires the shared metrics store.
    device_mapper.assert_called_once_with(
        metrics=metrics.return_value,
    )

    # These mapping classes currently have no constructor dependencies.
    state_mapper.assert_called_once_with()
    command_mapper.assert_called_once_with()

    # Finally, verify the complete dependency graph passed to the
    # Orchestrator. This is the central purpose of this test.
    orchestrator.assert_called_once_with(
        settings=settings,
        event_bus=event_bus.return_value,
        lifecycle=lifecycle.return_value,
        metrics=metrics.return_value,
        state_cache=state_cache.return_value,
        discovery_cache=discovery_cache.return_value,
        ha_client=ha_client.return_value,
        ws_client=ws_client.return_value,
        rest_client=rest_client.return_value,
        device_mapper=device_mapper.return_value,
        state_mapper=state_mapper.return_value,
        command_mapper=command_mapper.return_value,
    )
