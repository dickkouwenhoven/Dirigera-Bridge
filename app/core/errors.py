"""
errors.py

Centralised error definitions for the Dirigera MQTT Bridge.

Role & Responsibility:
    Single source of truth for all custom exceptions and error codes
    used across the entire application. Every `raise` statement in the
    codebase raises DirigeraBridgeError with an ErrorCode — no bare
    built-in exceptions are raised directly by application code.

What it does:
    - Defines ErrorCode enum covering every failure category in the
    application (config, WebSocket, REST, mapping, MQTT, lifecycle)
    - Defines DirigeraBridgeError as the single application exception
    type, carrying an ErrorCode and a human-readable message
    - Provides a helper format_error() for consistent log formatting

Arguments / Configuration:
    No runtime configuration. Import and use directly.

Used by:
    - Every module in the application that raises or catches errors
    - app/core/lifecycle.py   (lifecycle state errors)
    - app/dirigera/websocket_client.py  (connection errors)
    - app/dirigera/rest_client.py       (REST call errors)
    - app/mapping/device_registry.py    (mapping errors)
    - app/mapping/device_mapper.py      (mapping errors)
    - app/ha/ha_client.py               (MQTT errors)
    - app/orchestrator.py               (orchestration errors)

Not responsible for:
    - Logging errors (each module logs its own errors)
    - SDK exceptions (those are defined in sdk/exceptions.py and are
    caught at the boundary in ha_client.py, then re-raised as
    DirigeraBridgeError where appropriate)
"""

from __future__ import annotations

import logging
from enum import Enum, unique
from typing import Optional

__all__ = [
    "ErrorCode",
    "DirigeraBridgeError",
    "format_error",
]

logger = logging.getLogger(__name__)


# ── Error codes ───────────────────────────────────────────────────────────────


@unique
class ErrorCode(str, Enum):
    """
    Enumeration of all error codes used in the application.

    Categories:
        CONFIG_*    Configuration and environment variable errors
        WS_*        Dirigera WebSocket connection errors
        REST_*        Dirigera REST API errors
        MAPPING_*    Device/state/command mapping errors
        MQTT_*        MQTT / HASDK interaction errors
        LIFECYCLE_*    Service lifecycle and state machine errors
        INTERNAL_*    Unexpected internal errors (bugs, assertions)
    """

    # ── Configuration ─────────────────────────────────────────────────────
    CONFIG_MISSING_FIELD = "CONFIG_MISSING_FIELD"
    CONFIG_INVALID_VALUE = "CONFIG_INVALID_VALUE"

    # ── WebSocket ─────────────────────────────────────────────────────────
    WS_CONNECTION_FAILED = "WS_CONNECTION_FAILED"
    WS_CONNECTION_LOST = "WS_CONNECTION_LOST"
    WS_MESSAGE_PARSE_ERROR = "WS_MESSAGE_PARSE_ERROR"
    WS_AUTHENTICATION_ERROR = "WS_AUTHENTICATION_ERROR"
    WS_UNEXPECTED_CLOSE = "WS_UNEXPECTED_CLOSE"

    # ── REST API ──────────────────────────────────────────────────────────
    REST_REQUEST_FAILED = "REST_REQUEST_FAILED"
    REST_AUTHENTICATION_ERROR = "REST_AUTHENTICATION_ERROR"
    REST_DEVICE_NOT_FOUND = "REST_DEVICE_NOT_FOUND"
    REST_INVALID_RESPONSE = "REST_INVALID_RESPONSE"
    REST_TIMEOUT = "REST_TIMEOUT"

    # ── Mapping ───────────────────────────────────────────────────────────
    MAPPING_UNKNOWN_DEVICE_TYPE = "MAPPING_UNKNOWN_DEVICE_TYPE"
    MAPPING_INVALID_PAYLOAD = "MAPPING_INVALID_PAYLOAD"
    MAPPING_MISSING_ATTRIBUTE = "MAPPING_MISSING_ATTRIBUTE"
    MAPPING_INVALID_COMMAND = "MAPPING_INVALID_COMMAND"
    MAPPING_DEVICE_BUILD_ERROR = "MAPPING_DEVICE_BUILD_ERROR"

    # ── MQTT / HASDK ──────────────────────────────────────────────────────
    MQTT_PUBLISH_FAILED = "MQTT_PUBLISH_FAILED"
    MQTT_SUBSCRIBE_FAILED = "MQTT_SUBSCRIBE_FAILED"
    MQTT_CONNECTION_FAILED = "MQTT_CONNECTION_FAILED"
    MQTT_CONNECTION_LOST = "MQTT_CONNECTION_LOST"
    MQTT_REGISTRATION_FAILED = "MQTT_REGISTRATION_FAILED"

    # ── Lifecycle ─────────────────────────────────────────────────────────
    LIFECYCLE_INVALID_TRANSITION = "LIFECYCLE_INVALID_TRANSITION"
    LIFECYCLE_STARTUP_FAILED = "LIFECYCLE_STARTUP_FAILED"
    LIFECYCLE_SHUTDOWN_FAILED = "LIFECYCLE_SHUTDOWN_FAILED"

    # ── Internal ──────────────────────────────────────────────────────────
    INTERNAL_INVALID_ARGUMENT = "INTERNAL_INVALID_ARGUMENT"
    INTERNAL_UNEXPECTED_STATE = "INTERNAL_UNEXPECTED_STATE"
    INTERNAL_NOT_IMPLEMENTED = "INTERNAL_NOT_IMPLEMENTED"


