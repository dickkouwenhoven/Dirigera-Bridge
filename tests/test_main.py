"""
Tests for the application entrypoint in dirigera_bridge.main.

The main module is the Composition Root of the application. It is responsible
for:

- Configuring logging
- Loading application settings
- Constructing all application dependencies
- Wiring those dependencies into the Orchestrator
- Registering SIGINT/SIGTERM handlers
- Starting and stopping the Orchestrator
- Handling startup and runtime errors

These tests deliberately mock the actual application dependencies. The purpose
is to verify the wiring and control flow without creating real network
connections to the Dirigera hub or MQTT broker.

The individual components and clients are tested separately in their own test
modules.
"""

import asyncio
import logging
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dirigera_bridge.main as main_module
from dirigera_bridge.core import DirigeraBridgeError, ErrorCode


def test_configure_logging_debug_level() -> None:
    """
    Verify that configure_logging() configures the root logger correctly.

    The test checks the logging level, output format, date format, output
    stream, and force=True option. The actual logging configuration is mocked
    because there is no need to modify pytest's logging configuration while
    running the test.
    """

    with patch("dirigera_bridge.main.logging.basicConfig") as basic_config:
        main_module.configure_logging("DEBUG")

        basic_config.assert_called_once_with(
            level=logging.DEBUG,
            format="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)-40s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            stream=sys.stdout,
            force=True,
        )


def test_configure_logging_invalid_level_defaults_to_info() -> None:
    """
    Verify that an unknown logging level falls back to INFO.

    configure_logging() uses getattr() with logging.INFO as its default.
    This protects the application from an invalid LOG_LEVEL configuration.
    """

    with patch("dirigera_bridge.main.logging.basicConfig") as mock_basic_config:
        main_module.configure_logging("THIS_IS_NOT_A_LEVEL")

    assert mock_basic_config.call_args.kwargs["level"] == logging.INFO


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
        patch("dirigera_bridge.main.AsyncEventBus") as event_bus,
        patch("dirigera_bridge.main.ServiceLifecycle") as lifecycle,
        patch("dirigera_bridge.main.MetricsStore") as metrics,
        patch("dirigera_bridge.main.StateCache") as state_cache,
        patch("dirigera_bridge.main.DiscoveryCache") as discovery_cache,
        patch("dirigera_bridge.main.DirigeraRestClient") as rest_client,
        patch("dirigera_bridge.main.DirigeraWebSocketClient") as ws_client,
        patch("dirigera_bridge.main.HAClient") as ha_client,
        patch("dirigera_bridge.main.DeviceMapper") as device_mapper,
        patch("dirigera_bridge.main.StateMapper") as state_mapper,
        patch("dirigera_bridge.main.CommandMapper") as command_mapper,
        patch("dirigera_bridge.main.Orchestrator") as orchestrator,
    ):
        result = main_module.build_orchestrator(settings)

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


@pytest.mark.asyncio
async def test_async_main_runs_orchestrator() -> None:
    """
    Verify the normal startup path of async_main().

    The test verifies that:

    1. Settings are loaded.
    2. Logging is configured using the configured log level.
    3. The Orchestrator is constructed.
    4. SIGINT and SIGTERM handlers are registered.
    5. The Orchestrator's run() method is awaited.

    All external dependencies are mocked.
    """

    # Provide realistic settings values because async_main() logs these
    # values during startup.
    settings = MagicMock()
    settings.log_level = "INFO"
    settings.dirigera_ip = "192.168.1.10"
    settings.mqtt_host = "localhost"
    settings.mqtt_port = 1883
    settings.mqtt_client_id = "test"
    settings.mqtt_base_topic = "dirigera"
    settings.discovery_prefix = "homeassistant"

    # The real Orchestrator must never be started during this unit test.
    # AsyncMock allows us to verify that run() was awaited.
    orchestrator = MagicMock()
    orchestrator.run = AsyncMock()

    # Replace configuration loading, logging, dependency construction, and
    # signal registration with mocks.
    with (
        patch(
            "dirigera_bridge.main.load_settings",
            return_value=settings,
        ),
        patch("dirigera_bridge.main.configure_logging"),
        patch(
            "dirigera_bridge.main.build_orchestrator",
            return_value=orchestrator,
        ),
        patch.object(
            asyncio.get_running_loop(),
            "add_signal_handler",
        ) as add_signal_handler,
    ):
        await main_module.async_main()

    # The Orchestrator must be started exactly once.
    orchestrator.run.assert_awaited_once()

    # Both shutdown signals must be registered.
    assert add_signal_handler.call_count == 2

    signals_registered = {call.args[0] for call in add_signal_handler.call_args_list}

    assert signals_registered == {
        signal.SIGINT,
        signal.SIGTERM,
    }


