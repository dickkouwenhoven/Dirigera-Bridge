"""
tests/core/test_errors.py

Tests for app/core/errors.py

Covers:
    - ErrorCode enum completeness and uniqueness
    - DirigeraBridgeError construction and validation
    - DirigeraBridgeError string representations
    - Exception chaining via cause parameter
    - format_error() helper
"""

import pytest

from app.core.errors import (
    DirigeraBridgeError,
    ErrorCode,
    format_error,
)


# ── ErrorCode tests ───────────────────────────────────────────────────────────


class TestErrorCode:
    @pytest.mark.unit
    def test_all_values_are_strings(self):
        """Every ErrorCode value is a non-empty string."""
        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert len(code.value) > 0

    @pytest.mark.unit
    def test_all_values_are_unique(self):
        """No two ErrorCodes share the same string value."""
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values))

    @pytest.mark.unit
    def test_required_categories_present(self):
        """All six error categories are represented."""
        values = {code.value for code in ErrorCode}
        categories = [
            "CONFIG_",
            "WS_",
            "REST_",
            "MAPPING_",
            "MQTT_",
            "LIFECYCLE_",
            "INTERNAL_",
        ]
        for cat in categories:
            assert any(v.startswith(cat) for v in values), (
                f"No ErrorCode found for category '{cat}'"
            )

    @pytest.mark.unit
    def test_specific_codes_exist(self):
        """Key error codes referenced throughout the codebase exist."""
        required = [
            ErrorCode.CONFIG_MISSING_FIELD,
            ErrorCode.CONFIG_INVALID_VALUE,
            ErrorCode.WS_CONNECTION_FAILED,
            ErrorCode.REST_REQUEST_FAILED,
            ErrorCode.REST_AUTHENTICATION_ERROR,
            ErrorCode.REST_DEVICE_NOT_FOUND,
            ErrorCode.MAPPING_UNKNOWN_DEVICE_TYPE,
            ErrorCode.MAPPING_INVALID_COMMAND,
            ErrorCode.MQTT_PUBLISH_FAILED,
            ErrorCode.MQTT_CONNECTION_FAILED,
            ErrorCode.LIFECYCLE_INVALID_TRANSITION,
            ErrorCode.LIFECYCLE_STARTUP_FAILED,
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
        ]
        for code in required:
            assert isinstance(code, ErrorCode)

    @pytest.mark.unit
    def test_error_code_is_str_subclass(self):
        """ErrorCode inherits from str so it can be used in f-strings."""
        code = ErrorCode.WS_CONNECTION_FAILED
        assert isinstance(code, str)
        assert code == "WS_CONNECTION_FAILED"


# ── DirigeraBridgeError construction ─────────────────────────────────────────


