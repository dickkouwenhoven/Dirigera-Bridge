"""
tests/config/test_config.py

Tests for app/config.py

Covers:
    - load_settings() with all required fields present
    - load_settings() — missing required fields
    - load_settings() — invalid field values (type, range)
    - load_settings() — cross-field validation
    - load_settings() — optional fields use defaults
    - load_settings() — optional fields accept custom values
    - load_settings() — LOG_LEVEL case-insensitive
    - load_settings() — reloads on second call (replaces singleton)
    - get_settings() — returns cached singleton
    - get_settings() — raises before load_settings() is called
    - Settings — is frozen (immutable)
    - Settings.safe_repr() — redacts secrets
"""

import pytest
from _pytest.monkeypatch import MonkeyPatch

from dirigera_bridge.config import Settings, get_settings, load_settings
from dirigera_bridge.core.errors import DirigeraBridgeError, ErrorCode

# ── Helpers ───────────────────────────────────────────────────────────────────


def reset_singleton() -> None:
    """Reset the config singleton between tests."""
    import dirigera_bridge.config as cfg

    cfg._settings = None


# ── load_settings() — happy path ──────────────────────────────────────────────


class TestLoadSettingsHappyPath:
    @pytest.mark.unit
    def test_returns_settings_instance(self, settings: Settings) -> None:
        """load_settings() returns a Settings instance."""
        assert isinstance(settings, Settings)

    @pytest.mark.unit
    def test_required_fields_populated(self, settings: Settings) -> None:
        """All required fields are populated from environment."""
        assert settings.dirigera_ip == "192.168.1.100"
        assert settings.dirigera_token == "test_token_abc123"
        assert settings.mqtt_host == "mqtt"
        assert settings.mqtt_port == 1883
        assert settings.mqtt_user == "hauser"
        assert settings.mqtt_password == "testpassword"

    @pytest.mark.unit
    def test_optional_defaults(self, settings: Settings) -> None:
        """Optional fields use their documented defaults."""
        assert settings.mqtt_client_id == "dirigera-bridge-test"
        assert settings.mqtt_keepalive == 60
        assert settings.mqtt_base_topic == "dirigera"
        assert settings.mqtt_qos == 1
        assert settings.mqtt_tls is False
        assert settings.mqtt_reconnect is True
        assert settings.mqtt_reconnect_delay_min == 1.0
        assert settings.mqtt_reconnect_delay_max == 60.0
        assert settings.discovery_prefix == "homeassistant"
        assert settings.log_level == "DEBUG"
        assert settings.metrics_interval == 60
        assert settings.ws_ping_interval == 30
        assert settings.ws_ping_timeout == 10
        assert settings.reconnect_delay_initial == 0.1
        assert settings.reconnect_delay_max == 1.0

    @pytest.mark.unit
    def test_get_settings_returns_same_instance(self, settings: Settings) -> None:
        """get_settings() returns the same instance as load_settings()."""
        assert get_settings() is settings

    @pytest.mark.unit
    def test_reload_replaces_singleton(self, valid_env: None) -> None:
        """Calling load_settings() again replaces the cached singleton."""

        s1 = load_settings()
        s2 = load_settings()
        assert s2 is not s1


# ── load_settings() — missing required fields ─────────────────────────────────


