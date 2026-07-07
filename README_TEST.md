# Dirigera MQTT Bridge — Test Suite

This document describes how to run the test suite, what is covered,
and how the tests are organised.

---

## Quick start

```bash
# Install test dependencies (once)
make install

# Run all tests
make test

# Run with coverage report
make coverage

# Run only unit tests (fast)
make test-unit

# Run only integration tests
make test-integration

# Run a specific file
make test-file FILE=tests/core/test_errors.py

# Run tests matching a keyword
make test-k KEY=lifecycle
```

---

## Prerequisites

The test suite requires the following packages. Install them with
`make install` or manually:

```bash
pip install pytest pytest-asyncio pytest-cov
```

The application dependencies (`pydantic`, `aiohttp`, `websockets`,
`aiomqtt`, `python-dotenv`) must also be installed. The HASDK
(`HomeAssistantMQTTSdk`) must be installed from `./sdk_src/`.

No real network connections are required — all external dependencies
are mocked.

---

## Running tests

### All tests

```bash
make test
# or directly:
python3 -m pytest tests/
```

### Unit tests only

Unit tests have no external dependencies and run in milliseconds.

```bash
make test-unit
# or:
python3 -m pytest tests/ -m unit
```

### Integration tests only

Integration tests exercise the orchestrator with all network
clients mocked.

```bash
make test-integration
# or:
python3 -m pytest tests/ -m integration
```

### A single test file

```bash
make test-file FILE=tests/core/test_lifecycle.py
# or:
python3 -m pytest tests/core/test_lifecycle.py -v
```

### A single test class or function

```bash
# All tests in a class:
python3 -m pytest tests/core/test_lifecycle.py::TestValidTransitions -v

# A single test function:
python3 -m pytest tests/core/test_lifecycle.py::TestValidTransitions::test_created_to_starting -v
```

### Tests matching a keyword

```bash
make test-k KEY=kelvin
# or:
python3 -m pytest tests/ -k kelvin -v
```

---

## Coverage

### Terminal report

```bash
make coverage
# or:
python3 -m pytest tests/ --cov=app --cov-report=term-missing
```

### HTML report

```bash
make coverage-html
# Opens htmlcov/index.html
```

The HTML report shows line-by-line coverage for every source file.
Green = covered, red = not covered.

---

## Test structure

```
tests/
├── conftest.py                     ← shared fixtures (all tests)
├── core/
│   ├── test_errors.py              ← DirigeraBridgeError, ErrorCode
│   ├── test_event_bus.py           ← AsyncEventBus, DirigeraEvent, EventType
│   ├── test_lifecycle.py           ← ServiceLifecycle, LifecycleState
│   ├── test_metrics.py             ← MetricsStore, MetricName
│   ├── test_retry.py               ← RetryConfig, retry_with_backoff
│   ├── test_state_cache.py         ← StateCache
│   └── test_discovery_cache.py     ← DiscoveryCache, RegistrationRecord
├── config/
│   └── test_config.py              ← load_settings, get_settings, Settings
├── dirigera/
│   ├── test_models.py              ← DirigeraDevice, DirigeraWebSocketEvent
│   ├── test_rest_client.py         ← DirigeraRestClient (mocked HTTP)
│   └── test_websocket_client.py    ← DirigeraWebSocketClient (mocked WS)
├── mapping/
│   ├── test_device_registry.py     ← build_device_contexts, DeviceContext
│   ├── test_device_mapper.py       ← DeviceMapper
│   ├── test_state_mapper.py        ← StateMapper.map_state() per device type
│   ├── test_command_mapper.py      ← CommandMapper.map_command() per device type
│   └── domains/
│       ├── test_gateway.py         ← gateway domain mapper
│       ├── test_light.py           ← light mapper, capability tiers, kelvin/mireds
│       ├── test_outlet.py          ← outlet mapper, energy sensors
│       ├── test_binary_sensor.py   ← motionSensor, waterSensor mappers
│       ├── test_sensor.py          ← lightSensor mapper
│       ├── test_environment_sensor.py ← VINDSTYRKA mapper
│       ├── test_remote.py          ← lightController mapper
│       ├── test_blind.py           ← blind/cover mapper
│       ├── test_switch.py          ← switch mapper
│       ├── test_button.py          ← button/shortcutController mapper
│       ├── test_air_purifier.py    ← STARKVIND fan mapper
│       └── test_speaker.py         ← SYMFONISK media_player mapper
└── integration/
    └── test_orchestrator_flow.py   ← full startup/shutdown/event flow
```

