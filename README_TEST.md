# Dirigera MQTT Bridge — Test Suite

This document describes the test suite for the Dirigera MQTT Bridge: how to install the development environment, run tests, generate coverage reports, run code-quality checks, understand the test structure, and add new tests.

The test suite is designed to exercise the bridge from small pure-Python units through multi-layer orchestration flows, while keeping tests deterministic and independent of a real Dirigera hub, Home Assistant instance, or MQTT broker.


## Quick start
The recommended development setup is an editable installation with all development dependencies:

```python -m pip install -e ".[dev]"```

Then run the complete test suite:

```make test```

Useful commands:
```
# All tests
make test

# Unit tests only
make test-unit

# Integration tests only
make test-integration

# Coverage report
make coverage

# HTML coverage report
make coverage-html

# Lint
make lint

# Type checking
make typecheck

# Run a specific test file
make test-file FILE=tests/core/test_errors.py

# Run tests matching a keyword
make test-k KEY=lifecycle

# Remove generated test/build artefacts
make clean
```
For a complete CI-equivalent test run, use:
```
python -m pytest \
  --cov \
  --cov-report=term-missing \
  --cov-fail-under=100 \
  -v
```
The GitHub Actions CI currently runs the test suite against Python 3.12, 3.13, and 3.14 and requires 100% coverage.

## Development prerequisites
The project currently requires Python 3.12 or newer.

The application and development dependencies are defined by pyproject.toml. The simplest setup is:
```
python -m pip install -e ".[dev]"
```
This installs:

- Application dependencies
- pytest
- pytest-asyncio
- pytest-cov
- ruff
- mypy
- Other development tooling declared by the project
Alternatively, the repository contains:

```requirements-dev.txt```

which installs the project in editable mode with its development extras.

The Home Assistant MQTT SDK is installed from PyPI as `ha_mqtt_sdk`; there is no local `sdk_src/` directory required by the current test suite.

### Test philosophy
The test suite is deliberately layered.

### Unit tests
Unit tests focus on one class or function at a time.

They should:

  - Avoid real network connections.
  - Avoid real MQTT brokers.
  - Avoid real Dirigera hubs.
  - Avoid Home Assistant.
  - Exercise actual application code rather than replacing the code under test with mocks.
  - Use mocks only at genuine external boundaries.

Examples include:
  - Lifecycle state transitions.
  - Retry/backoff calculations.
  - State and discovery caches.
  - Configuration validation.
  - Dirigera payload parsing.
  - Device and state mapping.
  - Domain mapper behavior.

### Integration tests
Integration tests exercise several application layers together.

They still do not require a physical Dirigera hub or Home Assistant instance. Network-facing application clients are replaced with controlled test doubles where appropriate.

The main integration flow is:
```
Dirigera events
      │
      ▼
  Orchestrator
      │
      ├── Device registry
      ├── Device mapper
      ├── State mapper
      ├── Command mapper
      ├── State cache
      └── HA client
              │
              ▼
       Fake MQTT transport
```
### HA/MQTT client tests
`tests/ha/test_ha_client.py` occupies a special position.
It uses a `FakeMQTTClient` instead of a real MQTT transport, but runs the real HA MQTT SDK underneath the bridge's `HAClient`.

This means the tests exercise SDK behavior that the bridge actually depends on, including:

  - MQTT topic construction.
  - Entity registration.
  - Discovery payload handling.
  -  State topics.
  - Availability topics.
  - Command routing.
  - MQTT message callbacks.
  - SDK validation.

This is intentionally stronger than simply mocking the entire SDK away.

### Running tests
### All tests
```
make test
```
Equivalent:
```
python -m pytest tests/
```
The repository's pytest configuration already sets:
```
asyncio_mode = auto
testpaths = tests
```
so no additional pytest arguments are normally required.

### Unit tests
Run only tests marked with unit:
```
make test-unit
```
or:
```
python -m pytest tests/ -m unit
```

