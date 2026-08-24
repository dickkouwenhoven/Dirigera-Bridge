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
Dirigera-Bridge/
├── .env                            ← Runtime configuration (never commit)
├── .env.example                    ← Template with descriptions and defaults
├── Dockerfile                      ← Two-stage build (builder + runtime)
├── docker-compose.yml              ← Service definition
├── pyproject.toml                  ← Package metadata, console script, dev deps
├── README.md
└── dirigera_bridge/
    ├── __init__.py                 ← Public library API (see "Two ways to use this project")
    ├── __main__.py                 ← `python -m dirigera_bridge` entry point
    ├── main.py                     ← CLI/Docker composition root: .env, logging, signals
    ├── factory.py                  ← build_orchestrator() — the library's composition root
    ├── config.py                   ← Typed settings from .env
    ├── orchestrator.py             ← Service lifecycle + event routing
    ├── core/
    │   ├── errors.py                ← Centralised error types
    │   ├── event_bus.py             ← Async internal pub/sub
    │   ├── lifecycle.py             ← Service state machine
    │   ├── metrics.py               ← In-memory counters
    │   ├── retry.py                 ← Exponential backoff utility
    │   ├── state_cache.py           ← Device state deduplication cache
    │   └── discovery_cache.py       ← HA entity registration cache
    ├── dirigera/
    │   ├── models.py                ← Pydantic models for Dirigera payloads
    │   ├── rest_client.py           ← Dirigera REST API (PATCH commands)
    │   └── websocket_client.py      ← Dirigera WebSocket event stream
    ├── mapping/
    │   ├── device_registry.py       ← Groups logical devices by physical device
    │   ├── device_mapper.py         ← Routes DeviceContext → HA entities
    │   ├── state_mapper.py          ← Dirigera attribute → HA state payload
    │   ├── command_mapper.py        ← HA command → Dirigera REST payload
    │   └── domains/
    │       ├── __init__.py          ← Plugin registry + shared helpers
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
        └── ha_client.py             ← HASDK AsyncEntityManager wrapper
```

---

## Two ways to use this project

This project ships as a single installable package that serves two different users.

**1. As an application** — if you just want your Dirigera hub integrated with Home Assistant and nothing else, run it as-is: `docker compose up -d --build`, or `python -m dirigera_bridge` outside Docker. Everything is configured through `.env`. This is what the [Fully implementing this application](#fully-implementing-this-application) section below walks through end to end.

**2. As a library** — if you're bridging more than one hub vendor (say, Dirigera *and* Philips Hue) into the same Home Assistant instance, you don't need to run two unrelated services side by side. Install this package and import its public API directly:

```python
from dirigera_bridge import Settings, load_settings, build_orchestrator

# load_settings() reads .env, or build a Settings instance yourself
dirigera_settings = load_settings()
dirigera_orchestrator = build_orchestrator(dirigera_settings)

# construct your other vendor's bridge the same way, e.g.:
# from philips_hue_bridge import build_orchestrator as build_hue_orchestrator
# hue_orchestrator = build_hue_orchestrator(hue_settings)

