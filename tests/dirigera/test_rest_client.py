"""
tests/dirigera/test_rest_client.py

Tests for app/dirigera/rest_client.py

All HTTP calls are mocked — no real network connection is made.

Covers:
    - DirigeraRestClient construction and validation
    - get_devices() — parses response into DirigeraDevice list
    - get_devices() — HTTP error raises REST_REQUEST_FAILED
    - get_devices() — network error raises REST_REQUEST_FAILED
    - send_command() — sends PATCH with correct payload
    - send_command() — HTTP 404 raises REST_DEVICE_NOT_FOUND
    - send_command() — HTTP 401 raises REST_AUTHENTICATION_ERROR
    - send_command() — HTTP 5xx raises REST_REQUEST_FAILED
    - close() — closes session cleanly
    - _auth_headers() — contains Authorization Bearer token
    - _timeout() — is a staticmethod returning aiohttp.ClientTimeout
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dirigera_bridge.config import Settings
from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode
from dirigera_bridge.core.metrics import MetricName, MetricsStore
from dirigera_bridge.dirigera import DirigeraRestClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_client(settings: Settings, metrics: Any = None) -> DirigeraRestClient:
    """Build a DirigeraRestClient with injected settings."""
    from dirigera_bridge.dirigera.rest_client import DirigeraRestClient

    return DirigeraRestClient(
        settings=settings,
        metrics=metrics or MetricsStore(),
    )


def make_mock_response(status: int = 200, json_data: Any = None) -> MagicMock:
    """Build a mock aiohttp response."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_data or [])
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


# ── Construction ──────────────────────────────────────────────────────────────