@pytest.mark.asyncio
async def test_async_main_configuration_error() -> None:
    """
    Verify that a configuration error causes a clean application exit.

    load_settings() raises DirigeraBridgeError when configuration is invalid.
    async_main() should log the error and terminate with exit code 1.
    """

    error = DirigeraBridgeError(
        code=ErrorCode.CONFIG_INVALID_VALUE,
        message="configuration failed",
    )

    with (
        patch(
            "dirigera_bridge.main.load_settings",
            side_effect=error,
        ),
        patch(
            "dirigera_bridge.main.sys.exit",
            side_effect=SystemExit(1),
        ) as exit_mock,
        pytest.raises(SystemExit),
    ):
        await main_module.async_main()

    # Verify that configuration failure results in exit code 1.
    exit_mock.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_async_main_build_error() -> None:
    """
    Verify that a dependency construction error causes a clean exit.

    If build_orchestrator() raises DirigeraBridgeError, async_main() should
    report the error and terminate with exit code 1.
    """

    settings = MagicMock()
    settings.log_level = "INFO"

    error = DirigeraBridgeError(
        code=ErrorCode.LIFECYCLE_STARTUP_FAILED,
        message="failed to build orchestrator",
    )

    with (
        patch(
            "dirigera_bridge.main.load_settings",
            return_value=settings,
        ),
        patch("dirigera_bridge.main.configure_logging"),
        patch(
            "dirigera_bridge.main.build_orchestrator",
            side_effect=error,
        ),
        patch(
            "dirigera_bridge.main.sys.exit",
            side_effect=SystemExit(1),
        ) as exit_mock,
        pytest.raises(SystemExit),
    ):
        await main_module.async_main()

    # Dependency construction errors must terminate the application with
    # the documented exit code.
    exit_mock.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_async_main_orchestrator_error() -> None:
    """
    Verify that an unexpected Orchestrator exception causes a clean exit.

    The Orchestrator is deliberately made to raise a RuntimeError. This
    exercises the broad exception handler around orchestrator.run().
    """

    settings = MagicMock()
    settings.log_level = "INFO"

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(side_effect=RuntimeError("unexpected failure"))

    with (
        patch(
            "dirigera_bridge.main.load_settings",
            return_value=settings,
        ),
        patch("dirigera_bridge.main.configure_logging"),
        patch(
            "dirigera_bridge.main.build_orchestrator",
            return_value=orchestrator,
        ),
        patch(
            "dirigera_bridge.main.sys.exit",
            side_effect=SystemExit(1),
        ) as exit_mock,
        pytest.raises(SystemExit),
    ):
        await main_module.async_main()

    # Verify that run() was actually reached and that the exception was
    # handled by async_main().
    orchestrator.run.assert_awaited_once()
    exit_mock.assert_called_once_with(1)


def test_main() -> None:
    """
    Verify the synchronous application entrypoint.

    main() configures minimal logging and starts the asynchronous
    application through asyncio.run().
    """

    async def dummy_async_main() -> None:
        """
        Dummy asynchronous entrypoint used by this unit test.
        """
        return None

    async_main_coroutine = dummy_async_main()

    mock_async_main = MagicMock(
        name="mock_async_main",
        return_value=async_main_coroutine,
    )

    def run_coroutine(coro: object) -> None:
        """
        Consume the coroutine passed to asyncio.run().

        asyncio.run() is mocked during this test, so the coroutine must be
        explicitly closed to prevent an un-awaited-coroutine warning.
        """
        if not asyncio.iscoroutine(coro):
            raise TypeError("main() did not pass a coroutine to asyncio.run()")

        coro.close()

    with (
        patch("dirigera_bridge.main.configure_logging") as mock_configure_logging,
        patch(
            "dirigera_bridge.main.async_main",
            new=mock_async_main,
        ),
        patch(
            "dirigera_bridge.main.asyncio.run",
            side_effect=run_coroutine,
        ) as mock_asyncio_run,
    ):
        main_module.main()

    # Verify that minimal logging is configured before application startup.
    mock_configure_logging.assert_called_once_with("INFO")

    # Verify that async_main() is called exactly once.
    mock_async_main.assert_called_once_with()

    # Verify that asyncio.run() is called with the coroutine returned by
    # async_main().
    mock_asyncio_run.assert_called_once_with(async_main_coroutine)