# ── Exception class ───────────────────────────────────────────────────────────


class DirigeraBridgeError(Exception):
    """
    Single application-level exception for the Dirigera MQTT Bridge.

    Every error raised by application code (outside third-party
    libraries) is an instance of this class. Callers can inspect
    `error.code` to handle specific failure categories.

    Args:
        code (ErrorCode):    Categorised error code from ErrorCode enum.
        message (str):        Human-readable description of the failure.
        cause (Exception):    Optional original exception that triggered
                    this error. Stored as __cause__ for
                    traceback chaining.

    Example:
        raise DirigeraBridgeError(
            ErrorCode.WS_CONNECTION_FAILED,
            f"Cannot connect to Dirigera at {host}",
            cause=original_exc,
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        cause: Optional[Exception] = None,
    ) -> None:

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(code, ErrorCode):
            raise TypeError(
                f"DirigeraBridgeError.code must be an ErrorCode, got {type(code).__name__}"
            )
        if not isinstance(message, str) or not message.strip():
            raise TypeError("DirigeraBridgeError.message must be a non-empty string")

        super().__init__(message)

        self.code: ErrorCode = code
        self.message: str = message

        # Chain the original exception so tracebacks are preserved
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        cause_repr = f", cause={self.__cause__!r}" if self.__cause__ is not None else ""
        return (
            f"DirigeraBridgeError("
            f"code={self.code!r}, "
            f"message={self.message!r}"
            f"{cause_repr})"
        )


# ── Helper ────────────────────────────────────────────────────────────────────


def format_error(error: DirigeraBridgeError) -> str:
    """
    Format a DirigeraBridgeError into a consistent single-line string
    suitable for structured log output.

    Args:
        error (DirigeraBridgeError): The error to format.

    Returns:
        str: Formatted string in the form:
            "[ERROR_CODE] message (caused by: OriginalError: detail)"

    Raises:
        TypeError: If error is not a DirigeraBridgeError instance.

    Example:
        logger.error("%s", format_error(err))
    """

    if not isinstance(error, DirigeraBridgeError):
        raise TypeError(
            f"format_error expects DirigeraBridgeError, got {type(error).__name__}"
        )

    base = str(error)

    if error.__cause__ is not None:
        cause_str = f"{type(error.__cause__).__name__}: {error.__cause__}"
        return f"{base} (caused by: {cause_str})"

    return base