class TestDirigeraRestClientConstruction:
    @pytest.mark.unit
    def test_valid_construction(self, settings: Settings) -> None:
        """DirigeraRestClient constructs with valid Settings."""
        client = make_client(settings)
        assert client is not None

    @pytest.mark.unit
    def test_invalid_settings_raises(self) -> None:
        """Non-Settings raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.dirigera.rest_client import DirigeraRestClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraRestClient(settings="not_settings", metrics=MetricsStore())  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_metrics_raises(self, settings: Settings) -> None:
        """Non-MetricsStore raises INTERNAL_INVALID_ARGUMENT."""
        from dirigera_bridge.dirigera.rest_client import DirigeraRestClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraRestClient(settings=settings, metrics="not_metrics")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── _auth_headers() ───────────────────────────────────────────────────────────


class TestAuthHeaders:
    @pytest.mark.unit
    def test_auth_header_contains_bearer_token(self, settings: Settings) -> None:
        """Authorization header contains Bearer token."""
        client = make_client(settings)
        headers = client._auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert settings.dirigera_token in headers["Authorization"]

    @pytest.mark.unit
    def test_auth_headers_is_dict(self, settings: Settings) -> None:
        """_auth_headers returns a dict."""
        client = make_client(settings)
        assert isinstance(client._auth_headers(), dict)


# ── _timeout() ────────────────────────────────────────────────────────────────


class TestTimeout:
    @pytest.mark.unit
    def test_timeout_is_staticmethod(self, settings: Settings) -> None:
        """_timeout is a staticmethod."""
        import inspect

        from dirigera_bridge.dirigera.rest_client import DirigeraRestClient

        assert isinstance(
            inspect.getattr_static(DirigeraRestClient, "_timeout"),
            staticmethod,
        )

    @pytest.mark.unit
    def test_timeout_returns_client_timeout(self, settings: Settings) -> None:
        """_timeout() returns an aiohttp.ClientTimeout."""
        import aiohttp

        client = make_client(settings)
        timeout = client._timeout()
        assert isinstance(timeout, aiohttp.ClientTimeout)


# ── get_devices() ─────────────────────────────────────────────────────────────


class TestGetDevices:
    @pytest.mark.unit
    async def test_get_devices_returns_list(
        self,
        settings: Settings,
        light_raw: dict[str, Any],
    ) -> None:
        """get_devices() parses response into DirigeraDevice list."""
        client = make_client(settings)
        mock_response = make_mock_response(200, [light_raw])

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.get.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response.status = 200
            mock_session.return_value = session

            result = await client.get_devices()

        assert isinstance(result, list)

    @pytest.mark.unit
    async def test_get_devices_empty_list(self, settings: Settings) -> None:
        """get_devices() returns empty list for empty response."""
        client = make_client(settings)
        mock_response = make_mock_response(200, [])

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.get.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            result = await client.get_devices()

        assert result == []

    @pytest.mark.unit
    async def test_get_devices_http_error_raises(self, settings: Settings) -> None:
        """HTTP error from get_devices raises REST_REQUEST_FAILED."""
        client = make_client(settings)
        mock_response = make_mock_response(500)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.get.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.get_devices()

        assert exc_info.value.code == ErrorCode.REST_REQUEST_FAILED

    @pytest.mark.unit
    async def test_get_devices_rejects_non_list_response(self, settings: Settings) -> None:
        """A JSON object is not a valid device-list response."""
        client = make_client(settings)
        with (
            patch.object(client, attribute="_get_json", new=AsyncMock(return_value={})),
            pytest.raises(DirigeraBridgeError) as exc_info,
        ):
            await client.get_devices()

        assert exc_info.value.code == ErrorCode.REST_INVALID_RESPONSE

    @pytest.mark.unit
    async def test_get_devices_skips_malformed_device(
        self,
        settings: Settings,
        light_raw: dict[str, Any],
    ) -> None:
        """A malformed entry does not discard valid discovery results."""
        client = make_client(settings)
        with patch.object(
            client, attribute="_get_json", new=AsyncMock(return_value=[light_raw, {"id": "broken"}])
        ):
            devices = await client.get_devices()

        assert len(devices) == 1
        assert client._metrics.get(MetricName.REST_REQUESTS_SUCCESS) == 1

    @pytest.mark.unit
    async def test_get_devices_network_error_raises(self, settings: Settings) -> None:
        """Network error raises REST_REQUEST_FAILED."""
        import aiohttp

        client = make_client(settings)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.get.side_effect = aiohttp.ClientConnectionError("refused")
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.get_devices()

        assert exc_info.value.code == ErrorCode.REST_REQUEST_FAILED


# ── send_command() ────────────────────────────────────────────────────────────


class TestSendCommand:
    @pytest.mark.unit
    async def test_send_command_success(self, settings: Settings) -> None:
        """send_command() sends PATCH with correct payload."""
        client = make_client(settings)
        mock_response = make_mock_response(200, {})

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            await client.send_command(
                logical_id="light_abc_1",
                attributes={"isOn": True},
            )

            # Verify PATCH was called
            session.patch.assert_called_once()
            call_kwargs = session.patch.call_args
            assert "isOn" in str(call_kwargs)

    @pytest.mark.unit
    async def test_send_command_combined_isOn_splits_into_two_patches(
        self, settings: Settings
    ) -> None:
        """
        send_command() splits isOn + other attributes into two separate
        PATCH requests, isOn first, instead of one combined request.

        Dirigera's REST API accepts (202) a combined
        {"isOn": ..., "lightLevel": ...} PATCH but silently applies only
        isOn and drops the rest — confirmed via direct REST testing
        against a live hub. See send_command()'s docstring.
        """
        client = make_client(settings)
        mock_response = make_mock_response(202, {})

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            await client.send_command(
                logical_id="light_abc_1",
                attributes={"isOn": True, "lightLevel": 72},
            )

            # Two separate PATCH calls, not one combined call
            assert session.patch.call_count == 2

            first_call_kwargs = session.patch.call_args_list[0].kwargs
            second_call_kwargs = session.patch.call_args_list[1].kwargs

            # First call: isOn alone
            assert first_call_kwargs["json"] == [{"attributes": {"isOn": True}}]
            # Second call: remaining attributes alone, isOn excluded
            assert second_call_kwargs["json"] == [{"attributes": {"lightLevel": 72}}]

    @pytest.mark.unit
    async def test_send_command_isOn_alone_sends_single_patch(self, settings: Settings) -> None:
        """send_command() does NOT split when isOn is the only attribute."""
        client = make_client(settings)
        mock_response = make_mock_response(202, {})

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            await client.send_command(
                logical_id="light_abc_1",
                attributes={"isOn": False},
            )

            assert session.patch.call_count == 1
            call_kwargs = session.patch.call_args.kwargs
            assert call_kwargs["json"] == [{"attributes": {"isOn": False}}]

    @pytest.mark.unit
    async def test_send_command_non_isOn_attributes_send_single_patch(
        self, settings: Settings
    ) -> None:
        """send_command() does NOT split when isOn is absent entirely."""
        client = make_client(settings)
        mock_response = make_mock_response(202, {})

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            await client.send_command(
                logical_id="light_abc_1",
                attributes={"lightLevel": 40, "colorTemperature": 3000},
            )

            assert session.patch.call_count == 1
            call_kwargs = session.patch.call_args.kwargs
            assert call_kwargs["json"] == [
                {"attributes": {"lightLevel": 40, "colorTemperature": 3000}}
            ]
    
    @pytest.mark.unit
    async def test_send_command_404_raises_device_not_found(self, settings: Settings) -> None:
        """HTTP 404 raises REST_DEVICE_NOT_FOUND."""
        client = make_client(settings)
        mock_response = make_mock_response(404)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_DEVICE_NOT_FOUND

    @pytest.mark.unit
    async def test_send_command_401_raises_auth_error(self, settings: Settings) -> None:
        """HTTP 401 raises REST_AUTHENTICATION_ERROR."""
        client = make_client(settings)
        mock_response = make_mock_response(401)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_AUTHENTICATION_ERROR

    @pytest.mark.unit
    async def test_send_command_5xx_raises_request_failed(self, settings: Settings) -> None:
        """HTTP 500 raises REST_REQUEST_FAILED."""
        client = make_client(settings)
        mock_response = make_mock_response(500)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_REQUEST_FAILED

    @pytest.mark.unit
    async def test_send_command_empty_logical_id_raises(self, settings: Settings) -> None:
        """Empty logical_id raises INTERNAL_INVALID_ARGUMENT."""
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.send_command("", {"isOn": True})

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_send_command_rejects_whitespace_id_and_non_dict_attributes(
        self,
        settings: Settings,
    ) -> None:
        """Validation rejects values that are not usable commands."""
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError):
            await client.send_command("   ", {"isOn": True})
        with pytest.raises(DirigeraBridgeError):
            await client.send_command("light_1", ["not-a-dict"])  # type: ignore[arg-type]

    @pytest.mark.unit
    async def test_send_command_empty_attributes_raises(self, settings: Settings) -> None:
        """Empty attributes dict raises INTERNAL_INVALID_ARGUMENT."""
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.send_command("light_1", {})

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── close() ───────────────────────────────────────────────────────────────────


class TestClose:
    @pytest.mark.unit
    async def test_close_when_no_session_is_noop(self, settings: Settings) -> None:
        """close() with no active session does not raise."""
        client = make_client(settings)
        await client.close()  # should not raise

    @pytest.mark.unit
    async def test_close_closes_session(self, settings: Settings) -> None:
        """close() calls session.close() when session exists."""
        client = make_client(settings)

        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.close()

        mock_session.close.assert_awaited_once()

    @pytest.mark.unit
    async def test_close_keeps_already_closed_session(self, settings: Settings) -> None:
        """An already-closed session needs no second close."""
        client = make_client(settings)
        session = AsyncMock()
        session.closed = True
        client._session = session

        await client.close()

        session.close.assert_not_awaited()


class TestGetDevice:
    @pytest.mark.unit
    async def test_get_device_parses_a_single_device(
        self,
        settings: Settings,
        light_raw: dict[str, Any],
    ) -> None:
        client = make_client(settings)
        with patch.object(client, attribute="_get_json", new=AsyncMock(return_value=light_raw)):
            device = await client.get_device("light_1")

        assert device.id == light_raw["id"]
        assert client._metrics.get(MetricName.REST_REQUESTS_SUCCESS) == 1

    @pytest.mark.unit
    @pytest.mark.parametrize("logical_id", ["", "   ", 42])
    async def test_get_device_rejects_invalid_id(
        self,
        settings: Settings,
        logical_id: str | int,
    ) -> None:
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.get_device(logical_id)  # type: ignore[arg-type]

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_get_device_rejects_non_object_response(self, settings: Settings) -> None:
        client = make_client(settings)
        with (
            patch.object(client, attribute="_get_json", new=AsyncMock(return_value=[])),
            pytest.raises(DirigeraBridgeError) as exc_info,
        ):
            await client.get_device("light_1")

        assert exc_info.value.code == ErrorCode.REST_INVALID_RESPONSE

    @pytest.mark.unit
    async def test_get_device_rejects_invalid_model(
        self,
        settings: Settings,
        light_raw: dict[str, Any],
    ) -> None:
        client = make_client(settings)
        invalid_device = dict(light_raw)
        invalid_device["attributes"] = dict(light_raw["attributes"])
        invalid_device["attributes"]["model"] = None
        with (
            patch.object(
                target=client, attribute="_get_json", new=AsyncMock(return_value=invalid_device)
            ),
            pytest.raises(DirigeraBridgeError) as exc_info,
        ):
            await client.get_device("light_1")

        assert exc_info.value.code == ErrorCode.REST_INVALID_RESPONSE


class TestRestClientInternals:
    @pytest.mark.unit
    def test_get_session_reuses_an_open_session(self, settings: Settings) -> None:
        client = make_client(settings)
        session = MagicMock(closed=False)
        client._session = session

        assert client._get_session() is session

    @pytest.mark.unit
    def test_get_session_creates_session_with_auth_headers(self, settings: Settings) -> None:
        client = make_client(settings)
        with (
            patch("dirigera_bridge.dirigera.rest_client.aiohttp.TCPConnector") as connector,
            patch("dirigera_bridge.dirigera.rest_client.aiohttp.ClientSession") as session_type,
        ):
            session_type.return_value.closed = False
            assert client._get_session() is session_type.return_value

        connector.assert_called_once_with(ssl=False)
        assert session_type.call_args.kwargs["headers"] == client._auth_headers()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ErrorCode.REST_AUTHENTICATION_ERROR),
            (403, ErrorCode.REST_AUTHENTICATION_ERROR),
            (404, ErrorCode.REST_DEVICE_NOT_FOUND),
            (429, ErrorCode.REST_REQUEST_FAILED),
            (500, ErrorCode.REST_REQUEST_FAILED),
            (418, ErrorCode.REST_REQUEST_FAILED),
        ],
    )
    def test_raise_for_status_maps_errors(
        self,
        settings: Settings,
        status: int,
        expected: ErrorCode,
    ) -> None:
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            client._raise_for_status(status, "https://hub/devices/1", logical_id="device_1")

        assert exc_info.value.code == expected

    @pytest.mark.unit
    def test_raise_for_status_accepts_success(self, settings: Settings) -> None:
        client = make_client(settings)
        client._raise_for_status(204, "https://hub/devices/1")

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error_type, expected",
        [
            ("connection", ErrorCode.REST_REQUEST_FAILED),
            ("timeout", ErrorCode.REST_TIMEOUT),
            ("unexpected", ErrorCode.REST_REQUEST_FAILED),
        ],
    )
    async def test_get_json_wraps_transport_errors(
        self,
        settings: Settings,
        error_type: str,
        expected: ErrorCode,
    ) -> None:
        import aiohttp

        client = make_client(settings)
        session = MagicMock()
        if error_type == "connection":
            session.get.side_effect = aiohttp.ClientConnectionError("down")
        elif error_type == "timeout":
            context = MagicMock()
            context.__aenter__ = AsyncMock(side_effect=aiohttp.ServerTimeoutError())
            context.__aexit__ = AsyncMock(return_value=False)
            session.get.return_value = context
        else:
            session.get.side_effect = ValueError("bad response")
        with (
            patch.object(client, attribute="_get_session", new=MagicMock(return_value=session)),
            pytest.raises(DirigeraBridgeError) as exc_info,
        ):
            await client._get_json("https://hub/devices")

        assert exc_info.value.code == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error_type, expected",
        [
            ("connection", ErrorCode.REST_REQUEST_FAILED),
            ("timeout", ErrorCode.REST_TIMEOUT),
            ("unexpected", ErrorCode.REST_REQUEST_FAILED),
        ],
    )
    async def test_patch_json_wraps_transport_errors(
        self,
        settings: Settings,
        error_type: str,
        expected: ErrorCode,
    ) -> None:
        import aiohttp

        client = make_client(settings)
        session = MagicMock()
        if error_type == "connection":
            session.patch.side_effect = aiohttp.ClientConnectionError("down")
        elif error_type == "timeout":
            session.patch.side_effect = aiohttp.ServerTimeoutError()
        else:
            session.patch.side_effect = ValueError("bad response")
        with (
            patch.object(
                client,
                attribute="_get_session",
                new=MagicMock(return_value=session),
            ),
            pytest.raises(DirigeraBridgeError) as exc_info,
        ):
            await client._patch_json("https://hub/devices", [], logical_id="device_1")

        assert exc_info.value.code == expected