def test_main_keyboard_interrupt() -> None:
    """
    Verify graceful handling of KeyboardInterrupt.

    asyncio.run() can propagate KeyboardInterrupt if the process is
    interrupted before the application's signal handlers are active.
    main() catches it and logs a normal shutdown message instead of
    propagating the exception.
    """

    def run_and_interrupt(coro: object) -> None:
        # asyncio.run(async_main()) evaluates async_main() BEFORE the
        # (mocked) asyncio.run() call happens, so a real coroutine
        # object is always created here regardless of what the mock
        # does with it. Closing it before raising is what the real
        # asyncio.run would effectively do on interrupt. Skipping
        # this leaves an un-awaited "coroutine 'async_main' was never
        # awaited" warning, which surfaces later during garbage
        # collection rather than at the point of the actual bug.
        if hasattr(coro, "close"):
            coro.close()
        raise KeyboardInterrupt

    with (
        patch("dirigera_bridge.main.configure_logging"),
        patch(
            "dirigera_bridge.main.asyncio.run",
            side_effect=run_and_interrupt,
        ),
        patch("dirigera_bridge.main.logging.getLogger") as get_logger,
    ):
        main_module.main()

    # Verify that KeyboardInterrupt is converted into the expected
    # informational shutdown message.
    get_logger.return_value.info.assert_called_once_with("KeyboardInterrupt — bridge stopped")


@pytest.mark.asyncio
async def test_async_main_signal_handler_requests_shutdown() -> None:
    """
    Verify that the registered signal handler actually requests a
    graceful shutdown when invoked.

    async_main() registers a closure (_request_shutdown) with
    loop.add_signal_handler() for both SIGINT and SIGTERM. No OS signal
    ever arrives during a unit test. So this captures that closure
    directly from the mocked add_signal_handler() call and invokes it
    exactly as the event loop would — covering the closure's body
    (lines 228-232), which test_async_main_runs_orchestrator alone
    does not reach.
    """
    settings = MagicMock()
    settings.log_level = "INFO"
    settings.dirigera_ip = "192.168.1.10"
    settings.mqtt_host = "localhost"
    settings.mqtt_port = 1883
    settings.mqtt_client_id = "test"
    settings.mqtt_base_topic = "dirigera"
    settings.discovery_prefix = "homeassistant"

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock()
    orchestrator.stop = AsyncMock()

    with (
        patch(
            "dirigera_bridge.main.load_settings",
            return_value=settings,
        ),
        patch("dirigera_bridge.main.configure_logging"),
        patch(
            "dirigera_bridge.main.build_orchestrator",
            return_value=orchestrator,
        ),
        patch.object(
            asyncio.get_running_loop(),
            "add_signal_handler",
        ) as add_signal_handler,
    ):
        await main_module.async_main()

    # Pull out the handler + its bound argument for SIGINT specifically,
    # exactly as loop.add_signal_handler(sig, _request_shutdown, sig.name)
    # registered it.
    sigint_call = next(
        call for call in add_signal_handler.call_args_list if call.args[0] == signal.SIGINT
    )
    handler = sigint_call.args[1]
    handler_arg = sigint_call.args[2]

    # Invoke it directly — this is what the event loop does when the
    # real signal arrives. Deliberately NOT mocking asyncio.ensure_future
    # here: doing so would leave orchestrator.stop()'s coroutine
    # created-but-never-awaited, the exact kind of leak this whole fix
    # is about. Instead, let it schedule a real Task on the running loop,
    # then yield once so that Task actually runs.
    handler(handler_arg)
    await asyncio.sleep(0)

    orchestrator.stop.assert_awaited_once()
