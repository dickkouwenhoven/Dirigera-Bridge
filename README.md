# Dirigera MQTT Bridge

A Python service that bridges an **IKEA Dirigera hub** to **Home Assistant** via **MQTT**.

The bridge connects to the Dirigera hub over WebSocket (real-time events), forwards device state changes to Home Assistant via MQTT discovery,
and translates Home Assistant commands back to the Dirigera REST API.

---

## Architecture overview

```
┌─────────────────┐     WebSocket       ┌─────────────────────────┐
│  Dirigera Hub   │ ◄─────────────────► │                         │
│  (IKEA)         │                     │   dirigera-mqtt-bridge  │
│                 │ ◄── REST (PATCH) ── │                         │
└─────────────────┘                     └──────────┬──────────────┘
                                                   │ MQTT
                                                   ▼
                                         ┌──────────────────┐
                                         │   Mosquitto      │
                                         │   MQTT Broker    │
                                         └──────────┬───────┘
                                                    │ MQTT discovery
                                                    ▼
                                         ┌──────────────────┐
                                         │  Home Assistant  │
                                         └──────────────────┘
```

### Four-layer design

| Layer                   | Responsibility                                                                                                        |
|-------------------------|-----------------------------------------------------------------------------------------------------------------------|
| **Dirigera layer**      | WebSocket listener + REST client. Knows nothing about MQTT or HA.                                                     |
| **Mapping layer**       | Translates Dirigera device models → HA entities and events → state payloads. Plugin-based — one file per device type. |
| **MQTT layer**          | Owned entirely by the HASDK (`AsyncEntityManager`). No raw aiomqtt calls in application code.                         |
| **Orchestration layer** | Wires all layers together. Manages startup, shutdown, reconnect, and event routing.                                   |

---

## Supported devices

All device types supported by the Dirigera hub are handled. Devices confirmed from real discovery data:

| Dirigera deviceType             | HA domain(s)                                        | Example device                |
|---------------------------------|-----------------------------------------------------|-------------------------------|
| `light`                         | `light`                                             | TRADFRI bulb GU10 CWS / WS    |
| `outlet`                        | `switch` + `sensor` ×4                              | INSPELNING Smart plug (E2206) |
| `lightController`               | `event` + `sensor` (battery)                        | Remote Control N2 (E2001)     |
| `motionSensor`                  | `binary_sensor` + `sensor` (battery)                | VALLHORN (E2134)              |
| `lightSensor`                   | `sensor` (illuminance)                              | VALLHORN sibling              |
| `waterSensor`                   | `binary_sensor` + `sensor` (battery)                | BADRING (E2202)               |
| `environmentSensor`             | `sensor` ×4                                         | VINDSTYRKA (E2112)            |
| `gateway`                       | `binary_sensor` ×2 + `sensor` ×7 + `device_tracker` | DIRIGERA Hub                  |
| `blind` / `blinds`              | `cover`                                             | PRAKTLYSING / KADRILJ         |
| `airPurifier`                   | `fan` + `sensor` ×2                                 | STARKVIND (E2007)             |
| `speaker`                       | `media_player`                                      | SYMFONISK                     |
| `switch`                        | `switch`                                            | Generic switch                |
| `button` / `shortcutController` | `event` + `sensor` (battery)                        | SOMRIG (E2213)                |

Multi-deviceType physical devices (e.g. VALLHORN with `motionSensor` + `lightSensor`) are automatically grouped under one physical device in Home Assistant using
the `relationId` from the Dirigera discovery payload.

---

## Project structure