await dirigera_orchestrator.run()
# await asyncio.gather(dirigera_orchestrator.run(), hue_orchestrator.run())
```

The public surface is deliberately small: `Orchestrator`, `Settings`, `load_settings`, and `build_orchestrator`. Everything `build_orchestrator()` constructs — the WebSocket/REST clients, the MQTT client, the mapping layer — is fully self-contained per call. Each `Orchestrator` owns its own MQTT connection, so running several bridges concurrently is just running several `Orchestrator`s side by side; there's no shared connection state to coordinate between vendors.

Internal modules (`dirigera_bridge.core`, `dirigera_bridge.mapping`, etc.) are technically importable too, but aren't part of the supported public API — they can change without notice between versions. Build against `dirigera_bridge.__init__`'s exports.

---

## Prerequisites

- A machine running Docker and Docker Compose (this project is built and tested for Raspberry Pi 5 / linux/arm64, but the image is portable to any Docker host)
- An IKEA Dirigera hub on the local network
- A Dirigera access token (see [Obtaining the Dirigera token](#obtaining-the-dirigera-token))
- A running MQTT broker — see [Setting up an MQTT broker](#setting-up-an-mqtt-broker) if you don't have one yet
- Home Assistant running, with its MQTT integration enabled — see [Enabling MQTT in Home Assistant](#enabling-mqtt-in-home-assistant)
- A Docker bridge network shared by the bridge, the broker, and Home Assistant (this project's `docker-compose.yml` expects one named `iot_network`)

---

## Enabling MQTT in Home Assistant

Home Assistant doesn't talk to this bridge directly — everything flows through MQTT, so Home Assistant's own MQTT integration has to be configured first, or nothing this bridge publishes will show up.

1. **Have a broker reachable from Home Assistant.** If you don't have one yet, do the [broker setup](#setting-up-an-mqtt-broker) section below first — Home Assistant's MQTT integration is a client, not a broker; it needs one to connect to.

2. **Add the MQTT integration.**
   - In Home Assistant, go to **Settings → Devices & services**.
   - Click **Add Integration** (bottom right) and select **MQTT**.
   - If you're running the official Mosquitto broker *add-on* inside a Home Assistant OS/Supervised install, Home Assistant can auto-configure this step for you — it generates a broker user/password automatically. If you're running Mosquitto as a **separate Docker container** (the setup this bridge assumes), choose the manual option instead.

3. **Enter your broker's connection details.**
   - **Broker:** the broker's hostname or IP. If Home Assistant and the broker are both Docker containers on the same network, use the broker's *container name* (e.g. `mosquitto`) rather than an IP — container names resolve automatically on a shared Docker bridge network. If Home Assistant lives outside Docker, use the host's IP and the broker's exposed port.
   - **Port:** `1883` for a plain connection (matches this bridge's own `MQTT_PORT` default), or your broker's TLS port if you've enabled TLS.
   - **Username / Password:** the credentials you created for the broker (see the broker setup section — this should be a *separate* MQTT user from the one you give this bridge, so each client's access can be revoked independently).
   - Home Assistant requires the broker to support **MQTT protocol version 5**. The current Mosquitto image (`eclipse-mosquitto:2.x`) supports v5 by default, so this is only a concern with very old broker versions.
   - Submit the form. Home Assistant will test the connection immediately and report an error if it can't reach the broker — double-check the hostname/network and credentials if it fails.

4. **Leave discovery on its default.** Home Assistant listens for MQTT discovery messages on the `homeassistant/` topic prefix by default. This bridge's own `DISCOVERY_PREFIX` setting defaults to `homeassistant` too — leave both at their defaults unless you have a specific reason to change them, and if you do change one, change both to match.

5. **Verify the integration is live.** Go to **Settings → Devices & services** and confirm the **MQTT** tile shows as connected (no error banner). At this point Home Assistant is ready to receive discovery messages — devices won't appear yet until this bridge itself is running and publishing, which the [implementation walkthrough](#fully-implementing-this-application) below covers.

6. **Optional: sanity-check the broker independently of Home Assistant.** From any machine that can reach the broker:
   ```bash
   mosquitto_sub -h <broker-host> -p 1883 -u <user> -P <password> -v -t "homeassistant/#"
   ```
   This subscribes to everything under the discovery prefix. Leave it running, then start this bridge — you should see discovery and state messages appear here as devices come online, which is a faster way to confirm the whole pipeline works than watching the Home Assistant UI alone.

---

## Setting up an MQTT broker

If you already have a broker running, skip this section — just make sure you can create a dedicated username/password for this bridge on it. If not, here's a self-contained Mosquitto broker on Docker, matching the network layout `docker-compose.yml` in this repo expects (a shared `iot_network` bridge network, broker reachable by container name).

### 1. Create the config directory

```bash
mkdir -p ~/mosquitto/config ~/mosquitto/data ~/mosquitto/log
```

### 2. Write `mosquitto.conf`

```bash
cat > ~/mosquitto/config/mosquitto.conf << 'EOF'
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
persistence true
persistence_location /mosquitto/data/
log_dest file /mosquitto/log/mosquitto.log
EOF
```

Mosquitto 2.x refuses all non-loopback connections unless a listener and an authentication method are both explicitly configured — `allow_anonymous false` plus `password_file` is the minimum needed for this bridge and Home Assistant to authenticate as distinct users.

### 3. Create the Docker network (skip if it already exists)

```bash
docker network create iot_network
```

### 4. Start the broker container

```bash
docker run -d \
  --name mosquitto \
  --network iot_network \
  --restart always \
  -p 1883:1883 \
  -v ~/mosquitto/config:/mosquitto/config \
  -v ~/mosquitto/data:/mosquitto/data \
  -v ~/mosquitto/log:/mosquitto/log \
  eclipse-mosquitto:2.0