Unit tests should remain fast and isolated.

### Integration tests
Run only tests marked with `integration`:
```
make test-integration
```
or:
```
python -m pytest tests/ -m integration
```

Integration tests currently concentrate on multi-layer orchestration behavior.

### Exclude slow tests
```
python -m pytest tests/ -m "not slow"
```

### A specific test file
```
make test-file FILE=tests/core/test_lifecycle.py
```
or:
```
python -m pytest tests/core/test_lifecycle.py -v
```

### A specific test class
```
python -m pytest \
  tests/core/test_lifecycle.py::TestValidTransitions \
  -v
```

### A specific test function
```
python -m pytest \
  tests/core/test_lifecycle.py::TestValidTransitions::test_created_to_starting \
  -v
```

### Tests matching a keyword
```
make test-k KEY=lifecycle
```
or:
```
python -m pytest tests/ -k lifecycle -v
```

Multiple expressions can be combined using normal pytest `-k` syntax:
```
python -m pytest tests/ -k "light and not slow" -v
```

### Test markers
The repository defines three pytest markers:

Marker	Description
unit	Pure unit tests with no external dependencies
integration	Tests that exercise multiple application layers together
slow	Tests expected to take more than one second

Examples:

@pytest.mark.unit
def test_something() -> None:
    ...

and:

@pytest.mark.integration
async def test_orchestrator_flow() -> None:
    ...

Run marker-specific tests with:

python -m pytest tests/ -m unit
python -m pytest tests/ -m integration
python -m pytest tests/ -m "not slow"

The pytest configuration uses strict markers, so a new marker must be explicitly added to pytest.ini or pyproject.toml.


### Test structure

The current test suite is organized to mirror the application's architecture:

tests/
├── __init__.py
├── conftest.py                         ← shared fixtures
│
├── test_factory.py                     ← composition-root wiring
├── test_main.py                        ← CLI/application entrypoint
│
├── config/
│   ├── __init__.py
│   └── test_config.py                  ← Settings and configuration
│
├── core/
│   ├── __init__.py
│   ├── test_errors.py                  ← errors and ErrorCode
│   ├── test_event_bus.py               ← AsyncEventBus
│   ├── test_lifecycle.py               ← lifecycle state machine
│   ├── test_metrics.py                 ← MetricsStore
│   ├── test_retry.py                   ← retry/backoff
│   ├── test_state_cache.py             ← state deduplication
│   └── test_discovery_cache.py         ← HA registration cache
│
├── dirigera/
│   ├── __init__.py
│   ├── test_models.py                  ← Pydantic Dirigera models
│   ├── test_rest_client.py             ← REST client
│   └── test_websocket_client.py        ← WebSocket client
│
├── ha/
│   ├── __init__.py
│   └── test_ha_client.py               ← HA/MQTT SDK integration
│
├── mapping/
│   ├── __init__.py
│   ├── test_command_mapper.py          ← HA → Dirigera commands
│   ├── test_device_mapper.py           ← device → HA entities
│   ├── test_device_registry.py         ← physical-device grouping
│   ├── test_state_mapper.py            ← Dirigera → HA state
│   │
│   └── domains/
│       ├── __init__.py
│       ├── test_init.py                ← registry/helpers
│       ├── test_air_purifier.py
│       ├── test_binary_sensor.py
│       ├── test_blind.py
│       ├── test_button.py
│       ├── test_environment_sensor.py
│       ├── test_gateway.py
│       ├── test_light.py
│       ├── test_outlet.py
│       ├── test_remote.py
│       ├── test_sensor.py
│       ├── test_speaker.py
│       └── test_switch.py
│
└── integration/
    ├── __init__.py
    └── test_orchestrator_flow.py        ← end-to-end application flow

The structure intentionally follows the production package layout and keeps tests close to the responsibility they verify.

### Shared fixtures