```
dirigera-mqtt-bridge/
├── .env                            ← Runtime configuration (never commit)
├── Dockerfile                      ← Two-stage ARM64 build
├── docker-compose.yml              ← Service definition
├── requirements.txt                ← Python dependencies
├── README.md
├── main.py                         ← Entrypoint + composition root
└── app/
    ├── config.py                   ← Typed settings from .env
    ├── orchestrator.py             ← Service lifecycle + event routing
    ├── core/
    │   ├── errors.py               ← Centralised error types
    │   ├── event_bus.py            ← Async internal pub/sub
    │   ├── lifecycle.py            ← Service state machine
    │   ├── metrics.py              ← In-memory counters
    │   ├── retry.py                ← Exponential backoff utility
    │   ├── state_cache.py          ← Device state deduplication cache
    │   └── discovery_cache.py      ← HA entity registration cache
    ├── dirigera/
    │   ├── models.py               ← Pydantic models for Dirigera payloads
    │   ├── rest_client.py          ← Dirigera REST API (PATCH commands)
    │   └── websocket_client.py     ← Dirigera WebSocket event stream
    ├── mapping/
    │   ├── device_registry.py      ← Groups logical devices by physical device
    │   ├── device_mapper.py        ← Routes DeviceContext → HA entities
    │   ├── state_mapper.py         ← Dirigera attribute → HA state payload
    │   ├── command_mapper.py       ← HA command → Dirigera REST payload
    │   └── domains/
    │       ├── __init__.py         ← Plugin registry + shared helpers
    │       ├── gateway.py
    │       ├── light.py
    │       ├── outlet.py
    │       ├── binary_sensor.py
    │       ├── sensor.py
    │       ├── environment_sensor.py
    │       ├── remote.py
    │       ├── blind.py
    │       ├── switch.py
    │       ├── button.py
    │       ├── air_purifier.py
    │       └── speaker.py
    └── ha/
        └── ha_client.py            ← HASDK AsyncEntityManager wrapper
```

---

## Prerequisites