class TestLoadSettingsMissingFields:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "missing_key",
        [
            "DIRIGERA_IP",
            "DIRIGERA_TOKEN",
            "MQTT_HOST",
            "MQTT_USER",
            "MQTT_PASSWORD",
        ],
    )
    def test_missing_required_field_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
        missing_key: str,
    ) -> None:
        """Missing required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.delenv(missing_key, raising=False)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD
        assert missing_key in exc_info.value.message

    @pytest.mark.unit
    def test_empty_required_field_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Empty string for required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.setenv("DIRIGERA_TOKEN", "")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD

    @pytest.mark.unit
    def test_whitespace_required_field_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Whitespace-only value for required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.setenv("DIRIGERA_IP", "   ")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD


# ── load_settings() — invalid field values ────────────────────────────────────


class TestLoadSettingsInvalidValues:
    @pytest.mark.unit
    def test_invalid_mqtt_port_type_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Noninteger MQTT_PORT raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "not_a_number")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_port_too_low_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """MQTT_PORT below 1 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_port_too_high_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """MQTT_PORT above 65535 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "99999")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_mqtt_qos_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """MQTT_QOS outside 0-2 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_QOS", "3")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_log_level_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Unrecognised LOG_LEVEL raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_metrics_interval_too_low_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """METRICS_INTERVAL below minimum raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("METRICS_INTERVAL", "5")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_zero_mqtt_keepalive_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """MQTT_KEEPALIVE=0 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_KEEPALIVE", "0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_mqtt_tls_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Unrecognised MQTT_TLS value raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_TLS", "maybe")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_mqtt_reconnect_raises(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Unrecognised MQTT_RECONNECT value raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_RECONNECT", "maybe")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_reconnect_delay_min_below_minimum_raises(
        self, valid_env: None, monkeypatch: MonkeyPatch
    ) -> None:
        """MQTT_RECONNECT_DELAY_MIN below 0.1 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_RECONNECT_DELAY_MIN", "0.01")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

 
# ── load_settings() — cross-field validation ──────────────────────────────────