Common fixtures live in:

tests/conftest.py

They are automatically available to every test.
Environment and settings
Fixture	Description
isolate_dotenv	Prevents tests from accidentally reading a developer's real .env
valid_env	Populates all required and optional settings with deterministic test values
settings	Returns a validated Settings object
reset_settings_singelton	Resets the configuration singleton between tests

Tests involving configuration should use valid_env or settings rather than relying on a real .env.

This is particularly important because configuration tests deliberately verify missing and invalid environment variables.
Core fixtures
Fixture	Description
event_bus	Fresh AsyncEventBus
lifecycle	Fresh ServiceLifecycle
metrics	Fresh MetricsStore
state_cache	Fresh StateCache
discovery_cache	Fresh DiscoveryCache

Each test receives an isolated instance.
Mock client fixtures
Fixture	Description
mock_ha_client	Mocked HAClient with asynchronous methods
mock_ws_client	Mocked DirigeraWebSocketClient
mock_rest_client	Mocked DirigeraRestClient

These fixtures are primarily useful for orchestrator and integration-flow tests where establishing real network clients would make the test unnecessarily fragile.
Real Dirigera payload fixtures

conftest.py also contains session-scoped payload fixtures based on real Dirigera discovery data.

Current fixtures include:
Fixture	Device	Purpose
light_raw	TRADFRI GU10 CWS	Colour-temperature/colour light
vallhorn_motion_raw	VALLHORN	Motion sensor with battery
vallhorn_light_raw	VALLHORN sibling	Illuminance sensor
vindstyrka_raw	VINDSTYRKA	Environment/air-quality measurements
outlet_raw	INSPELNING	Smart plug and energy measurements
gateway_raw	DIRIGERA Hub	Gateway state and coordinates
remote_raw	Remote Control N2	Controller and battery
water_sensor_raw	BADRING	Water-leak sensor and battery

These fixtures are valuable because they test the application against realistic Dirigera payload shapes rather than simplified hand-written examples.
What is tested
Application entrypoint

tests/test_main.py verifies the CLI/application startup layer independently from dependency construction.

Covered behavior includes:

    Logging configuration.
    Invalid log-level fallback.
    Settings loading.
    Orchestrator construction.
    Signal registration.
    Normal asynchronous startup.
    Configuration failures.
    Orchestrator construction failures.
    Runtime failures.
    Graceful signal-triggered shutdown.
    KeyboardInterrupt handling.
    python -m dirigera_bridge module entrypoint behavior.

The purpose is to test main.py's control flow without creating real network connections.
Composition root

tests/test_factory.py tests:

build_orchestrator()

The test verifies that the composition root constructs and wires the complete dependency graph correctly.

It checks the creation and injection of:

    Event bus.
    Lifecycle manager.
    Metrics store.
    State cache.
    Discovery cache.
    Dirigera REST client.
    Dirigera WebSocket client.
    HA client.
    Device mapper.
    State mapper.
    Command mapper.
    Orchestrator.

This is intentionally separate from test_main.py: main.py handles application startup concerns, while factory.py owns dependency construction.
tests/core/ — Core infrastructure
Module	Main scenarios
errors.py	Error codes, exception construction, formatting
event_bus.py	Subscribe/unsubscribe, multiple subscribers, error isolation, non-blocking publication
lifecycle.py	Valid transitions, invalid transitions, failure paths, callbacks
metrics.py	Counters, snapshots, reset behavior, error totals
retry.py	Backoff sequence, maximum delay, jitter, exhaustion, interruption
state_cache.py	First value, unchanged value, changed value, None/zero values, clearing and snapshots
discovery_cache.py	Registration, duplicate handling, physical-device grouping, relation indexes

These modules are mostly pure application logic and therefore should have very high coverage.
tests/config/ — Configuration

test_config.py verifies the typed settings layer.

