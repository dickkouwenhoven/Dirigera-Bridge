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

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.metrics import MetricsStore


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_client(settings, metrics=None):
    """Build a DirigeraRestClient with injected settings."""
    from app.dirigera.rest_client import DirigeraRestClient

    return DirigeraRestClient(
        settings=settings,
        metrics=metrics or MetricsStore(),
    )


def make_mock_response(status=200, json_data=None):
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
    def test_valid_construction(self, settings):
        """DirigeraRestClient constructs with valid Settings."""
        client = make_client(settings)
        assert client is not None

    @pytest.mark.unit
    def test_invalid_settings_raises(self):
        """Non-Settings raises INTERNAL_INVALID_ARGUMENT."""
        from app.dirigera.rest_client import DirigeraRestClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraRestClient(settings="not_settings", metrics=MetricsStore())
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_invalid_metrics_raises(self, settings):
        """Non-MetricsStore raises INTERNAL_INVALID_ARGUMENT."""
        from app.dirigera.rest_client import DirigeraRestClient

        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraRestClient(settings=settings, metrics="not_metrics")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── _auth_headers() ───────────────────────────────────────────────────────────


class TestAuthHeaders:
    @pytest.mark.unit
    def test_auth_header_contains_bearer_token(self, settings):
        """Authorization header contains Bearer token."""
        client = make_client(settings)
        headers = client._auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert settings.dirigera_token in headers["Authorization"]

    @pytest.mark.unit
    def test_auth_headers_is_dict(self, settings):
        """_auth_headers returns a dict."""
        client = make_client(settings)
        assert isinstance(client._auth_headers(), dict)


# ── _timeout() ────────────────────────────────────────────────────────────────


class TestTimeout:
    @pytest.mark.unit
    def test_timeout_is_staticmethod(self, settings):
        """_timeout is a staticmethod."""
        from app.dirigera.rest_client import DirigeraRestClient
        import inspect

        assert isinstance(
            inspect.getattr_static(DirigeraRestClient, "_timeout"),
            staticmethod,
        )

    @pytest.mark.unit
    def test_timeout_returns_client_timeout(self, settings):
        """_timeout() returns an aiohttp.ClientTimeout."""
        import aiohttp

        client = make_client(settings)
        timeout = client._timeout()
        assert isinstance(timeout, aiohttp.ClientTimeout)


# ── get_devices() ─────────────────────────────────────────────────────────────


class TestGetDevices:
    @pytest.mark.unit
    async def test_get_devices_returns_list(self, settings, light_raw):
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
    async def test_get_devices_empty_list(self, settings):
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
    async def test_get_devices_http_error_raises(self, settings):
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
    async def test_get_devices_network_error_raises(self, settings):
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
    async def test_send_command_success(self, settings):
        """send_command() sends PATCH with correct payload."""
        client = make_client(settings)
        mock_response = make_mock_response(200, {})

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
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
    async def test_send_command_404_raises_device_not_found(self, settings):
        """HTTP 404 raises REST_DEVICE_NOT_FOUND."""
        client = make_client(settings)
        mock_response = make_mock_response(404)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_DEVICE_NOT_FOUND

    @pytest.mark.unit
    async def test_send_command_401_raises_auth_error(self, settings):
        """HTTP 401 raises REST_AUTHENTICATION_ERROR."""
        client = make_client(settings)
        mock_response = make_mock_response(401)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_AUTHENTICATION_ERROR

    @pytest.mark.unit
    async def test_send_command_5xx_raises_request_failed(self, settings):
        """HTTP 500 raises REST_REQUEST_FAILED."""
        client = make_client(settings)
        mock_response = make_mock_response(500)

        with patch.object(client, "_get_session") as mock_session:
            session = MagicMock()
            session.patch.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            session.patch.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = session

            with pytest.raises(DirigeraBridgeError) as exc_info:
                await client.send_command("light_1", {"isOn": True})

        assert exc_info.value.code == ErrorCode.REST_REQUEST_FAILED

    @pytest.mark.unit
    async def test_send_command_empty_logical_id_raises(self, settings):
        """Empty logical_id raises INTERNAL_INVALID_ARGUMENT."""
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.send_command("", {"isOn": True})

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_send_command_empty_attributes_raises(self, settings):
        """Empty attributes dict raises INTERNAL_INVALID_ARGUMENT."""
        client = make_client(settings)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await client.send_command("light_1", {})

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── close() ───────────────────────────────────────────────────────────────────


class TestClose:
    @pytest.mark.unit
    async def test_close_when_no_session_is_noop(self, settings):
        """close() with no active session does not raise."""
        client = make_client(settings)
        await client.close()  # should not raise

    @pytest.mark.unit
    async def test_close_closes_session(self, settings):
        """close() calls session.close() when session exists."""
        client = make_client(settings)

        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.close()

        mock_session.close.assert_awaited_once()