class TestLoadSettingsCrossFieldValidation:
    @pytest.mark.unit
    def test_reconnect_max_less_than_initial_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """RECONNECT_DELAY_MAX < RECONNECT_DELAY_INITIAL raises."""
        monkeypatch.setenv("RECONNECT_DELAY_INITIAL", "30.0")
        monkeypatch.setenv("RECONNECT_DELAY_MAX", "5.0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_ws_ping_timeout_equals_interval_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """WS_PING_TIMEOUT >= WS_PING_INTERVAL raises."""
        monkeypatch.setenv("WS_PING_INTERVAL", "10")
        monkeypatch.setenv("WS_PING_TIMEOUT", "10")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_ws_ping_timeout_greater_than_interval_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """WS_PING_TIMEOUT > WS_PING_INTERVAL raises."""
        monkeypatch.setenv("WS_PING_INTERVAL", "10")
        monkeypatch.setenv("WS_PING_TIMEOUT", "15")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_keepalive_less_than_ping_timeout_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_KEEPALIVE <= WS_PING_TIMEOUT raises."""
        monkeypatch.setenv("MQTT_KEEPALIVE", "5")
        monkeypatch.setenv("WS_PING_TIMEOUT", "10")
        monkeypatch.setenv("WS_PING_INTERVAL", "30")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_reconnect_delay_max_less_than_min_raises(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_RECONNECT_DELAY_MAX < MQTT_RECONNECT_DELAY_MIN raises."""
        monkeypatch.setenv("MQTT_RECONNECT_DELAY_MIN", "30.0")
        monkeypatch.setenv("MQTT_RECONNECT_DELAY_MAX", "5.0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_reconnect_delay_independent_of_websocket_reconnect_delay(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_RECONNECT_DELAY_MAX and RECONNECT_DELAY_MAX are independent
        fields — setting one does not affect the other, and each is
        validated only against its own paired minimum."""
        monkeypatch.setenv("RECONNECT_DELAY_INITIAL", "1.0")
        monkeypatch.setenv("RECONNECT_DELAY_MAX", "120.0")
        monkeypatch.setenv("MQTT_RECONNECT_DELAY_MIN", "1.0")
        monkeypatch.setenv("MQTT_RECONNECT_DELAY_MAX", "5.0")

        s = load_settings()

        assert s.reconnect_delay_max == 120.0
        assert s.mqtt_reconnect_delay_max == 5.0


# ── load_settings() — optional field custom values ────────────────────────────


class TestLoadSettingsOptionalCustomValues:
    @pytest.mark.unit
    def test_custom_discovery_prefix(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Custom DISCOVERY_PREFIX is accepted."""
        monkeypatch.setenv("DISCOVERY_PREFIX", "ha")
        s = load_settings()
        assert s.discovery_prefix == "ha"

    @pytest.mark.unit
    def test_custom_mqtt_base_topic(self, valid_env: None, monkeypatch: MonkeyPatch) -> None:
        """Custom MQTT_BASE_TOPIC is accepted."""
        monkeypatch.setenv("MQTT_BASE_TOPIC", "myhome")
        s = load_settings()
        assert s.mqtt_base_topic == "myhome"

    @pytest.mark.unit
    def test_mqtt_qos_zero_is_valid(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_QOS=0 is valid."""
        monkeypatch.setenv("MQTT_QOS", "0")
        s = load_settings()
        assert s.mqtt_qos == 0

    @pytest.mark.unit
    def test_mqtt_qos_two_is_valid(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_QOS=2 is valid."""
        monkeypatch.setenv("MQTT_QOS", "2")
        s = load_settings()
        assert s.mqtt_qos == 2

    @pytest.mark.unit
    def test_mqtt_tls_true_is_valid(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """MQTT_TLS=true is valid."""
        monkeypatch.setenv("MQTT_TLS", "true")
        s = load_settings()
        assert s.mqtt_tls is True

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
    def test_mqtt_reconnect_true_variants_are_valid(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
        value: str,
    ) -> None:
        """MQTT_RECONNECT accepts several truthy spellings, case-insensitively."""
        monkeypatch.setenv("MQTT_RECONNECT", value)
        s = load_settings()
        assert s.mqtt_reconnect is True

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "No", "OFF"])
    def test_mqtt_reconnect_false_variants_are_valid(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
        value: str,
    ) -> None:
        """MQTT_RECONNECT accepts several falsy spellings, case-insensitively."""
        monkeypatch.setenv("MQTT_RECONNECT", value)
        s = load_settings()
        assert s.mqtt_reconnect is False
    
    @pytest.mark.unit
    @pytest.mark.parametrize("level", ["debug", "DEBUG", "Info", "WARNING", "error", "CRITICAL"])
    def test_log_level_case_insensitive(
        self,
        valid_env: None,
        monkeypatch: MonkeyPatch,
        level: str,
    ) -> None:
        """LOG_LEVEL is accepted case-insensitively."""
        monkeypatch.setenv("LOG_LEVEL", level)
        s = load_settings()
        assert s.log_level == level.upper()


# ── get_settings() ────────────────────────────────────────────────────────────


class TestGetSettings:
    @pytest.mark.unit
    def test_raises_before_load(self) -> None:
        """get_settings() raises LIFECYCLE_STARTUP_FAILED before load."""

        with pytest.raises(DirigeraBridgeError) as exc_info:
            get_settings()

        assert exc_info.value.code == ErrorCode.LIFECYCLE_STARTUP_FAILED

    @pytest.mark.unit
    def test_returns_cached_after_load(self, settings: Settings) -> None:
        """get_settings() returns the same object as load_settings()."""
        assert get_settings() is settings


# ── Settings — immutability ───────────────────────────────────────────────────


class TestSettingsImmutability:
    @pytest.mark.unit
    def test_settings_is_frozen(self, settings: Settings) -> None:
        """Settings are frozen — attribute assignment raises."""
        with pytest.raises((AttributeError, TypeError)):
            # noinspection dataclass
            settings.dirigera_ip = "new_ip"  # type: ignore[misc]

    @pytest.mark.unit
    def test_cannot_add_new_attribute(self, settings: Settings) -> None:
        """Cannot add new attributes to frozen Settings."""
        with pytest.raises((AttributeError, TypeError)):
            # noinspection dataclass
            settings.new_field = "value"  # type: ignore[attr-defined]


# ── Settings.safe_repr() ──────────────────────────────────────────────────────


class TestSafeRepr:
    @pytest.mark.unit
    def test_redacts_dirigera_token(self, settings: Settings) -> None:
        """safe_repr() does not include the Dirigera token."""
        r = settings.safe_repr()
        assert settings.dirigera_token not in r
        assert "***" in r

    @pytest.mark.unit
    def test_redacts_mqtt_password(self, settings: Settings) -> None:
        """safe_repr() does not include the MQTT password."""
        r = settings.safe_repr()
        assert settings.mqtt_password not in r
        assert "***" in r

    @pytest.mark.unit
    def test_includes_non_secret_fields(self, settings: Settings) -> None:
        """safe_repr() includes non-secret field values."""
        r = settings.safe_repr()
        assert settings.dirigera_ip in r
        assert settings.mqtt_host in r
        assert settings.discovery_prefix in r

    @pytest.mark.unit
    def test_returns_string(self, settings: Settings) -> None:
        """safe_repr() returns a string."""
        assert isinstance(settings.safe_repr(), str)

    @pytest.mark.unit
    def test_is_single_line(self, settings: Settings) -> None:
        """safe_repr() returns a single-line string."""
        assert "\n" not in settings.safe_repr()


class TestOptionalValueHelpers:
    """Cover helper-specific defaults and validation paths."""

    @pytest.mark.unit
    def test_optional_int_uses_default_for_missing_or_blank_value(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_int

        monkeypatch.delenv("TEST_OPTIONAL_INT", raising=False)
        assert _optional_int("TEST_OPTIONAL_INT", 42, 1) == 42
        monkeypatch.setenv("TEST_OPTIONAL_INT", "   ")
        assert _optional_int("TEST_OPTIONAL_INT", 42, 1) == 42

    @pytest.mark.unit
    def test_optional_bool_uses_default_for_missing_or_blank_value(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_bool

        monkeypatch.delenv("TEST_OPTIONAL_BOOL", raising=False)
        assert _optional_bool("TEST_OPTIONAL_BOOL", True) is True
        monkeypatch.setenv("TEST_OPTIONAL_BOOL", "   ")
        assert _optional_bool("TEST_OPTIONAL_BOOL", False) is False

    @pytest.mark.unit
    def test_optional_bool_rejects_unrecognised_value(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_bool

        monkeypatch.setenv("TEST_OPTIONAL_BOOL", "sort-of")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            _optional_bool("TEST_OPTIONAL_BOOL", True)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE
    
    @pytest.mark.unit
    def test_optional_float_uses_default_for_missing_or_blank_value(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_float

        monkeypatch.delenv("TEST_OPTIONAL_FLOAT", raising=False)
        assert _optional_float("TEST_OPTIONAL_FLOAT", 1.5, 0.0) == 1.5
        monkeypatch.setenv("TEST_OPTIONAL_FLOAT", "   ")
        assert _optional_float("TEST_OPTIONAL_FLOAT", 1.5, 0.0) == 1.5

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["not-a-number", "1.2.3"])
    def test_optional_float_rejects_non_numeric_values(
        self,
        monkeypatch: MonkeyPatch,
        value: str,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_float

        monkeypatch.setenv("TEST_OPTIONAL_FLOAT", value)
        with pytest.raises(DirigeraBridgeError) as exc_info:
            _optional_float("TEST_OPTIONAL_FLOAT", 1.5, 0.0)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_optional_float_rejects_value_below_minimum(self, monkeypatch: MonkeyPatch) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_float

        monkeypatch.setenv("TEST_OPTIONAL_FLOAT", "0.09")
        with pytest.raises(DirigeraBridgeError) as exc_info:
            _optional_float("TEST_OPTIONAL_FLOAT", 1.5, 0.1)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_optional_log_level_uses_default_for_missing_or_blank_value(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # noinspection protected-member
        from dirigera_bridge.config import _optional_log_level

        monkeypatch.delenv("TEST_LOG_LEVEL", raising=False)
        assert _optional_log_level("TEST_LOG_LEVEL", "INFO") == "INFO"
        monkeypatch.setenv("TEST_LOG_LEVEL", "   ")
        assert _optional_log_level("TEST_LOG_LEVEL", "INFO") == "INFO"
