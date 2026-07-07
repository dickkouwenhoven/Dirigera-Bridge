"""
config.py

Application configuration loader and typed settings.

Role & Responsibility:
    Single source of truth for all runtime configuration. Reads
    environment variables (populated from the .env file via
    python-dotenv), validates every value at startup, and exposes
    a single typed Settings object that all other modules import.

    If any required variable is missing or any value is invalid the
    application raises immediately at startup with a clear error
    message — configuration problems are never discovered at runtime
    mid-operation.

What it does:
    - Loads the .env file from the project root using python-dotenv
    - Reads and validates all required and optional environment
    variables
    - Exposes a Settings dataclass with typed fields for every config
    value used in the application
    - Provides a load_settings() factory function that performs the
    full load-and-validate cycle
    - Provides a get_settings() module-level accessor that returns
    the cached singleton — callers never need to pass Settings
    around explicitly

Arguments / Configuration:
    All configuration comes from environment variables. Required:

    DIRIGERA_IP        IP address of the Dirigera hub
    DIRIGERA_TOKEN        Access token for the Dirigera WebSocket/REST API
    MQTT_HOST        Hostname of the MQTT broker (e.g. 'mosquitto')
    MQTT_USER        MQTT username
    MQTT_PASSWORD        MQTT password

    Optional (with defaults):

    MQTT_PORT        MQTT broker port (default: 1883)
    MQTT_CLIENT_ID        MQTT client identifier (default: 'dirigera-bridge')
    MQTT_KEEPALIVE        MQTT keepalive interval in seconds (default: 60)
    MQTT_BASE_TOPIC        Base topic prefix for state/command topics
                (default: 'dirigera')
    MQTT_QOS        MQTT QoS level 0, 1, or 2 (default: 1)
    DISCOVERY_PREFIX    HA MQTT discovery prefix (default: 'homeassistant')
    LOG_LEVEL        Logging level string (default: 'INFO')
    METRICS_INTERVAL    Seconds between metrics log snapshots (default: 300)
    WS_PING_INTERVAL    Seconds between WebSocket keepalive pings (default: 30)
    WS_PING_TIMEOUT        Seconds to wait for a pong reply (default: 10)
    RECONNECT_DELAY_INITIAL    First reconnect delay in seconds (default: 1.0)
    RECONNECT_DELAY_MAX    Maximum reconnect delay in seconds (default: 60.0)

Used by:
    - main.py                           (calls load_settings() once at startup)
    - app/orchestrator.py               (reads settings fields)
    - app/dirigera/websocket_client.py  (DIRIGERA_IP, TOKEN, WS_PING_*)
    - app/dirigera/rest_client.py       (DIRIGERA_IP, TOKEN)
    - app/ha/ha_client.py               (all MQTT_* and DISCOVERY_PREFIX)

Not responsible for:
    - Establishing any connections (that is the client modules' job)
    - Watching for config changes at runtime (static at startup)
    - Secrets management beyond reading from environment variables
    - SERVICE_VERSION — that is a hardcoded constant in main.py, not
    an environment variable, so it cannot be accidentally overridden
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from .core.errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "Settings",
    "load_settings",
    "get_settings",
]

logger = logging.getLogger(__name__)

# Module-level singleton — populated by load_settings()
_settings: Optional["Settings"] = None

# Valid MQTT QoS levels
_VALID_QOS_LEVELS = {0, 1, 2}

# Valid Python logging level strings
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


# ── Settings dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    """
    Typed, immutable application settings.

    Constructed by load_settings() after reading and validating all
    environment variables. Frozen so no module can accidentally mutate
    configuration at runtime.

    Fields — Dirigera:
        dirigera_ip (str):
            IP address of the Dirigera hub.

        dirigera_token (str):
            Access token for Dirigera WebSocket and REST API.
            Never logged — treated as a secret.

    Fields — MQTT connection:
        mqtt_host (str):
            Hostname or IP of the MQTT broker.

        mqtt_port (int):
            MQTT broker port. Range: 1-65535. Default: 1883.

        mqtt_user (str):
            MQTT broker username.

        mqtt_password (str):
            MQTT broker password. Never logged — treated as a secret.

        mqtt_client_id (str):
            MQTT client identifier sent to the broker on connect.
            Makes broker-side log filtering easier and ensures the
            broker recognizes reconnecting clients as the same session.
            Default: 'dirigera-bridge'.

        mqtt_keepalive (int):
            Seconds between MQTT PINGREQ keepalive messages.
            Must be > 0. Default: 60.

        mqtt_base_topic (str):
            Base topic prefix for all state and command topics owned
            by this bridge (e.g. 'dirigera/light/abc123/state').
            Distinct from discovery_prefix which is controlled by HA.
            Default: 'dirigera'.

        mqtt_qos (int):
            MQTT Quality of Service level for all publishes and
            subscriptions. 0 = at most once, 1 = at least once,
            2 = exactly once. Default: 1.

    Fields — Home Assistant:
        discovery_prefix (str):
            Home Assistant MQTT discovery topic prefix. Must match the
            discovery prefix configured in HA. Default: 'homeassistant'.

    Fields — operational:
        log_level (str):
            Python logging level string. Default: 'INFO'.

        metrics_interval (int):
            Seconds between periodic metrics log snapshots.
            Minimum: 10. Default: 300 (5 minutes).

        ws_ping_interval (int):
            Seconds between WebSocket keepalive pings to the hub.
            Minimum: 5. Default: 30.

        ws_ping_timeout (int):
            Seconds to wait for a pong before declaring the WebSocket
            connection dead. Must be < ws_ping_interval. Default: 10.

        reconnect_delay_initial (float):
            Initial delay in seconds for exponential backoff on
            reconnect. Minimum: 0.1. Default: 1.0.

        reconnect_delay_max (float):
            Maximum delay cap in seconds for exponential backoff.
            Must be >= reconnect_delay_initial. Default: 60.0.
    """

    # ── Dirigera ──────────────────────────────────────────────────────────
    dirigera_ip: str
    dirigera_token: str  # secret — never log raw value

    # ── MQTT connection ───────────────────────────────────────────────────
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str  # secret — never log raw value
    mqtt_client_id: str
    mqtt_keepalive: int
    mqtt_base_topic: str
    mqtt_qos: int

    # ── Home Assistant ────────────────────────────────────────────────────
    discovery_prefix: str

    # ── Operational ───────────────────────────────────────────────────────
    log_level: str
    metrics_interval: int
    ws_ping_interval: int
    ws_ping_timeout: int
    reconnect_delay_initial: float
    reconnect_delay_max: float

    def safe_repr(self) -> str:
        """
        Return a string representation with secrets redacted.

        Used for startup logging — never log the raw Settings object
        as it contains the Dirigera token and MQTT password.

        Returns:
            str: Settings summary with secrets replaced by '***'.
        """

        return (
            f"Settings("
            f"dirigera_ip={self.dirigera_ip!r}, "
            f"dirigera_token='***', "
            f"mqtt_host={self.mqtt_host!r}, "
            f"mqtt_port={self.mqtt_port}, "
            f"mqtt_user={self.mqtt_user!r}, "
            f"mqtt_password='***', "
            f"mqtt_client_id={self.mqtt_client_id!r}, "
            f"mqtt_keepalive={self.mqtt_keepalive}, "
            f"mqtt_base_topic={self.mqtt_base_topic!r}, "
            f"mqtt_qos={self.mqtt_qos}, "
            f"discovery_prefix={self.discovery_prefix!r}, "
            f"log_level={self.log_level!r}, "
            f"metrics_interval={self.metrics_interval}, "
            f"ws_ping_interval={self.ws_ping_interval}, "
            f"ws_ping_timeout={self.ws_ping_timeout}, "
            f"reconnect_delay_initial={self.reconnect_delay_initial}, "
            f"reconnect_delay_max={self.reconnect_delay_max}"
            f")"
        )


# ── Public API ────────────────────────────────────────────────────────────────


def load_settings(env_file: Optional[str] = None) -> Settings:
    """
    Load, validate, and cache the application settings.

    Reads environment variables after loading the .env file.
    Validates every value — including cross-field constraints — and
    raises immediately on any problem so configuration errors surface
    at startup rather than mid-operation.

    Stores the result as a module-level singleton accessible via
    get_settings(). Calling load_settings() a second time reloads
    and replaces the singleton — useful in tests that inject different
    configuration.

    Args:
        env_file (str | None):    Path to the .env file. If None, looks
                    for '.env' in the current working
                    directory (standard dotenv behavior).

    Returns:
        Settings:        Fully validated, immutable settings object.

    Raises:
        DirigeraBridgeError:    CONFIG_MISSING_FIELD if a required
                    variable is absent or empty.
        DirigeraBridgeError:    CONFIG_INVALID_VALUE if any value fails
                    type, range, or cross-field validation.
    """

    global _settings

    # ── Load .env file ────────────────────────────────────────────────────
    # override=False (the default) is intentional: environment
    # variables already set — by the shell, Docker's `environment:`
    # block, or a test's monkeypatch — must take priority over
    # whatever is in the .env file on disk. The .env file only fills
    # in values that aren't already set. Using override=True here
    # previously caused a real .env file in the project root to
    # silently clobber test-injected environment variables.
    if env_file:
        loaded = load_dotenv(env_file, override=False)
    else:
        loaded = load_dotenv(override=False)

    logger.debug(
        "dotenv loaded: %s (env_file=%r)",
        "yes" if loaded else "no file found — using os environment",
        env_file,
    )

    # ── Required fields ───────────────────────────────────────────────────
    dirigera_ip = _require_str("DIRIGERA_IP")
    dirigera_token = _require_str("DIRIGERA_TOKEN")
    mqtt_host = _require_str("MQTT_HOST")
    mqtt_user = _require_str("MQTT_USER")
    mqtt_password = _require_str("MQTT_PASSWORD")

    # ── MQTT optional fields ──────────────────────────────────────────────
    mqtt_port = _optional_int("MQTT_PORT", default=1883, min_val=1, max_val=65535)
    mqtt_client_id = _optional_str("MQTT_CLIENT_ID", default="dirigera-bridge")
    mqtt_keepalive = _optional_int("MQTT_KEEPALIVE", default=60, min_val=1)
    mqtt_base_topic = _optional_str("MQTT_BASE_TOPIC", default="dirigera")
    mqtt_qos = _optional_int("MQTT_QOS", default=1, min_val=0, max_val=2)

    # ── HA optional fields ────────────────────────────────────────────────
    discovery_prefix = _optional_str("DISCOVERY_PREFIX", default="homeassistant")

    # ── Operational optional fields ───────────────────────────────────────
    log_level = _optional_log_level("LOG_LEVEL", default="INFO")
    metrics_interval = _optional_int("METRICS_INTERVAL", default=300, min_val=10)
    ws_ping_interval = _optional_int("WS_PING_INTERVAL", default=30, min_val=5)
    ws_ping_timeout = _optional_int("WS_PING_TIMEOUT", default=10, min_val=1)
    reconnect_delay_initial = _optional_float(
        "RECONNECT_DELAY_INITIAL", default=1.0, min_val=0.1
    )
    reconnect_delay_max = _optional_float(
        "RECONNECT_DELAY_MAX", default=60.0, min_val=1.0
    )

    # ── Cross-field validation ────────────────────────────────────────────
    if reconnect_delay_max < reconnect_delay_initial:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"RECONNECT_DELAY_MAX ({reconnect_delay_max}) must be >= "
            f"RECONNECT_DELAY_INITIAL ({reconnect_delay_initial})",
        )

    if ws_ping_timeout >= ws_ping_interval:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"WS_PING_TIMEOUT ({ws_ping_timeout}) must be < "
            f"WS_PING_INTERVAL ({ws_ping_interval})",
        )

    if mqtt_keepalive <= ws_ping_timeout:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"MQTT_KEEPALIVE ({mqtt_keepalive}) should be > "
            f"WS_PING_TIMEOUT ({ws_ping_timeout}) to avoid "
            f"premature broker disconnects during WebSocket probes",
        )

    # ── Construct and cache ───────────────────────────────────────────────
    _settings = Settings(
        dirigera_ip=dirigera_ip,
        dirigera_token=dirigera_token,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_user=mqtt_user,
        mqtt_password=mqtt_password,
        mqtt_client_id=mqtt_client_id,
        mqtt_keepalive=mqtt_keepalive,
        mqtt_base_topic=mqtt_base_topic,
        mqtt_qos=mqtt_qos,
        discovery_prefix=discovery_prefix,
        log_level=log_level,
        metrics_interval=metrics_interval,
        ws_ping_interval=ws_ping_interval,
        ws_ping_timeout=ws_ping_timeout,
        reconnect_delay_initial=reconnect_delay_initial,
        reconnect_delay_max=reconnect_delay_max,
    )

    assert _settings is not None
    logger.info("Settings loaded: %s", _settings.safe_repr())

    return _settings


def get_settings() -> Settings:
    """
    Return the cached Settings singleton.

    Must be called after load_settings() has been called at least once.
    Raises immediately if called before load_settings() so that
    missing startup initialization is caught early and clearly.

    Returns:
        Settings:        The cached settings object.

    Raises:
        DirigeraBridgeError:    LIFECYCLE_STARTUP_FAILED if load_settings()
                    has not been called yet.
    """

    if _settings is None:
        raise DirigeraBridgeError(
            ErrorCode.LIFECYCLE_STARTUP_FAILED,
            "get_settings() called before load_settings() — "
            "call load_settings() during application startup",
        )

    return _settings


# ── Internal helpers ──────────────────────────────────────────────────────────


def _require_str(key: str) -> str:
    """
    Read a required environment variable as a non-empty string.

    Raises:
        DirigeraBridgeError: CONFIG_MISSING_FIELD if absent or empty.
    """

    value = os.environ.get(key, "").strip()

    if not value:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_MISSING_FIELD,
            f"Required environment variable '{key}' is missing or empty",
        )

    return value


def _optional_str(key: str, default: str) -> str:
    """
    Read an optional environment variable as a string.

    Returns default if absent or empty.
    """

    value = os.environ.get(key, "").strip()
    return value if value else default


def _optional_int(
    key: str,
    default: int,
    min_val: int,
    max_val: Optional[int] = None,
) -> int:
    """
    Read an optional environment variable as an integer.

    Uses default if absent or empty. Validates min_val and optional
    max_val.

    Raises:
        DirigeraBridgeError:    CONFIG_INVALID_VALUE if non-parseable
                    or out of range.
    """

    raw = os.environ.get(key, "").strip()

    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be an integer, got {raw!r}",
        )

    if value < min_val:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be >= {min_val}, got {value}",
        )

    if max_val is not None and value > max_val:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be <= {max_val}, got {value}",
        )

    return value


def _optional_float(
    key: str,
    default: float,
    min_val: float,
) -> float:
    """
    Read an optional environment variable as a float.

    Uses default if absent or empty. Validates min_val only.

    Raises:
        DirigeraBridgeError:    CONFIG_INVALID_VALUE if non-parseable
                    or below min_val.
    """

    raw = os.environ.get(key, "").strip()

    if not raw:
        return default

    try:
        value = float(raw)
    except ValueError:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be a number, got {raw!r}",
        )

    if value < min_val:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be >= {min_val}, got {value}",
        )

    return value


def _optional_log_level(key: str, default: str) -> str:
    """
    Read an optional environment variable as a logging level string.

    Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL (case-insensitive).
    Returns the value uppercased. Uses default if absent or empty.

    Raises:
        DirigeraBridgeError: CONFIG_INVALID_VALUE if not a valid level.
    """

    raw = os.environ.get(key, "").strip()

    if not raw:
        return default

    upper = raw.upper()

    if upper not in _VALID_LOG_LEVELS:
        raise DirigeraBridgeError(
            ErrorCode.CONFIG_INVALID_VALUE,
            f"Environment variable '{key}' must be one of "
            f"{sorted(_VALID_LOG_LEVELS)}, got {raw!r}",
        )

    return upper