---

## Test markers

Tests are tagged with pytest markers defined in `pytest.ini`:

| Marker | Description | Speed |
|---|---|---|
| `@pytest.mark.unit` | Pure unit test, no I/O, no mocked layers | Fast (< 1ms) |
| `@pytest.mark.integration` | Multi-layer test with mocked dependencies | Medium (< 100ms) |
| `@pytest.mark.slow` | Tests that take > 1 second | Slow |

Run by marker:
```bash
python3 -m pytest tests/ -m unit
python3 -m pytest tests/ -m integration
python3 -m pytest tests/ -m "not slow"
```

---

## Shared fixtures (`conftest.py`)

All fixtures are defined in `tests/conftest.py` and available to
every test file automatically.

### Core infrastructure

| Fixture | Type | Description |
|---|---|---|
| `event_bus` | `AsyncEventBus` | Fresh event bus, no subscribers |
| `lifecycle` | `ServiceLifecycle` | Fresh lifecycle in CREATED state |
| `metrics` | `MetricsStore` | Fresh metrics store, all counters zero |
| `state_cache` | `StateCache` | Fresh empty state cache |
| `discovery_cache` | `DiscoveryCache` | Fresh empty discovery cache |

### Settings

| Fixture | Type | Description |
|---|---|---|
| `valid_env` | None | Sets all env vars via monkeypatch |
| `settings` | `Settings` | Validated Settings object (depends on valid_env) |

### Mock clients

| Fixture | Type | Description |
|---|---|---|
| `mock_ha_client` | `MagicMock` | HAClient with AsyncMock methods |
| `mock_ws_client` | `MagicMock` | DirigeraWebSocketClient with AsyncMock methods |
| `mock_rest_client` | `MagicMock` | DirigeraRestClient returning empty device list |

### Real Dirigera payload fixtures

These are exact payloads from a real Dirigera hub. Session-scoped
(created once per test session for efficiency).

| Fixture | Device | Description |
|---|---|---|
| `light_raw` | TRADFRI GU10 CWS | Full colour light with CT range |
| `vallhorn_motion_raw` | VALLHORN _1 | motionSensor sibling with battery |
| `vallhorn_light_raw` | VALLHORN _3 | lightSensor sibling, empty customName |
| `vindstyrka_raw` | VINDSTYRKA | All 4 air quality measurements |
| `outlet_raw` | INSPELNING | Full energy monitoring outlet |
| `gateway_raw` | DIRIGERA Hub | Gateway with coordinates |
| `remote_raw` | Remote Control N2 | lightController with battery |
| `water_sensor_raw` | BADRING | waterSensor with battery |

---

## What is tested

### `tests/core/` — Infrastructure layer

| Module | Key scenarios tested |
|---|---|
| `errors.py` | All ErrorCode values unique, DirigeraBridgeError construction, format_error() |
| `event_bus.py` | Subscribe/unsubscribe, publish to multiple handlers, handler error isolation, publish_nowait |
| `lifecycle.py` | Full transition graph, invalid transitions, FAILED from all states, callbacks |
| `metrics.py` | increment/get/reset, snapshot with/without zeros, log_snapshot, total_errors |
| `retry.py` | Delay sequence, capping, jitter, RetryExhaustedError, stop_event interruption |
| `state_cache.py` | First write / unchanged / changed, zero/None values, clear_device, snapshot |
| `discovery_cache.py` | Single and multi-deviceType grouping, unregister sibling, relation index |

### `tests/config/` — Configuration

| Scenario | Tested |
|---|---|
| All required fields | Present → Settings object returned |
| Missing required fields | Each field individually → CONFIG_MISSING_FIELD |
| Invalid values | Port range, QoS range, log level, intervals |
| Cross-field validation | reconnect max < initial, ping timeout ≥ interval |
| Immutability | Settings is frozen — attribute assignment raises |
| safe_repr() | Token and password redacted |

### `tests/dirigera/` — Dirigera layer

| Module | Key scenarios tested |
|---|---|
| `models.py` | Real payload parsing for all 7 fixture devices, is_grouped, physical_id, raw_attributes |
| `rest_client.py` | Auth headers, timeout, get_devices, send_command HTTP 200/401/404/500, close |
| `websocket_client.py` | Message handling, one event per attribute, device added/removed, SSL context |