Important scenarios include:

    All required variables present.
    Each required variable missing individually.
    Invalid numeric values.
    Invalid MQTT QoS values.
    Invalid log levels.
    Invalid intervals.
    Cross-field validation.
    MQTT reconnect delay validation.
    WebSocket ping/timeout validation.
    Frozen/immutable settings.
    Secret redaction in safe_repr().

Configuration tests deliberately isolate dotenv loading so that a developer's real .env can never accidentally satisfy a test that is supposed to verify a missing setting.
tests/dirigera/ — Dirigera layer
test_models.py

Tests Pydantic parsing and model behavior using real discovery payloads.

Coverage includes:

    Device parsing.
    WebSocket event parsing.
    Device grouping.
    Physical IDs.
    Raw attributes.
    Real-world payload variations.

test_rest_client.py

The REST client is tested without contacting a real hub.

Covered behavior includes:

    Authentication headers.
    HTTP configuration.
    Timeouts.
    Device retrieval.
    PATCH commands.
    Successful responses.
    Authentication failures.
    Not-found responses.
    Server errors.
    Client shutdown.

test_websocket_client.py

The WebSocket client is tested with a mocked WebSocket connection.

Covered behavior includes:

    Incoming messages.
    Attribute-level event generation.
    Device-added events.
    Device-removed events.
    Connection handling.
    SSL configuration.
    Shutdown behavior.

tests/ha/ — Home Assistant/MQTT integration

test_ha_client.py is one of the most important integration-boundary test modules.

Instead of mocking the entire HA MQTT SDK, it provides an in-memory:

FakeMQTTClient

The real SDK remains active above that transport.

This allows the bridge to test actual SDK behavior without requiring a broker.
Construction

Tests verify:

    Valid dependency construction.
    Invalid settings.
    Invalid metrics.
    Invalid lifecycle objects.
    Invalid discovery caches.
    Initial disconnected state.

Connection

Tests verify:

    SDK object creation.
    MQTT configuration.
    Connection behavior.
    Connection metrics.
    is_connected.

Entity registration

Tests verify:

    Entity validation.
    Connection requirements.
    Discovery publication.
    Discovery cache updates.
    Registration metrics.
    Duplicate registration suppression.

Command callbacks

Tests verify:

    Callback registration.
    Incoming MQTT command routing.
    Correct callback invocation.
    Callback error handling.
    Error metrics.

Availability

Tests verify:

    Online/offline publication.
    Availability topic handling.
    Registration requirements.
    set_all_offline().
    Per-entity failure isolation.

State publishing

Tests verify:

    State-topic resolution.
    Domains without state topics.
    Direct topic publishing.
    Publish failures being converted into bridge errors.

Shutdown

Tests verify:

    Clean disconnect.
    Calling stop() before connect().
    Calling stop() more than once.

tests/mapping/ — Mapping layer
test_device_registry.py

Tests physical-device grouping and context construction.

Important cases include:

    Single devices.
    Multi-deviceType physical devices.
    VALLHORN sibling grouping.
    Gateway handling.
    Device-name election.

The registry is particularly important because Dirigera can represent one physical product as multiple logical device records.
test_device_mapper.py

Tests the routing from a DeviceContext to the appropriate domain mapper.

Covered behavior includes:

    Known device types.
    Unknown device types.
    Mapper failures.
    Metrics.
    Flattening the resulting entity collections.

test_state_mapper.py

Tests Dirigera attribute → Home Assistant state translation.

Important cases include:

    Supported device types.
    Internal attributes that must not become HA state.
    Boolean and numeric values.
    Blind position inversion.
    Speaker volume normalization.
    Device-specific state translation.

test_command_mapper.py

Tests Home Assistant command → Dirigera REST payload translation.

Covered behavior includes:

    Controllable device types.
    Read-only devices.
    Light commands.
    JSON payload parsing.
    Blind position inversion.
    Unsupported commands.