class TestDirigeraBridgeErrorConstruction:
    @pytest.mark.unit
    def test_basic_construction(self):
        """Can construct with code and message."""
        err = DirigeraBridgeError(
            ErrorCode.WS_CONNECTION_FAILED,
            "Cannot connect to hub",
        )
        assert err.code == ErrorCode.WS_CONNECTION_FAILED
        assert err.message == "Cannot connect to hub"
        assert err.__cause__ is None

    @pytest.mark.unit
    def test_construction_with_cause(self):
        """cause is stored as __cause__ for traceback chaining."""
        original = ConnectionRefusedError("port 8443")
        err = DirigeraBridgeError(
            ErrorCode.REST_REQUEST_FAILED,
            "REST call failed",
            cause=original,
        )
        assert err.__cause__ is original

    @pytest.mark.unit
    def test_is_exception(self):
        """DirigeraBridgeError is a subclass of Exception."""
        err = DirigeraBridgeError(
            ErrorCode.INTERNAL_INVALID_ARGUMENT,
            "bad arg",
        )
        assert isinstance(err, Exception)

    @pytest.mark.unit
    def test_can_be_raised_and_caught(self):
        """Can be raised and caught like a regular exception."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            raise DirigeraBridgeError(
                ErrorCode.MQTT_PUBLISH_FAILED,
                "Publish failed",
            )
        assert exc_info.value.code == ErrorCode.MQTT_PUBLISH_FAILED

    @pytest.mark.unit
    def test_can_catch_by_code(self):
        """Callers can inspect .code to handle specific error categories."""
        try:
            raise DirigeraBridgeError(
                ErrorCode.REST_DEVICE_NOT_FOUND,
                "Device abc_1 not found",
            )
        except DirigeraBridgeError as err:
            assert err.code == ErrorCode.REST_DEVICE_NOT_FOUND
            assert "abc_1" in err.message


# ── DirigeraBridgeError validation ────────────────────────────────────────────


class TestDirigeraBridgeErrorValidation:
    @pytest.mark.unit
    def test_invalid_code_type_raises_type_error(self):
        """Passing a non-ErrorCode as code raises TypeError."""
        with pytest.raises(TypeError):
            DirigeraBridgeError("NOT_AN_ERROR_CODE", "message")

    @pytest.mark.unit
    def test_invalid_code_int_raises_type_error(self):
        """Passing an int as code raises TypeError."""
        with pytest.raises(TypeError):
            DirigeraBridgeError(42, "message")

    @pytest.mark.unit
    def test_empty_message_raises_type_error(self):
        """Empty string message raises TypeError."""
        with pytest.raises(TypeError):
            DirigeraBridgeError(ErrorCode.INTERNAL_INVALID_ARGUMENT, "")

    @pytest.mark.unit
    def test_whitespace_message_raises_type_error(self):
        """Whitespace-only message raises TypeError."""
        with pytest.raises(TypeError):
            DirigeraBridgeError(ErrorCode.INTERNAL_INVALID_ARGUMENT, "   ")

    @pytest.mark.unit
    def test_none_message_raises_type_error(self):
        """None message raises TypeError."""
        with pytest.raises(TypeError):
            DirigeraBridgeError(ErrorCode.INTERNAL_INVALID_ARGUMENT, None)


# ── DirigeraBridgeError string representations ────────────────────────────────


class TestDirigeraBridgeErrorRepresentations:
    @pytest.mark.unit
    def test_str_includes_code_and_message(self):
        """str(error) includes both the error code and the message."""
        err = DirigeraBridgeError(
            ErrorCode.WS_CONNECTION_FAILED,
            "Cannot connect to hub at 192.168.1.100",
        )
        s = str(err)
        assert "WS_CONNECTION_FAILED" in s
        assert "192.168.1.100" in s

    @pytest.mark.unit
    def test_str_format(self):
        """str(error) follows the [CODE] message format."""
        err = DirigeraBridgeError(
            ErrorCode.REST_TIMEOUT,
            "Request timed out",
        )
        assert str(err) == "[REST_TIMEOUT] Request timed out"

    @pytest.mark.unit
    def test_repr_includes_code_and_message(self):
        """repr(error) includes code and message."""
        err = DirigeraBridgeError(
            ErrorCode.MQTT_CONNECTION_FAILED,
            "Broker unreachable",
        )
        r = repr(err)
        assert "MQTT_CONNECTION_FAILED" in r
        assert "Broker unreachable" in r

    @pytest.mark.unit
    def test_repr_includes_cause_when_present(self):
        """repr(error) mentions the cause when one is provided."""
        cause = TimeoutError("connection timed out")
        err = DirigeraBridgeError(
            ErrorCode.REST_TIMEOUT,
            "REST request timed out",
            cause=cause,
        )
        r = repr(err)
        assert "cause" in r

    @pytest.mark.unit
    def test_repr_no_cause_when_absent(self):
        """repr(error) does not mention cause when none is provided."""
        err = DirigeraBridgeError(
            ErrorCode.REST_TIMEOUT,
            "REST request timed out",
        )
        r = repr(err)
        assert "cause=None" not in r


# ── format_error() tests ──────────────────────────────────────────────────────


class TestFormatError:
    @pytest.mark.unit
    def test_format_without_cause(self):
        """format_error returns [CODE] message for errors without cause."""
        err = DirigeraBridgeError(
            ErrorCode.WS_CONNECTION_FAILED,
            "Cannot connect",
        )
        result = format_error(err)
        assert result == "[WS_CONNECTION_FAILED] Cannot connect"

    @pytest.mark.unit
    def test_format_with_cause(self):
        """format_error includes cause type and message."""
        cause = ConnectionRefusedError("port 8443")
        err = DirigeraBridgeError(
            ErrorCode.WS_CONNECTION_FAILED,
            "Cannot connect",
            cause=cause,
        )
        result = format_error(err)
        assert "[WS_CONNECTION_FAILED] Cannot connect" in result
        assert "caused by" in result
        assert "ConnectionRefusedError" in result
        assert "8443" in result

    @pytest.mark.unit
    def test_format_error_invalid_input(self):
        """format_error raises TypeError for non-DirigeraBridgeError input."""
        with pytest.raises(TypeError):
            format_error(ValueError("not a bridge error"))

    @pytest.mark.unit
    def test_format_error_invalid_none(self):
        """format_error raises TypeError for None input."""
        with pytest.raises(TypeError):
            format_error(None)

    @pytest.mark.unit
    def test_format_error_suitable_for_logging(self):
        """format_error output is a single-line string suitable for logs."""
        err = DirigeraBridgeError(
            ErrorCode.MAPPING_DEVICE_BUILD_ERROR,
            "Mapper failed for device xyz",
        )
        result = format_error(err)
        assert isinstance(result, str)
        assert "\n" not in result
