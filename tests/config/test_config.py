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

from app.config import Settings, get_settings, load_settings
from app.core.errors import DirigeraBridgeError, ErrorCode


# ── Helpers ───────────────────────────────────────────────────────────────────


def reset_singleton():
    """Reset the config singleton between tests."""
    import app.config as cfg

    cfg._settings = None


# ── load_settings() — happy path ──────────────────────────────────────────────


class TestLoadSettingsHappyPath:
    @pytest.mark.unit
    def test_returns_settings_instance(self, settings):
        """load_settings() returns a Settings instance."""
        assert isinstance(settings, Settings)

    @pytest.mark.unit
    def test_required_fields_populated(self, settings):
        """All required fields are populated from environment."""
        assert settings.dirigera_ip == "192.168.1.100"
        assert settings.dirigera_token == "test_token_abc123"
        assert settings.mqtt_host == "mqtt"
        assert settings.mqtt_port == 1883
        assert settings.mqtt_user == "hauser"
        assert settings.mqtt_password == "testpassword"

    @pytest.mark.unit
    def test_optional_defaults(self, settings):
        """Optional fields use their documented defaults."""
        assert settings.mqtt_client_id == "dirigera-bridge-test"
        assert settings.mqtt_keepalive == 60
        assert settings.mqtt_base_topic == "dirigera"
        assert settings.mqtt_qos == 1
        assert settings.discovery_prefix == "homeassistant"
        assert settings.log_level == "DEBUG"
        assert settings.metrics_interval == 60
        assert settings.ws_ping_interval == 30
        assert settings.ws_ping_timeout == 10
        assert settings.reconnect_delay_initial == 0.1
        assert settings.reconnect_delay_max == 1.0

    @pytest.mark.unit
    def test_get_settings_returns_same_instance(self, settings):
        """get_settings() returns the same instance as load_settings()."""
        assert get_settings() is settings

    @pytest.mark.unit
    def test_reload_replaces_singleton(self, valid_env):
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
    def test_missing_required_field_raises(self, valid_env, monkeypatch, missing_key):
        """Missing required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.delenv(missing_key, raising=False)

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD
        assert missing_key in exc_info.value.message

    @pytest.mark.unit
    def test_empty_required_field_raises(self, valid_env, monkeypatch):
        """Empty string for required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.setenv("DIRIGERA_TOKEN", "")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD

    @pytest.mark.unit
    def test_whitespace_required_field_raises(self, valid_env, monkeypatch):
        """Whitespace-only value for required field raises CONFIG_MISSING_FIELD."""
        monkeypatch.setenv("DIRIGERA_IP", "   ")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_MISSING_FIELD


# ── load_settings() — invalid field values ────────────────────────────────────