tests/mapping/domains/ — Domain mappers

Each domain mapper is tested independently.

The tests generally verify:

    Correct entity count.
    Correct Home Assistant domain.
    Stable unique_id.
    Device information.
    Entity names.
    Device class.
    Units.
    State classes.
    Entity capabilities.
    Battery entities where applicable.
    Domain-specific configuration.

Current domain test modules are:
Test	Device type / responsibility
test_air_purifier.py	STARKVIND / air purifier
test_binary_sensor.py	Motion and water sensors
test_blind.py	Blinds/covers
test_button.py	Buttons and shortcut controllers
test_environment_sensor.py	VINDSTYRKA
test_gateway.py	DIRIGERA gateway
test_init.py	Mapper registry and shared helper functions
test_light.py	Lights and colour-temperature capabilities
test_outlet.py	Smart plugs and energy measurements
test_remote.py	Light controllers
test_sensor.py	Illuminance and other sensor entities
test_speaker.py	SYMFONISK/media players
test_switch.py	Generic switches

The test_init.py module is especially important for maintaining coverage of mapper registration and shared entity helper error paths.
tests/integration/ — Orchestrator flow

test_orchestrator_flow.py exercises the bridge's major runtime flow with dependencies controlled by test doubles.

The scenarios include:
Startup

Verifies:

    Lifecycle progression.
    Client startup.
    Initial discovery.
    Entity registration.
    Transition into the running state.

State changes

Verifies:

    Dirigera events reaching the orchestrator.
    State mapping.
    State-cache deduplication.
    HA state updates.
    Suppression of internal/non-entity attributes.

Disconnection

Verifies:

    Entities being marked unavailable.
    Lifecycle transition into reconnecting behavior.
    Appropriate cleanup.

Reconnection

Verifies:

    Re-discovery.
    State replay.
    Recovery of the application flow.

Device removal

Verifies:

    Device removal events.
    Cache cleanup.
    Removal of stale registrations/state.

Commands

Verifies:

    HA command callbacks.
    Command mapping.
    Correct REST payloads.
    REST client invocation.

Shutdown

Verifies:

    Lifecycle transition to stopped.
    Client shutdown.
    Entities being marked offline.
    Clean completion of the orchestrator.

Coverage
Terminal coverage

Run:

make coverage

This runs pytest with coverage enabled and prints missing lines directly in the terminal.

Equivalent:

python -m pytest \
  --cov \
  --cov-report=term-missing \
  tests/

The coverage configuration measures the application package and enables branch coverage.
HTML coverage

Run:

make coverage-html

The generated report is:

htmlcov/index.html

Open that file in a browser to inspect:

    File-level coverage.
    Line coverage.
    Missing lines.
    Branch coverage.
    Individual source files.

CI coverage requirement

The CI pipeline currently enforces:

--cov-fail-under=100

Therefore, a pull request must maintain 100% coverage to pass CI.

Run the same requirement locally with:

python -m pytest \
  --cov \
  --cov-report=term-missing \
  --cov-fail-under=100 \
  -v

When adding new code, add the corresponding tests in the same change.
Code quality checks

Testing is only one part of the development workflow.
Ruff

Run:

make lint

This checks both application and test code.

The CI workflow also runs Ruff linting and formatting checks.

To check formatting directly:

ruff format . --check --diff

To format the project:

ruff format .

MyPy

Run:

make typecheck

The project uses strict MyPy checking.

The CI workflow runs MyPy against the application package as an independent check.
Recommended local validation

Before opening a pull request:

make test
make lint
make typecheck

For the strongest local equivalent of the CI test requirement:

python -m pytest \
  --cov \
  --cov-report=term-missing \
  --cov-fail-under=100 \
  -v

ruff check .
ruff format . --check --diff
mypy app

