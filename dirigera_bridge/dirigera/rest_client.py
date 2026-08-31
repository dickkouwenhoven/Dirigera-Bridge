"""
rest_client.py

Async REST client for the Dirigera hub API.

Role & Responsibility:
    Owns all outbound HTTP communication from the bridge to the
    Dirigera hub. Translates command payloads (produced by the mapping
    layer) into authenticated PATCH requests to the Dirigera REST API,
    and fetches the initial device list at startup.

    This is the only module that knows the Dirigera REST API URL
    structure, authentication scheme, and HTTP error codes.

What it does:
    - Fetches the complete device list from the hub at startup
      (GET /devices) and returns a list of DirigeraDevice instances
    - Sends attribute update commands to specific devices
      (PATCH /devices/{id}/attributes) using the payload produced
      by the command mapper
    - Authenticates all requests using the Bearer token from settings
    - Validates HTTP response status codes and raises typed errors
    - Tracks REST metrics (requests sent, success, failed, commands)
    - Logs all outbound requests and responses at DEBUG level

Arguments / Configuration:
    settings (Settings): Injected application settings. Reads:
        - settings.dirigera_ip       for the hub base URL
        - settings.dirigera_token    for Bearer authentication
    metrics (MetricsStore): Injected metrics store for counter
        increments.

Used by:
    - app/orchestrator.py           (calls get_devices() at startup)
    - app/mapping/command_mapper.py (indirectly — orchestrator calls
                                     send_command() with mapped payload)

Not responsible for:
    - WebSocket event handling (that is websocket_client.py)
    - Mapping commands to Dirigera payloads (that is command_mapper.py)
    - Retry logic — the orchestrator wraps get_devices() in a retry
      loop; send_command() raises immediately on failure and the
      orchestrator decides whether to retry
    - SSL certificate verification — Dirigera uses a self-signed cert;
      ssl=False is intentional and documented

Design notes:
    - Uses aiohttp.ClientSession with a shared session across requests
      for connection pooling. Session is created on first use and
      closed explicitly via close().
    - The Dirigera API base URL is https://{ip}:8443/v1
    - All PATCH payloads are JSON arrays of attribute objects:
      [{"attributes": {"isOn": true}}]
    - Dirigera returns 202 Accepted on successful PATCH, not 200 OK
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..config import Settings
from ..core.errors import DirigeraBridgeError, ErrorCode
from ..core.metrics import MetricName, MetricsStore
from .models import DirigeraDevice

__all__ = [
    "DirigeraRestClient",
]

logger = logging.getLogger(__name__)

# Dirigera REST API constants
_API_PORT = 8443
_API_VERSION = "v1"
_PATCH_SUCCESS_CODES = {200, 202}  # Dirigera returns 202 on attribute update


class DirigeraRestClient:
    """
    Async REST client for the Dirigera hub.

    Manages a shared aiohttp.ClientSession for connection pooling.
    Must be closed via close() when the service shuts down.

    Args:
        settings (Settings):    Application settings. Must not be None.
        metrics (MetricsStore): Metrics store for counters. Must not
                                be None.

    Raises:
        DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if settings or
                             metrics are not the correct types.
    """

    def __init__(
        self,
        settings: Settings,
        metrics: MetricsStore,
    ) -> None:

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(settings, Settings):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraRestClient: settings must be Settings, got {type(settings).__name__}",
            )

        if not isinstance(metrics, MetricsStore):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"DirigeraRestClient: metrics must be MetricsStore, got {type(metrics).__name__}",
            )

        self._settings = settings
        self._metrics = metrics
        self._session: aiohttp.ClientSession | None = None

        self._base_url = f"https://{settings.dirigera_ip}:{_API_PORT}/{_API_VERSION}"

        logger.debug(
            "DirigeraRestClient initialised (base_url=%s)",
            self._base_url,
        )

    # ── Public API ────────────────────────────────────────────────────────

    async def get_device(self, logical_id: str) -> DirigeraDevice:
        """
        Fetch the current state of a single device from the Dirigera hub.

        Calls GET /devices/{id}. Used by the orchestrator to refresh a
        device's real current state after DEVICE_REACHABLE (e.g. after
        a power-cycle where the physical device's real state may differ
        from whatever Dirigera had cached before the outage) — see
        Orchestrator._on_device_reachable()'s docstring. Deliberately a
        single-device fetch rather than reusing get_devices(), so a
        reconnect only costs one lightweight request for the device that
        actually changed, not a full-fleet re-fetch.

        Args:
            logical_id (str): Dirigera logical device id.

        Returns:
            DirigeraDevice: The freshly-fetched device.

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                                 is not a non-empty string.
            DirigeraBridgeError: REST_REQUEST_FAILED if the HTTP request
                                 fails at the network level.
            DirigeraBridgeError: REST_AUTHENTICATION_ERROR on HTTP 401/403.
            DirigeraBridgeError: REST_DEVICE_NOT_FOUND on HTTP 404.
            DirigeraBridgeError: REST_INVALID_RESPONSE if the response
                                 body cannot be parsed as a device.
            DirigeraBridgeError: REST_TIMEOUT if the request times out.
        """
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "get_device: logical_id must be a non-empty string",
            )

        url = f"{self._base_url}/devices/{logical_id}"
        logger.debug("Fetching single device from Dirigera hub: %s", logical_id)

        self._metrics.increment(MetricName.REST_REQUESTS_SENT)

        raw_device = await self._get_json(url)

        if not isinstance(raw_device, dict):
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            raise DirigeraBridgeError(
                ErrorCode.REST_INVALID_RESPONSE,
                f"GET /devices/{logical_id}: expected a JSON object, "
                f"got {type(raw_device).__name__}",
            )

        try:
            device = DirigeraDevice.model_validate(raw_device)
        except Exception as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            raise DirigeraBridgeError(
                ErrorCode.REST_INVALID_RESPONSE,
                f"GET /devices/{logical_id}: failed to parse device: {exc}",
                cause=exc,
            ) from exc

        self._metrics.increment(MetricName.REST_REQUESTS_SUCCESS)
        logger.debug(
            "Single device fetched: %s (device_type=%s, reachable=%s)",
            device.id,
            device.device_type,
            device.is_reachable,
        )

        return device

    async def get_devices(self) -> list[DirigeraDevice]:
        """
        Fetch the complete device list from the Dirigera hub.

        Calls GET /devices and parses the response into a list of
        DirigeraDevice instances. Called once at startup by the
        orchestrator and again after a reconnect if a full re-discovery
        is needed.

        Returns:
            list[DirigeraDevice]: All devices currently known to the hub.
                                  Maybe an empty list if the hub has no
                                  paired devices.

        Raises:
            DirigeraBridgeError: REST_REQUEST_FAILED if the HTTP request
                                 fails at the network level.
            DirigeraBridgeError: REST_AUTHENTICATION_ERROR if the hub
                                 returns HTTP 401 or 403.
            DirigeraBridgeError: REST_INVALID_RESPONSE if the response
                                 body cannot be parsed as a device list.
            DirigeraBridgeError: REST_TIMEOUT if the request times out.
        """

        url = f"{self._base_url}/devices"

        logger.info("Fetching device list from Dirigera hub")
        self._metrics.increment(MetricName.REST_REQUESTS_SENT)

        # ── Request ───────────────────────────────────────────────────────
        raw_list = await self._get_json(url)

        # ── Parse ─────────────────────────────────────────────────────────
        if not isinstance(raw_list, list):
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            raise DirigeraBridgeError(
                ErrorCode.REST_INVALID_RESPONSE,
                f"GET /devices: expected a JSON array, got {type(raw_list).__name__}",
            )

        devices: list[DirigeraDevice] = []
        parse_errors = 0

        for raw_device in raw_list:
            try:
                device = DirigeraDevice.model_validate(raw_device)
                devices.append(device)

                logger.debug(
                    "Parsed device: %s (device_type=%s, reachable=%s)",
                    device.id,
                    device.device_type,
                    device.is_reachable,
                )

            except Exception as exc:
                parse_errors += 1
                logger.warning(
                    "Failed to parse device from discovery response (skipping): %s — raw id=%r",
                    exc,
                    raw_device.get("id", "<unknown>")
                    if isinstance(raw_device, dict)
                    else "<not a dict>",
                )

        if parse_errors:
            logger.warning(
                "Device list parsed with %d error(s) — %d device(s) loaded successfully",
                parse_errors,
                len(devices),
            )

        self._metrics.increment(MetricName.REST_REQUESTS_SUCCESS)

        logger.info(
            "Device list fetched: %d device(s) loaded (%d parse error(s))",
            len(devices),
            parse_errors,
        )

        return devices

    async def send_command(
        self,
        logical_id: str,
        attributes: dict[str, Any],
    ) -> None:
        """
        Send an attribute update command to a specific Dirigera device.

        Calls PATCH /devices/{logical_id}/attributes with a JSON body
        of the form [{"attributes": {...}}] as required by the Dirigera
        API. The attributes dict is produced by command_mapper.py and
        passed through unchanged.

        Args:
            logical_id (str):       Dirigera logical device id. Must be
                                    a non-empty string.
            attributes (dict):      Attribute key-value pairs to update.
                                    Must be a non-empty dict. Example:
                                    {"isOn": True, "lightLevel": 80}

        Raises:
            DirigeraBridgeError: INTERNAL_INVALID_ARGUMENT if logical_id
                                 is not a non-empty string or attributes
                                 is not a non-empty dict.
            DirigeraBridgeError: REST_REQUEST_FAILED if the HTTP request
                                 fails at the network level.
            DirigeraBridgeError: REST_AUTHENTICATION_ERROR on HTTP 401/403.
            DirigeraBridgeError: REST_DEVICE_NOT_FOUND on HTTP 404.
            DirigeraBridgeError: REST_TIMEOUT if the request times out.

        Design notes:
           Confirmed via direct REST testing against a live hub: Dirigera
           returns 202 Accepted for a PATCH combining 'isOn' with any
           other attribute (e.g. {"isOn": True, "lightLevel":90}) in a
           single request, but silently applies only 'isOn' and drops
           the rest - the physical device never changes. The same
           attribute sent alone (e.g. {"lightLevel": 40}) in its own
           PATCH is applied correctly. So whenever 'isOn' is combined
           with other attributes, this method splits them into two
           sequential PATCH requests - 'isON' first, then the rest -
           instead of one combined request.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "send_command: logical_id must be a non-empty string",
            )

        if not isinstance(attributes, dict) or not attributes:
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "send_command: attributes must be a non-empty dict",
            )

        # ── Split isOn from other attributes - see Design Notes above  ────
        if "isOn"  in attributes and len(attributes) > 1:
            is_on_only = {"isOn": attributes["isOn"]}
            remaining = {k: v for k, v in attributes.items() if k != "isOn"}

            await self._send_single_patch(logical_id, is_on_only)
            await self._send_single_patch(logical_id, remaining)
            return

        await self._send_single_patch(logical_id, attributes)

    async def _send_single_patch(
        self,
        logical_id: str,
        attributes: dict[str, Any],
    ) -> None:
        """
        Send one PATCH request for a single attributes dict.

        Extracted from send_command() so that a combined isOn + other
        attributes command can be issued as two separate calls to this
        method - see send_command()'s Design notes.

        Args:
           logical_id (str): Dirigera logical device_id.
           attributes (dict): Attribute key-value pairs for this PATCH.

        Raises:
            DirigeraBridgeError: REST_* on any failure - same as
                                 send_command() 
        """

        url = f"{self._base_url}/devices/{logical_id}"
        payload = [{"attributes": attributes}]

        logger.debug(
            "Sending command to device %s: %s",
            logical_id,
            attributes,
        )

        self._metrics.increment(MetricName.REST_REQUESTS_SENT)
        self._metrics.increment(MetricName.REST_COMMANDS_SENT)

        # ── Send ──────────────────────────────────────────────────────────
        await self._patch_json(url, payload, logical_id=logical_id)

        self._metrics.increment(MetricName.REST_REQUESTS_SUCCESS)

        logger.info(
            "Command sent successfully to device %s: %s",
            logical_id,
            attributes,
        )

    async def close(self) -> None:
        """
        Close the underlying aiohttp session and release connections.

        Safe to call even if the session was never opened (no-op).
        Called by the orchestrator during graceful shutdown.
        """

        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info("DirigeraRestClient: session closed")

    # ── Internal ─────────────────────────────────────────────────────────

    def _get_session(self) -> aiohttp.ClientSession:
        """
        Return the shared aiohttp session, creating it on first use.

        Uses ssl=False because Dirigera uses a self-signed TLS
        certificate that cannot be verified against a public CA.
        This is intentional and expected for local hub communication.

        Returns:
            aiohttp.ClientSession: The active session.
        """

        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=self._auth_headers(),
            )
            logger.debug("DirigeraRestClient: aiohttp session created")

        assert self._session is not None
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        """
        Build the authentication headers for all Dirigera requests.

        Returns:
            dict[str, str]: Headers dict with Authorization Bearer token
                            and Content-Type.
        """

        return {
            "Authorization": f"Bearer {self._settings.dirigera_token}",
            "Content-Type": "application/json",
        }

    async def _get_json(self, url: str) -> Any:
        """
        Perform an authenticated GET request and return parsed JSON.

        Args:
            url (str): Full URL to request.

        Returns:
            Any: Parsed JSON response body.

        Raises:
            DirigeraBridgeError: REST_* on any failure.
        """

        session = self._get_session()

        try:
            async with session.get(url, timeout=self._timeout()) as resp:
                logger.debug(
                    "GET %s → HTTP %d",
                    url,
                    resp.status,
                )

                self._raise_for_status(resp.status, url)

                return await resp.json(content_type=None)

        except DirigeraBridgeError:
            raise

        except aiohttp.ServerTimeoutError as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_TIMEOUT,
                f"GET {url}: request timed out",
                cause=exc,
            ) from exc

        except aiohttp.ClientConnectionError as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"GET {url}: connection error — {exc}",
                cause=exc,
            ) from exc

        except Exception as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"GET {url}: unexpected error — {exc}",
                cause=exc,
            ) from exc

    async def _patch_json(
        self,
        url: str,
        payload: Any,
        logical_id: str = "",
    ) -> None:
        """
        Perform an authenticated PATCH request with a JSON body.

        Args:
            url (str):        Full URL to PATCH.
            payload (Any):    JSON-serializable request body.
            logical_id (str): Device id for error messages.

        Raises:
            DirigeraBridgeError: REST_* on any failure.
        """

        session = self._get_session()

        try:
            async with session.patch(
                url,
                json=payload,
                timeout=self._timeout(),
            ) as resp:
                logger.debug(
                    "PATCH %s → HTTP %d",
                    url,
                    resp.status,
                )

                # ── Dirigera returns 202 Accepted on success ───────────────
                if resp.status not in _PATCH_SUCCESS_CODES:
                    self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
                    self._metrics.increment(MetricName.ERROR_REST)
                    self._raise_for_status(
                        resp.status,
                        url,
                        logical_id=logical_id,
                    )

        except DirigeraBridgeError:
            raise

        except aiohttp.ServerTimeoutError as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_TIMEOUT,
                f"PATCH {url}: request timed out",
                cause=exc,
            ) from exc

        except aiohttp.ClientConnectionError as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"PATCH {url}: connection error — {exc}",
                cause=exc,
            ) from exc

        except Exception as exc:
            self._metrics.increment(MetricName.REST_REQUESTS_FAILED)
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"PATCH {url}: unexpected error — {exc}",
                cause=exc,
            ) from exc

    def _raise_for_status(
        self,
        status: int,
        url: str,
        logical_id: str = "",
    ) -> None:
        """
        Raise a typed DirigeraBridgeError for non-success HTTP status
        codes.

        Args:
            status (int):     HTTP status code.
            url (str):        Request URL for the error message.
            logical_id (str): Optional device id for 404 messages.

        Raises:
            DirigeraBridgeError: Typed REST error for the status code.
                                 No-op for 2xx status codes.
        """

        if 200 <= status < 300:
            return

        # ── Map HTTP status to typed error ────────────────────────────────
        if status in (401, 403):
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_AUTHENTICATION_ERROR,
                f"HTTP {status} from Dirigera ({url}) — check DIRIGERA_TOKEN in .env",
            )

        if status == 404:
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_DEVICE_NOT_FOUND,
                f"HTTP 404 from Dirigera ({url}) — "
                f"device not found" + (f": {logical_id}" if logical_id else ""),
            )

        if status == 429:
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"HTTP 429 from Dirigera ({url}) — rate limited, reduce command frequency",
            )

        if status >= 500:
            self._metrics.increment(MetricName.ERROR_REST)
            raise DirigeraBridgeError(
                ErrorCode.REST_REQUEST_FAILED,
                f"HTTP {status} from Dirigera hub ({url}) — hub-side error",
            )

        # Catch-all for other non-success codes
        self._metrics.increment(MetricName.ERROR_REST)
        raise DirigeraBridgeError(
            ErrorCode.REST_REQUEST_FAILED,
            f"HTTP {status} from Dirigera ({url})",
        )

    @staticmethod
    def _timeout() -> aiohttp.ClientTimeout:
        """
        Build an aiohttp timeout object for all requests.

        Uses a fixed 10-second total timeout for all REST calls.
        Dirigera is a local hub — 10 seconds is generous.

        Returns:
            aiohttp.ClientTimeout
        """

        return aiohttp.ClientTimeout(total=10)