- Raspberry Pi 5 running Docker and Docker Compose
- IKEA Dirigera hub on the local network
- Dirigera access token (see [Obtaining the token](#obtaining-the-dirigera-token))
- Mosquitto MQTT broker running as Docker container named `mosquitto`
- Home Assistant running as Docker container
- Docker bridge network named `iot_network` shared by all containers

---

## Setup

### 1. Clone the repository

```bash
cd /home/dickkouwenhoven/DockerProjects/Ikea
git clone <repository-url> DirigeraApi
cd DirigeraApi
```

### 2. Install the HASDK

The HASDK is now available on PyPI and is installed via `requirements.xt`:

```bash
pip install ha_mqtt_sdk
```

When building the Docker image, `ha_mqtt_sdk` is pulled from PyPI during the build - no
local `sdk_src/` directory is needed.

### 3. Configure the environment

```bash
cp .env .env.local   # optional backup
```

Edit `.env` and fill in your values:

```env
DIRIGERA_IP=192.168.1.XXX       # your hub's IP address
DIRIGERA_TOKEN=your_token_here  # see section below
```

All other values have sensible defaults. See `.env` for the full list with descriptions.

### 4. Create the Docker network (once)

```bash
docker network create iot_network
```

If the network already exists from your Home Assistant or Mosquitto stack, skip this step.

### 5. Build and start

```bash
docker compose up -d --build
```

### 6. Verify it is running

```bash
docker compose logs -f dirigera-bridge
```

A successful startup looks like:

```
INFO     app.orchestrator   Orchestrator: startup complete — bridge is RUNNING.
INFO     app.orchestrator   Supported device types: ['airPurifier', 'blind', ...]
```

---

## Obtaining the Dirigera token

The Dirigera hub uses a token-based API. To obtain your token:

1. Install the [dirigera Python library](https://github.com/Leggin/dirigera):
   ```bash
   pip install dirigera
   ```

2. Run the authentication flow:
   ```bash
   python -c "import dirigera; dirigera.create_token('192.168.1.XXX')"
   ```
   Replace `192.168.1.XXX` with your hub's IP address.

3. Follow the on-screen instructions — you will be asked to press the action button on the hub.

4. Copy the token into your `.env` file as `DIRIGERA_TOKEN`.

---

## Adding a new device type

The mapping layer is plugin-based. To add support for a new Dirigera device type:

1. Create a new file in `app/mapping/domains/`, e.g. `my_device.py`
2. Implement a mapper function:
   ```python
   def map_my_device(context: DeviceContext, device_info: DeviceInfo) -> List[Entity]:
       ...
   ```
3. Add a `DEVICE_TYPES` dict at the bottom:
   ```python
   DEVICE_TYPES = {
       "myDeviceType": map_my_device,
   }
   ```
4. Add the module to `_DOMAIN_MODULES` in `app/mapping/domains/__init__.py`
5. Add state translation to `app/mapping/state_mapper.py`
6. Add command translation to `app/mapping/command_mapper.py` (if controllable)

Nothing else needs to change.

---

## Configuration reference

All settings are loaded from `.env` at startup. Invalid or missing required values cause an immediate error with a clear message.

| Variable                  | Required | Default           | Description                                 |
|---------------------------|----------|-------------------|---------------------------------------------|
| `DIRIGERA_IP`             | ✓        | —                 | IP address of the Dirigera hub              |
| `DIRIGERA_TOKEN`          | ✓        | —                 | Hub access token (secret)                   |
| `MQTT_HOST`               | ✓        | —                 | MQTT broker hostname                        |
| `MQTT_PORT`               |          | `1883`            | MQTT broker port                            |
| `MQTT_USER`               | ✓        | —                 | MQTT username                               |
| `MQTT_PASSWORD`           | ✓        | —                 | MQTT password (secret)                      |
| `MQTT_CLIENT_ID`          |          | `dirigera-bridge` | MQTT client identifier                      |
| `MQTT_KEEPALIVE`          |          | `60`              | MQTT keepalive interval (seconds)           |
| `MQTT_BASE_TOPIC`         |          | `dirigera`        | Base topic prefix for state/command topics  |
| `MQTT_QOS`                |          | `1`               | MQTT QoS level (0, 1, or 2)                 |
| `DISCOVERY_PREFIX`        |          | `homeassistant`   | HA MQTT discovery prefix                    |
| `LOG_LEVEL`               |          | `INFO`            | Logging level                               |
| `METRICS_INTERVAL`        |          | `300`             | Seconds between metrics log snapshots       |
| `WS_PING_INTERVAL`        |          | `30`              | WebSocket keepalive ping interval (seconds) |
| `WS_PING_TIMEOUT`         |          | `10`              | WebSocket pong timeout (seconds)            |
| `RECONNECT_DELAY_INITIAL` |          | `1.0`             | Initial reconnect backoff delay (seconds)   |
| `RECONNECT_DELAY_MAX`     |          | `60.0`            | Maximum reconnect backoff delay (seconds)   |

---

## Resilience

The bridge is designed to recover automatically from network failures:

- **Dirigera WebSocket** — reconnects with exponential backoff (1s → 60s) when the hub connection drops. On reconnect, all devices are re-discovered and state is replayed to HA.
- **MQTT broker**        — reconnects with exponential backoff when the broker is unavailable. Entity registrations are not re-sent if the entity was already registered in the
-                          current session (discovery cache).
- **Availability**       — all entities are marked offline in HA when the Dirigera connection drops and marked online again on reconnect.
- **Shutdown**           — `SIGINT` and `SIGTERM` trigger a graceful shutdown: entities are marked offline, connections are closed cleanly, and a final metrics snapshot is logged.

---

## Observability

Structured log output goes to stdout and is captured by Docker's json-file logging driver with rotation (10 MB × 5 files).

A metrics snapshot is logged every `METRICS_INTERVAL` seconds (default 5 minutes):

```
INFO  app.orchestrator  Metrics snapshot: ws_messages_received=1243  mqtt_messages_published=892  mapping_state_updates=756  ...
```

To view live logs:
```bash
docker compose logs -f dirigera-bridge
```

To filter for errors only:
```bash
docker compose logs dirigera-bridge | grep ERROR
```

---

## Security notes

- The `.env` file contains secrets and must never be committed to version control. Add it to `.gitignore`.
- The bridge runs as a non-root user (`bridge`, uid 1001) inside the container.
- The Dirigera token and MQTT password are redacted from all log output.
- No ports are exposed externally — the bridge communicates only on the internal `iot_network`.
- SSL certificate verification is disabled for the Dirigera hub connection because the hub uses a self-signed certificate. This is expected and safe for local network communication.

---

## Dependencies

| Package                | Version | Purpose                       |
|------------------------|---------|-------------------------------|
| `websockets`           | ≥12.0   | Dirigera WebSocket connection |
| `aiohttp`              | ≥3.9    | Dirigera REST API             |
| `aiomqtt`              | ≥2.0    | MQTT transport (via HASDK)    |
| `pydantic`             | ≥2.0    | Dirigera payload validation   |
| `python-dotenv`        | ≥1.0    | `.env` file loading           |
| `HomeAssistantMQTTSdk` | local   | HA entity lifecycle via MQTT  |

---

## License

Private project — all rights reserved.