Adding a new test
Adding a test for a core module

    Create or update the corresponding file under tests/core/.
    Instantiate the real class where practical.
    Avoid mocking pure application logic.
    Add @pytest.mark.unit when the test is a pure unit test.
    Cover both normal and error paths.
    Run the affected test file.
    Run the full coverage suite before committing.

Example:

python -m pytest tests/core/test_new_module.py -v

Adding a new device-domain mapper

When adding a new Dirigera device type:

    Add the production mapper under app/mapping/domains/.
    Create:

tests/mapping/domains/test_<device_type>.py

    Test the mapper with representative DeviceContext and DeviceInfo values.
    Verify the entity count.
    Verify HA domains.
    Verify unique_id values.
    Verify device information.
    Verify domain-specific configuration.
    Test invalid or missing input where the mapper validates it.
    Add a real Dirigera payload fixture if the new device has a representative discovery payload.
    Verify that the device type is registered in the mapper registry.
    Run the full suite with 100% coverage.

Follow an existing mapper test such as:

tests/mapping/domains/test_switch.py

or:

tests/mapping/domains/test_light.py

as the structural starting point.
Adding a new Dirigera payload fixture

Real discovery payloads belong in:

tests/conftest.py

Prefer a session-scoped fixture:

@pytest.fixture(scope="session")
def my_device_raw() -> dict[str, Any]:
    return {
        ...
    }

Keep the payload representative of the actual Dirigera API response.

Useful payload fixtures should exercise something that a simplified synthetic fixture would not, such as:

    Optional attributes.
    Multiple device types.
    relationId.
    Battery information.
    Real capability lists.
    Device-specific measurements.
    Empty or unusual names.
    Nested attributes.

Adding a REST client test

Keep the HTTP layer mocked.

Do not make tests depend on a physical Dirigera hub.

Tests should verify the HTTP request the client constructs and how it handles the response.

Cover both successful and failure responses where applicable.
Adding a WebSocket client test

Mock the WebSocket connection and feed representative messages into the client.

Verify:

    Event creation.
    Attribute extraction.
    Device add/remove behavior.
    Error handling.
    Connection/reconnection behavior.
    Shutdown.

Do not make a unit test dependent on a live Dirigera hub.
Adding an HA client test

Prefer the existing FakeMQTTClient pattern in:

tests/ha/test_ha_client.py

This is preferable to mocking the entire HA MQTT SDK because it allows the bridge to exercise the real SDK's:

    Topic generation.
    Discovery handling.
    Entity validation.
    Command routing.
    State/availability behavior.

Only replace the transport boundary with the fake client.
Adding an orchestrator test

Add scenarios to:

tests/integration/test_orchestrator_flow.py

Use the shared mocked clients from conftest.py.

The test should concentrate on orchestration and event routing rather than repeating lower-level tests already covered elsewhere.
Testing configuration changes

If configuration fields are added or changed, update:

tests/config/test_config.py
tests/conftest.py

The valid_env fixture should represent a complete valid configuration.

Also add tests for:

    The new default.
    Required-field behavior, if applicable.
    Invalid values.
    Cross-field validation.
    Secret redaction, if applicable.

Do not allow tests to read the developer's real .env.
Testing error paths

This project aims for complete coverage, including error handling.

When adding a new try/except, validation branch, fallback, or error condition, add a test for that path.

For example:

with pytest.raises(DirigeraBridgeError) as exc_info:
    ...

Then verify the specific error code where appropriate:

assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

Avoid writing tests that merely execute a line without verifying its behavior.
Testing asynchronous code

The project uses pytest-asyncio with:

asyncio_mode = auto

Async tests can therefore be written directly as:

@pytest.mark.asyncio
async def test_something() -> None:
    result = await some_async_function()

    assert result == expected

Async fixtures should use async def when they perform asynchronous setup or teardown.

For mocked asynchronous methods, use AsyncMock:

client.connect = AsyncMock()

Then verify calls with:

client.connect.assert_awaited_once()