```

`-p 1883:1883` exposes the broker to the host too (useful for the `mosquitto_sub` sanity check above, or for devices outside Docker); containers on `iot_network` can already reach it by name (`mosquitto`) without this.

### 5. Create broker users

Create one user for this bridge and, separately, one for Home Assistant's own MQTT integration — keeping them distinct means you can revoke one client's access without affecting the other.

```bash
docker exec -it mosquitto mosquitto_passwd -c /mosquitto/config/passwd dirigera_bridge
docker exec -it mosquitto mosquitto_passwd /mosquitto/config/passwd homeassistant
```

The first command's `-c` flag *creates* the password file (only use `-c` once — it overwrites any existing file); the second appends a user to the same file. Each command will prompt for a password interactively.

### 6. Restart to pick up the users

```bash
docker restart mosquitto
```

### 7. Verify

```bash
docker exec -it mosquitto mosquitto_sub -h localhost -p 1883 -u dirigera_bridge -P <password> -t 'test' -C 1 &
docker exec -it mosquitto mosquitto_pub -h localhost -p 1883 -u dirigera_bridge -P <password> -t 'test' -m 'hello'
```

If the subscriber prints `hello`, the broker is accepting authenticated connections correctly. Use the `dirigera_bridge` user's credentials as `MQTT_USER`/`MQTT_PASSWORD` in this bridge's `.env`, and the `homeassistant` user's credentials when configuring [Home Assistant's MQTT integration](#enabling-mqtt-in-home-assistant).

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

## Fully implementing this application

This walks through everything from a bare checkout to a running bridge publishing devices into Home Assistant. It assumes you've already completed [Enabling MQTT in Home Assistant](#enabling-mqtt-in-home-assistant) and [Setting up an MQTT broker](#setting-up-an-mqtt-broker) (or already have both), and that you have your [Dirigera token](#obtaining-the-dirigera-token) in hand.

### 1. Clone the repository

```bash
git clone https://github.com/dickkouwenhoven/Dirigera-Bridge.git
cd Dirigera-Bridge
```

### 2. Create the shared Docker network (skip if it already exists)

```bash
docker network create iot_network
```

If you followed the broker setup above, or already have Home Assistant and Mosquitto running on a shared network, this step is likely already done — `docker network create` fails harmlessly if the network already exists, so it's safe to run either way.

### 3. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and fill in, at minimum, the required values:

```env
DIRIGERA_IP=192.168.1.XXX          # your hub's IP address
DIRIGERA_TOKEN=your_token_here     # from "Obtaining the Dirigera token" above
MQTT_HOST=mosquitto                # the broker's container name, if on the same Docker network
MQTT_USER=dirigera_bridge          # the user you created in the broker setup
MQTT_PASSWORD=your_broker_password
```

Everything else in `.env.example` has a working default — see [Configuration reference](#configuration-reference) for the full list, including the MQTT transport-level settings (`MQTT_TLS`, `MQTT_RECONNECT`, `MQTT_RECONNECT_DELAY_MIN`/`MAX`) if your broker setup needs anything beyond the defaults.

### 4. Build and start the bridge

```bash
docker compose up -d --build
```

This builds the two-stage image (installing dependencies in a builder layer, then copying only the installed packages and the `dirigera_bridge/` source into a slim runtime layer) and starts the container in the background, attached to `iot_network`.

### 5. Verify it's running

```bash
docker compose logs -f dirigera-bridge
```

A successful startup looks like:

```
INFO  dirigera_bridge.main          ============================================================
INFO  dirigera_bridge.main          dirigera-mqtt-bridge v1.0.0 starting
INFO  dirigera_bridge.main          Dirigera hub : 192.168.1.XXX
INFO  dirigera_bridge.main          MQTT broker  : mosquitto:1883 (client_id=dirigera-bridge)
INFO  dirigera_bridge.orchestrator  Orchestrator: startup complete — bridge is RUNNING.
INFO  dirigera_bridge.orchestrator  Supported device types: ['airPurifier', 'blind', ...]
```

If it instead exits immediately with a `CRITICAL Configuration error` line, the message names exactly which `.env` variable is missing or invalid — go back to step 3.

### 6. Confirm devices appear in Home Assistant

Go to **Settings → Devices & services → MQTT** in Home Assistant. Within a few seconds of the bridge reaching `RUNNING`, your Dirigera devices should appear as new devices under the MQTT integration, grouped by physical device (a VALLHORN sensor showing as one device with both a motion and illuminance entity, for example — see [Supported devices](#supported-devices)).

If nothing appears after a minute:
- Re-run the [broker sanity check](#setting-up-an-mqtt-broker) (`mosquitto_sub -t "homeassistant/#"`) — if you see messages there but not in Home Assistant, the problem is on the Home Assistant MQTT integration side (check its config against `homeassistant`'s broker credentials, not `dirigera_bridge`'s).
- If you see nothing at all on the broker, check `docker compose logs -f dirigera-bridge` for connection errors to either the Dirigera hub or the broker — both are logged with a clear error code (see `core/errors.py`'s `ErrorCode` enum for what each one means).

### 7. Ongoing operation

- **Updating:** `git pull`, then `docker compose up -d --build` again — Docker Compose rebuilds only what changed.
- **Stopping cleanly:** `docker compose down` — the bridge handles `SIGTERM` gracefully, marking all entities offline in Home Assistant before exiting (see [Resilience](#resilience)).
- **Changing configuration:** edit `.env`, then `docker compose restart dirigera-bridge` (no rebuild needed — `.env` is mounted at runtime, not baked into the image).

---

## Adding a new device type

The mapping layer is plugin-based. To add support for a new Dirigera device type:

1. Create a new file in `dirigera_bridge/mapping/domains/`, e.g. `my_device.py`
2. Implement a mapper function:
   ```python
   def map_my_device(context: DeviceContext, device_info: DeviceInfo) -> List[Entity]: ...
   ```
3. Add a `DEVICE_TYPES` dict at the bottom:
   ```python
   DEVICE_TYPES = {
       "myDeviceType": map_my_device,
   }
   ```
4. Add the module to `_DOMAIN_MODULES` in `dirigera_bridge/mapping/domains/__init__.py`
5. Add state translation to `dirigera_bridge/mapping/state_mapper.py`
6. Add command translation to `dirigera_bridge/mapping/command_mapper.py` (if controllable)

Nothing else needs to change.

---

## Configuration reference

All settings are loaded from `.env` at startup. Invalid or missing required values cause an immediate error with a clear message.

| Variable                   | Required | Default           | Description                                                  |
|------------------------------|----------|-------------------|----------------------------------------------------------------|
| `DIRIGERA_IP`               | ✓       | —                 | IP address of the Dirigera hub                                |
| `DIRIGERA_TOKEN`            | ✓       | —                 | Hub access token (secret)                                     |
| `MQTT_HOST`                 | ✓       | —                 | MQTT broker hostname                                           |
| `MQTT_PORT`                 |          | `1883`            | MQTT broker port                                               |
| `MQTT_USER`                 | ✓       | —                 | MQTT username                                                  |
| `MQTT_PASSWORD`             | ✓       | —                 | MQTT password (secret)                                         |
| `MQTT_CLIENT_ID`            |          | `dirigera-bridge` | MQTT client identifier                                          |
| `MQTT_KEEPALIVE`            |          | `60`              | MQTT keepalive interval (seconds)                               |
| `MQTT_BASE_TOPIC`           |          | `dirigera`        | Base topic prefix for state/command topics                      |
| `MQTT_QOS`                  |          | `1`               | MQTT QoS level (0, 1, or 2)                                     |
| `MQTT_TLS`                  |          | `false`           | Use TLS for the MQTT connection                                 |
| `MQTT_RECONNECT`            |          | `true`            | Let the HASDK auto-reconnect the MQTT transport                 |
| `MQTT_RECONNECT_DELAY_MIN`  |          | `1.0`             | Minimum HASDK MQTT reconnect delay (seconds)                    |
| `MQTT_RECONNECT_DELAY_MAX`  |          | `60.0`            | Maximum HASDK MQTT reconnect delay (seconds)                    |
| `DISCOVERY_PREFIX`          |          | `homeassistant`   | HA MQTT discovery prefix                                        |
| `LOG_LEVEL`                 |          | `INFO`            | Logging level                                                   |
| `METRICS_INTERVAL`          |          | `300`             | Seconds between metrics log snapshots                           |
| `WS_PING_INTERVAL`          |          | `30`              | WebSocket keepalive ping interval (seconds)                     |
| `WS_PING_TIMEOUT`           |          | `10`              | WebSocket pong timeout (seconds)                                |
| `RECONNECT_DELAY_INITIAL`   |          | `1.0`             | Initial Dirigera WebSocket reconnect backoff delay (seconds)    |
| `RECONNECT_DELAY_MAX`       |          | `60.0`            | Maximum Dirigera WebSocket reconnect backoff delay (seconds)    |

Note the `MQTT_*` reconnect/TLS settings are distinct from `RECONNECT_DELAY_INITIAL`/`RECONNECT_DELAY_MAX` — the former govern the HASDK's MQTT broker connection, the latter govern this bridge's own Dirigera WebSocket connection. They're deliberately named to avoid being confused with each other.

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
INFO  dirigera_bridge.orchestrator  Metrics snapshot: ws_messages_received=1243  mqtt_messages_published=892  mapping_state_updates=756  ...
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

| Package                | Version         | Purpose                       |
|------------------------|-----------------|--------------------------------|
| `websockets`           | ≥16.0, <17.0    | Dirigera WebSocket connection |
| `aiohttp`              | ≥3.9, <4.0      | Dirigera REST API             |
| `aiomqtt`              | ≥2.5.1, <3.0    | MQTT transport (via HASDK)    |
| `pydantic`             | ≥2.13.4, <3.0   | Dirigera payload validation   |
| `python-dotenv`        | ≥1.2.3, <2.0    | `.env` file loading           |
| `ha_mqtt_sdk`          | ≥0.8.4, <2.0    | HA entity lifecycle via MQTT — published to PyPI, installed like any other dependency |

---

## License

This project is licensed under the MIT License, which means you are free to use, copy, modify, merge, publish, distribute, sublicense, and sell this software.

See the LICENSE file for the full license text.

Attribution

You're welcome to use and adapt this code for your own projects. If you do, I would appreciate it if you kept the original author attribution and mentioned dickkouwenhoven as the original author.

Attribution is appreciated, but is not a requirement of the MIT License.

---

## Author

Dick Kouwenhoven

GitHub: https://github.com/dickkouwenhoven/Dirigera-Bridge
