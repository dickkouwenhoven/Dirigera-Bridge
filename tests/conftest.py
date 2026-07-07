"""
conftest.py

Shared pytest fixtures for the Dirigera MQTT Bridge test suite.

Fixtures are organized into four groups:
    1. Core infrastructure   — event_bus, lifecycle, metrics, caches
    2. Settings              — valid Settings object with test values
    3. Mock clients          — lightweight mock objects for network clients
    4. Real Dirigera payloads — raw dicts from actual discovery output
                                used for model parsing and mapper tests
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.discovery_cache import DiscoveryCache
from app.core.event_bus import AsyncEventBus
from app.core.lifecycle import ServiceLifecycle
from app.core.metrics import MetricsStore
from app.core.state_cache import StateCache


# ── Settings fixture ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_dotenv(monkeypatch) -> None:
    """
    Prevent load_settings() from reading any real .env file on disk.

    Without this, load_dotenv() backfills whatever a test's
    monkeypatch.delenv() just removed from os.environ straight from
    the developer's real .env file — silently defeating tests for
    "missing required field" behaviour, and leaking real production
    values (Dirigera token, IP, etc.) into "populated" assertions.
    Tests must get 100% of their environment from monkeypatch, never
    from whatever happens to exist on disk.
    """

    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture
def valid_env(monkeypatch) -> None:
    """
    Set all required and optional environment variables to valid test values.

    Use this fixture in tests that call load_settings() or get_settings().
    """

    env = {
        "DIRIGERA_IP": "192.168.1.100",
        "DIRIGERA_TOKEN": "test_token_abc123",
        "MQTT_HOST": "mqtt",
        "MQTT_PORT": "1883",
        "MQTT_USER": "hauser",
        "MQTT_PASSWORD": "testpassword",
        "MQTT_CLIENT_ID": "dirigera-bridge-test",
        "MQTT_KEEPALIVE": "60",
        "MQTT_BASE_TOPIC": "dirigera",
        "MQTT_QOS": "1",
        "DISCOVERY_PREFIX": "homeassistant",
        "LOG_LEVEL": "DEBUG",
        "METRICS_INTERVAL": "60",
        "WS_PING_INTERVAL": "30",
        "WS_PING_TIMEOUT": "10",
        "RECONNECT_DELAY_INITIAL": "0.1",
        "RECONNECT_DELAY_MAX": "1.0",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings(valid_env):
    """
    Return a fully validated Settings object with test values.

    Depends on valid_env to ensure all environment variables are set.
    Resets the config singleton after each test.
    """

    from app.config import load_settings

    return load_settings()


@pytest.fixture(autouse=True)
def reset_settings_singelton():
    import app.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None


# ── Core infrastructure fixtures ──────────────────────────────────────────────


@pytest.fixture
def event_bus() -> AsyncEventBus:
    """Fresh AsyncEventBus with no subscribers."""
    return AsyncEventBus()


@pytest.fixture
def lifecycle() -> ServiceLifecycle:
    """Fresh ServiceLifecycle in CREATED state."""
    return ServiceLifecycle()


@pytest.fixture
def metrics() -> MetricsStore:
    """Fresh MetricsStore with all counters at zero."""
    return MetricsStore()


@pytest.fixture
def state_cache() -> StateCache:
    """Fresh empty StateCache."""
    return StateCache()


@pytest.fixture
def discovery_cache() -> DiscoveryCache:
    """Fresh empty DiscoveryCache."""
    return DiscoveryCache()


# ── Mock client fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def mock_ha_client() -> MagicMock:
    """
    Mock HAClient with all async methods as AsyncMock.

    Useful for orchestrator and integration tests where the MQTT
    connection should not be established.
    """

    client = MagicMock()
    client.connect = AsyncMock()
    client.stop = AsyncMock()
    client.register_entity = AsyncMock()
    client.update_state = AsyncMock()
    client.update_state_direct = AsyncMock()
    client.update_availability = AsyncMock()
    client.set_all_offline = AsyncMock()
    client.is_connected = True
    return client


@pytest.fixture
def mock_ws_client() -> MagicMock:
    """
    Mock DirigeraWebSocketClient with all async methods as AsyncMock.
    """

    client = MagicMock()
    client.connect = AsyncMock()
    client.stop = AsyncMock()
    client.is_connected = True
    return client


@pytest.fixture
def mock_rest_client() -> MagicMock:
    """
    Mock DirigeraRestClient with all async methods as AsyncMock.

    get_devices() returns an empty list by default — override in
    individual tests via mock_rest_client.get_devices.return_value = [...]
    """

    client = MagicMock()
    client.get_devices = AsyncMock(return_value=[])
    client.send_command = AsyncMock()
    client.close = AsyncMock()
    return client


# ── Real Dirigera payload fixtures ────────────────────────────────────────────
# These are exact payloads from a real Dirigera discovery response.
# Used by model parsing tests, device_registry tests, and mapper tests.


@pytest.fixture(scope="session")
def light_raw() -> Dict[str, Any]:
    """Real TRADFRI GU10 CWS light payload."""
    return {
        "id": "f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1",
        "type": "light",
        "deviceType": "light",
        "createdAt": "2025-11-18T11:25:19.000Z",
        "isReachable": False,
        "lastSeen": "2026-01-30T09:18:17.000Z",
        "customIcon": "products_led_bulb",
        "attributes": {
            "customName": "Raamverlichting",
            "model": "TRADFRI bulb GU10 CWS 345lm",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "1.0.44",
            "hardwareVersion": "1",
            "serialNumber": "94A081FFFE049D9C",
            "productCode": "LED2110R3",
            "isOn": True,
            "lightLevel": 84,
            "colorHue": 29.999024463361252,
            "colorSaturation": 0.6414288176740123,
            "colorTemperature": 2967,
            "colorTemperatureMin": 4000,
            "colorTemperatureMax": 2202,
            "colorMode": "color",
        },
        "capabilities": {
            "canSend": [],
            "canReceive": [
                "customName",
                "isOn",
                "lightLevel",
                "colorTemperature",
                "colorHue",
                "colorSaturation",
            ],
        },
        "room": {
            "id": "fa24b2a7-4194-4bfc-a9fa-a1f301efc284",
            "name": "Woonkamer",
            "color": "ikea_green_no_65",
            "icon": "rooms_sofa",
        },
        "deviceSet": [],
        "remoteLinks": ["f0c0163b-baf5-4bb5-b5d2-e4ca87017228_1"],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def vallhorn_motion_raw() -> Dict[str, Any]:
    """Real VALLHORN motionSensor (_1 sibling) payload."""
    return {
        "id": "fff75d00-607c-4f23-a0e7-3dbed0e18b12_1",
        "relationId": "fff75d00-607c-4f23-a0e7-3dbed0e18b12",
        "type": "sensor",
        "deviceType": "motionSensor",
        "isReachable": True,
        "attributes": {
            "customName": "Bewegingssensor Gang",
            "model": "VALLHORN Wireless Motion Sensor",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "1.0.64",
            "hardwareVersion": "1",
            "serialNumber": "348D13FFFE3F55F1",
            "productCode": "E2134",
            "batteryPercentage": 70,
            "isOn": False,
            "isDetected": False,
            "motionDetectedDelay": 20,
        },
        "capabilities": {"canSend": [], "canReceive": ["customName"]},
        "room": {
            "id": "017495a4-e784-4b4a-ab17-6ad345f732cb",
            "name": "Gang",
            "color": "pantone_16_0230_tcx",
            "icon": "rooms_coat_hanger",
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def vallhorn_light_raw() -> Dict[str, Any]:
    """Real VALLHORN lightSensor (_3 sibling) payload."""
    return {
        "id": "fff75d00-607c-4f23-a0e7-3dbed0e18b12_3",
        "relationId": "fff75d00-607c-4f23-a0e7-3dbed0e18b12",
        "type": "unknown",
        "deviceType": "lightSensor",
        "isReachable": True,
        "attributes": {
            "customName": "",
            "model": "VALLHORN Wireless Motion Sensor",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "1.0.64",
            "hardwareVersion": "1",
            "serialNumber": "348D13FFFE3F55F1",
            "productCode": "E2134",
            "illuminance": 0,
        },
        "capabilities": {"canSend": [], "canReceive": ["customName"]},
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def vindstyrka_raw() -> Dict[str, Any]:
    """Real VINDSTYRKA environmentSensor payload."""
    return {
        "id": "85fe4485-7c1e-4e86-9eb1-f1aa856a1e66_1",
        "type": "sensor",
        "deviceType": "environmentSensor",
        "isReachable": True,
        "attributes": {
            "customName": "Hygrometer Woonkamer",
            "model": "VINDSTYRKA",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "1.0.11",
            "hardwareVersion": "1",
            "serialNumber": "70C59CFFFE9BE98B",
            "productCode": "E2112",
            "currentTemperature": 20,
            "currentRH": 50,
            "currentPM25": 3,
            "maxMeasuredPM25": 999,
            "minMeasuredPM25": 0,
            "vocIndex": 158,
        },
        "capabilities": {"canSend": [], "canReceive": ["customName"]},
        "room": {
            "id": "fa24b2a7-4194-4bfc-a9fa-a1f301efc284",
            "name": "Woonkamer",
            "color": "ikea_green_no_65",
            "icon": "rooms_sofa",
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def outlet_raw() -> Dict[str, Any]:
    """Real INSPELNING smart plug payload."""
    return {
        "id": "0acd598b-6bcb-46ba-8aa0-0fd035b678f6_1",
        "type": "outlet",
        "deviceType": "outlet",
        "isReachable": True,
        "attributes": {
            "customName": "Computer Stekker",
            "model": "INSPELNING Smart plug",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "2.4.45",
            "hardwareVersion": "1",
            "serialNumber": "54DCE9FFFEAE067A",
            "productCode": "E2206",
            "isOn": True,
            "currentVoltage": 226.6,
            "currentAmps": 0.003,
            "currentActivePower": 0,
            "totalEnergyConsumed": 2.97,
            "childLock": False,
        },
        "capabilities": {
            "canSend": [],
            "canReceive": ["customName", "isOn"],
        },
        "room": {
            "id": "860c9e4b-0438-4a93-a186-b7ac8697794f",
            "name": "Werkkamer",
            "color": "ikea_blue_no_58",
            "icon": "rooms_desk",
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def gateway_raw() -> Dict[str, Any]:
    """Real Dirigera gateway payload."""
    return {
        "id": "9d3b17d8-73c0-4f33-9637-e8ee2437acd3_1",
        "relationId": "9d3b17d8-73c0-4f33-9637-e8ee2437acd3",
        "type": "gateway",
        "deviceType": "gateway",
        "isReachable": True,
        "attributes": {
            "customName": "Ikea Hub",
            "model": "DIRIGERA Hub for smart products",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "2.815.2",
            "hardwareVersion": "P2.5",
            "serialNumber": "9d3b17d8-73c0-4f33-9637-e8ee2437acd3",
            "otaStatus": "updateAvailable",
            "otaState": "readyToUpdate",
            "backendConnected": True,
            "homestate": "home",
            "timezone": "Europe/Amsterdam",
            "nextSunRise": "2026-01-31T07:17:00.000Z",
            "nextSunSet": "2026-01-30T16:19:00.000Z",
            "coordinates": {
                "latitude": 51.873873873873876,
                "longitude": 6.245366556398994,
                "accuracy": -1,
            },
            "isOn": False,
        },
        "capabilities": {
            "canSend": [],
            "canReceive": ["customName", "permittingJoin"],
        },
        "deviceSet": [],
        "remoteLinks": [],
    }


@pytest.fixture(scope="session")
def remote_raw() -> Dict[str, Any]:
    """Real Remote Control N2 (E2001) payload."""
    return {
        "id": "315cebe3-06b1-4fe0-95c5-e8a8e086497c_1",
        "type": "controller",
        "deviceType": "lightController",
        "isReachable": True,
        "attributes": {
            "customName": "Lichtschakelaar Eetkamer",
            "model": "Remote Control N2",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "2.4.16",
            "hardwareVersion": "1",
            "serialNumber": "94DEB8FFFEC13C56",
            "productCode": "E2001",
            "batteryPercentage": 90,
            "isOn": False,
            "lightLevel": 1,
        },
        "capabilities": {
            "canSend": ["isOn", "lightLevel"],
            "canReceive": ["customName"],
        },
        "room": {
            "id": "6d1af5bc-0252-4a59-aa6e-d48d898f82a6",
            "name": "Eetkamer",
            "color": "ikea_lilac_no_3",
            "icon": "rooms_cutlery",
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }


@pytest.fixture(scope="session")
def water_sensor_raw() -> Dict[str, Any]:
    """Real BADRING Water Leakage Sensor payload."""
    return {
        "id": "967f65f3-81f2-4b1b-94c9-98fed7effe7c_1",
        "type": "sensor",
        "deviceType": "waterSensor",
        "isReachable": True,
        "attributes": {
            "customName": "Water Sensor",
            "model": "BADRING Water Leakage Sensor",
            "manufacturer": "IKEA of Sweden",
            "firmwareVersion": "1.0.7",
            "hardwareVersion": "1",
            "serialNumber": "94A081FFFE4EBE3E",
            "productCode": "E2202",
            "batteryPercentage": 70,
            "waterLeakDetected": False,
        },
        "capabilities": {"canSend": [], "canReceive": ["customName"]},
        "room": {
            "id": "ee27dd80-faee-47a8-8b5a-7e68fba7de0e",
            "name": "Waskamer",
            "color": "pantone_16_0940_tcx",
            "icon": "rooms_washing_machine",
        },
        "deviceSet": [],
        "remoteLinks": [],
        "isHidden": False,
    }