class TestLoadSettingsInvalidValues:
    @pytest.mark.unit
    def test_invalid_mqtt_port_type_raises(self, valid_env, monkeypatch):
        """Non-integer MQTT_PORT raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "not_a_number")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_port_too_low_raises(self, valid_env, monkeypatch):
        """MQTT_PORT below 1 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_port_too_high_raises(self, valid_env, monkeypatch):
        """MQTT_PORT above 65535 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_PORT", "99999")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_mqtt_qos_raises(self, valid_env, monkeypatch):
        """MQTT_QOS outside 0-2 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_QOS", "3")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_invalid_log_level_raises(self, valid_env, monkeypatch):
        """Unrecognised LOG_LEVEL raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_metrics_interval_too_low_raises(self, valid_env, monkeypatch):
        """METRICS_INTERVAL below minimum raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("METRICS_INTERVAL", "5")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_zero_mqtt_keepalive_raises(self, valid_env, monkeypatch):
        """MQTT_KEEPALIVE=0 raises CONFIG_INVALID_VALUE."""
        monkeypatch.setenv("MQTT_KEEPALIVE", "0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE


# ── load_settings() — cross-field validation ──────────────────────────────────


class TestLoadSettingsCrossFieldValidation:
    @pytest.mark.unit
    def test_reconnect_max_less_than_initial_raises(self, valid_env, monkeypatch):
        """RECONNECT_DELAY_MAX < RECONNECT_DELAY_INITIAL raises."""
        monkeypatch.setenv("RECONNECT_DELAY_INITIAL", "30.0")
        monkeypatch.setenv("RECONNECT_DELAY_MAX", "5.0")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_ws_ping_timeout_equals_interval_raises(self, valid_env, monkeypatch):
        """WS_PING_TIMEOUT >= WS_PING_INTERVAL raises."""
        monkeypatch.setenv("WS_PING_INTERVAL", "10")
        monkeypatch.setenv("WS_PING_TIMEOUT", "10")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_ws_ping_timeout_greater_than_interval_raises(self, valid_env, monkeypatch):
        """WS_PING_TIMEOUT > WS_PING_INTERVAL raises."""
        monkeypatch.setenv("WS_PING_INTERVAL", "10")
        monkeypatch.setenv("WS_PING_TIMEOUT", "15")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE

    @pytest.mark.unit
    def test_mqtt_keepalive_less_than_ping_timeout_raises(self, valid_env, monkeypatch):
        """MQTT_KEEPALIVE <= WS_PING_TIMEOUT raises."""
        monkeypatch.setenv("MQTT_KEEPALIVE", "5")
        monkeypatch.setenv("WS_PING_TIMEOUT", "10")
        monkeypatch.setenv("WS_PING_INTERVAL", "30")

        with pytest.raises(DirigeraBridgeError) as exc_info:
            load_settings()

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID_VALUE


# ── load_settings() — optional field custom values ────────────────────────────


class TestLoadSettingsOptionalCustomValues:
    @pytest.mark.unit
    def test_custom_discovery_prefix(self, valid_env, monkeypatch):
        """Custom DISCOVERY_PREFIX is accepted."""
        monkeypatch.setenv("DISCOVERY_PREFIX", "ha")
        s = load_settings()
        assert s.discovery_prefix == "ha"

    @pytest.mark.unit
    def test_custom_mqtt_base_topic(self, valid_env, monkeypatch):
        """Custom MQTT_BASE_TOPIC is accepted."""
        monkeypatch.setenv("MQTT_BASE_TOPIC", "myhome")
        s = load_settings()
        assert s.mqtt_base_topic == "myhome"

    @pytest.mark.unit
    def test_mqtt_qos_zero_is_valid(self, valid_env, monkeypatch):
        """MQTT_QOS=0 is valid."""
        monkeypatch.setenv("MQTT_QOS", "0")
        s = load_settings()
        assert s.mqtt_qos == 0

    @pytest.mark.unit
    def test_mqtt_qos_two_is_valid(self, valid_env, monkeypatch):
        """MQTT_QOS=2 is valid."""
        monkeypatch.setenv("MQTT_QOS", "2")
        s = load_settings()
        assert s.mqtt_qos == 2

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "level", ["debug", "DEBUG", "Info", "WARNING", "error", "CRITICAL"]
    )
    def test_log_level_case_insensitive(self, valid_env, monkeypatch, level):
        """LOG_LEVEL is accepted case-insensitively."""
        monkeypatch.setenv("LOG_LEVEL", level)
        s = load_settings()
        assert s.log_level == level.upper()


# ── get_settings() ────────────────────────────────────────────────────────────


class TestGetSettings:
    @pytest.mark.unit
    def test_raises_before_load(self):
        """get_settings() raises LIFECYCLE_STARTUP_FAILED before load."""

        with pytest.raises(DirigeraBridgeError) as exc_info:
            get_settings()

        assert exc_info.value.code == ErrorCode.LIFECYCLE_STARTUP_FAILED

    @pytest.mark.unit
    def test_returns_cached_after_load(self, settings):
        """get_settings() returns the same object as load_settings()."""
        assert get_settings() is settings


# ── Settings — immutability ───────────────────────────────────────────────────


class TestSettingsImmutability:
    @pytest.mark.unit
    def test_settings_is_frozen(self, settings):
        """Settings is frozen — attribute assignment raises."""
        with pytest.raises((AttributeError, TypeError)):
            settings.dirigera_ip = "new_ip"

    @pytest.mark.unit
    def test_cannot_add_new_attribute(self, settings):
        """Cannot add new attributes to frozen Settings."""
        with pytest.raises((AttributeError, TypeError)):
            settings.new_field = "value"


# ── Settings.safe_repr() ──────────────────────────────────────────────────────


class TestSafeRepr:
    @pytest.mark.unit
    def test_redacts_dirigera_token(self, settings):
        """safe_repr() does not include the Dirigera token."""
        r = settings.safe_repr()
        assert settings.dirigera_token not in r
        assert "***" in r

    @pytest.mark.unit
    def test_redacts_mqtt_password(self, settings):
        """safe_repr() does not include the MQTT password."""
        r = settings.safe_repr()
        assert settings.mqtt_password not in r
        assert "***" in r

    @pytest.mark.unit
    def test_includes_non_secret_fields(self, settings):
        """safe_repr() includes non-secret field values."""
        r = settings.safe_repr()
        assert settings.dirigera_ip in r
        assert settings.mqtt_host in r
        assert settings.discovery_prefix in r

    @pytest.mark.unit
    def test_returns_string(self, settings):
        """safe_repr() returns a string."""
        assert isinstance(settings.safe_repr(), str)

    @pytest.mark.unit
    def test_is_single_line(self, settings):
        """safe_repr() returns a single-line string."""
        assert "\n" not in settings.safe_repr()