Avoid creating coroutines that are never awaited. Such mistakes can result in PytestUnraisableExceptionWarning or other asynchronous cleanup warnings.
Testing without real infrastructure

Normal tests should not require:

    A Dirigera hub.
    A Dirigera access token.
    Home Assistant.
    A real MQTT broker.
    Docker.
    A particular local network.

The only external infrastructure used by the CI workflow is a Mosquitto service container. The bridge's HA client tests replace the MQTT transport with an in-memory fake, so the test suite itself remains deterministic and does not require a real MQTT connection.

This distinction is important: adding a Docker service to CI does not mean individual tests should start depending on that service.
Troubleshooting
ModuleNotFoundError: No module named 'dirigera_bridge'

Install the project in editable mode:

python -m pip install -e ".[dev]"

Then verify:

python -c "import dirigera_bridge; print(dirigera_bridge)"

ModuleNotFoundError for an application dependency

Install the complete project rather than installing only pytest:

python -m pip install -e ".[dev]"

This installs both application and development dependencies.
Tests accidentally use values from .env

The shared isolate_dotenv fixture is specifically intended to prevent this.

If a configuration test behaves differently depending on which machine runs it, check that the test is using valid_env and that dotenv loading has not been bypassed.
PytestUnraisableExceptionWarning

Check asynchronous fixtures and mocked coroutines.

Common causes include:

    An async def function being called without await.
    An AsyncMock coroutine not being awaited.
    A coroutine being created while mocking asyncio.run().
    An async fixture using the wrong fixture style.

Run the affected test with:

python -m pytest path/to/test.py -vv -s

Coverage drops below 100%

Run:

make coverage

or:

python -m pytest \
  --cov \
  --cov-report=term-missing \
  tests/

Look at the reported missing lines and add behavior-focused tests for them.

For branches that are intentionally difficult to reach, prefer a targeted test that exercises the real branch rather than excluding the code from coverage.
MyPy failures

Run:

make typecheck

or:

mypy app

The project uses strict checking, so new code should include appropriate type annotations.
Ruff failures

Run:

make lint

For formatting:

ruff format .

Then verify:

ruff format . --check --diff

Test and CI checklist

Before committing a change:

    New behavior has corresponding tests.
    Error paths are tested where applicable.
    New device types have domain mapper tests.
    Real payload fixtures are added when they provide meaningful coverage.
    No test requires a real Dirigera hub.
    No test requires a real Home Assistant instance.
    No test depends on a developer's .env.
    Async operations are properly awaited.
    make test passes.
    Coverage remains at 100%.
    make lint passes.
    make typecheck passes.

CI

The repository's GitHub Actions CI currently runs the test suite against:

Python 3.12
Python 3.13
Python 3.14

The CI test job:

    Checks out the repository.
    Installs the project with development dependencies.
    Runs pytest.
    Collects coverage.
    Requires at least 100% coverage.
    Produces a coverage XML report.

Separate workflows also run Ruff and MyPy.

This means a successful local:

make test
make lint
make typecheck

is a good first validation, but the CI matrix remains the final compatibility check.
Useful development commands
Command	Purpose
make test	Run all tests
make test-unit	Run unit tests
make test-integration	Run integration tests
make coverage	Run tests with terminal coverage
make coverage-html	Generate HTML coverage
make test-file FILE=...	Run one test file
make test-k KEY=...	Run tests matching a keyword
make lint	Run Ruff
make typecheck	Run MyPy
make clean	Remove generated test/build artefacts
make help	Show available Make targets
Relationship to the application documentation

README.md documents the application itself: architecture, supported devices, configuration, deployment, operation, and development concepts.

This document is specifically for contributors working on the test suite.

For application setup and runtime documentation, see:

README.md

For package metadata and development dependencies, see:

pyproject.toml
requirements-dev.txt

For pytest configuration, see:

pytest.ini

For the available test commands, see:

Makefile