### `tests/mapping/` — Mapping layer

| Module | Key scenarios tested |
|---|---|
| `device_registry.py` | Single device, VALLHORN grouping, gateway routing, name election |
| `device_mapper.py` | Known/unknown types, mapper errors, metrics, flat list |
| `state_mapper.py` | All 13 device types, internal attrs suppressed, blind inversion, volume normalisation |
| `command_mapper.py` | All 5 controllable types, read-only types, light JSON parsing, blind inversion |

### `tests/mapping/domains/` — Domain mappers

Every domain mapper is tested for entity count, HA domain, unique_id
format, entity names, and config fields (device_class, unit, etc.).

| File | Device | Entities |
|---|---|---|
| `test_gateway.py` | DIRIGERA Hub | 10 (2 binary + 7 sensor + 1 tracker) |
| `test_light.py` | TRADFRI bulb | 1 light, 4 capability tiers, mireds |
| `test_outlet.py` | INSPELNING | 1–5 (switch + energy sensors) |
| `test_binary_sensor.py` | VALLHORN, BADRING | 1–2 (binary + battery) |
| `test_sensor.py` | VALLHORN _3 | 1–2 (illuminance + battery) |
| `test_environment_sensor.py` | VINDSTYRKA | 0–4 sensors |
| `test_remote.py` | Remote N2 | 1–2 (event + battery) |
| `test_blind.py` | PRAKTLYSING | 1–2 (cover + battery) |
| `test_switch.py` | Generic switch | 1 switch |
| `test_button.py` | SOMRIG | 1–2 (event + battery) |
| `test_air_purifier.py` | STARKVIND | 1–3 (fan + pm25 + filter) |
| `test_speaker.py` | SYMFONISK | 1 media_player |

### `tests/integration/` — Orchestrator flow

| Scenario | Tested |
|---|---|
| Startup | Lifecycle CREATED → RUNNING, all clients connected, entities registered |
| State change | EVENT → update_state_direct(), deduplication, internal attrs suppressed |
| Disconnection | set_all_offline(), lifecycle → RECONNECTING |
| Reconnection | Re-discovery triggered |
| Device removed | Caches cleared |
| Command callback | REST send_command() called with correct payload |
| Shutdown | Lifecycle → STOPPED, all clients stopped, entities offline |

---

## Adding new tests

### New device type mapper test

1. Create `tests/mapping/domains/test_<device_type>.py`
2. Follow the pattern of an existing file (e.g. `test_switch.py`)
3. Use `MockContext`, `MockDeviceInfo`, `MockAttrs` — no real SDK needed
4. Add a real fixture test using the session-scoped payload from `conftest.py`
5. Verify `DEVICE_TYPES` registry at the end

### New core module test

1. Create `tests/core/test_<module>.py`
2. Import and instantiate the real class — no mocking needed for pure modules
3. Use `@pytest.mark.unit` on every test function

### New integration scenario

Add to `tests/integration/test_orchestrator_flow.py` following the
existing pattern — use the `orchestrator` fixture which injects all
mocked dependencies.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'sdk'`

The HASDK is not installed. Install it from `PyPI`:
```bash
pip install ha_mqtt_sdk
```

### `ModuleNotFoundError: No module named 'pydantic'`

Install application dependencies:
```bash
pip install -r requirements.txt
```

### `PytestUnraisableExceptionWarning` during async tests

Ensure `asyncio_mode = auto` is set in `pytest.ini` (already
configured). If the warning persists, check that all async fixtures
use `async def` and are not mixing sync/async incorrectly.

### Tests pass locally but fail in Docker

The Docker image uses Python 3.12 on ARM64. Ensure you are testing
with the same Python version locally:
```bash
python3 --version  # should be 3.12.x
```

---

## Coverage targets

| Package | Target | Rationale |
|---|---|---|
| `app/core/` | ≥ 95% | Pure Python, fully testable |
| `app/config.py` | ≥ 90% | Pure validation logic |
| `app/dirigera/models.py` | ≥ 90% | Pydantic parsing |
| `app/mapping/` | ≥ 85% | Pure translation logic |
| `app/ha/ha_client.py` | ≥ 60% | HASDK dependency |
| `app/dirigera/rest_client.py` | ≥ 60% | Mocked aiohttp |
| `app/dirigera/websocket_client.py` | ≥ 60% | Mocked websockets |
| `app/orchestrator.py` | ≥ 70% | Integration-level |

