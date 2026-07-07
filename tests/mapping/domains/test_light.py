"""
tests/mapping/domains/test_light.py

Tests for app/mapping/domains/light.py

Covers:
    - map_light() — produces exactly one entity
    - map_light() — entity domain is HADomain.LIGHT
    - map_light() — entity name equals device_name
    - map_light() — unique_id correct (no suffix for primary entity)
    - Capability tier detection:
        Tier 1: on/off only           → supported_color_modes=["onoff"]
        Tier 2: dimmable              → supported_color_modes=["brightness"]
        Tier 3: colour temperature    → supported_color_modes=["color_temp"]
        Tier 4: full colour (RGB/HS)  → supported_color_modes=["hs","color_temp"]
    - brightness_scale=100 when dimming capable
    - brightness_scale absent for on/off only lights
    - min_mireds / max_mireds set from colorTemperatureMin/Max
    - Kelvin inversion: Dirigera min/max mapping to HA mireds
    - min_mireds / max_mireds absent when CT range missing
    - JSON schema set for RGB lights, absent for simpler lights
    - CT-only lights use schema=None (not json schema)
    - _kelvin_to_mireds() — standard conversions
    - _kelvin_to_mireds() — zero/negative raises
    - DEVICE_TYPES registry maps 'light' to map_light
    - Real TRADFRI CWS fixture maps correctly
"""

import pytest

from app.mapping.domains.light import (
    DEVICE_TYPES,
    _build_light_extra,
    _kelvin_to_mireds,
    map_light,
)


# ── Shared mock objects ───────────────────────────────────────────────────────


class MockDeviceInfo(dict):
    """
    Minimal DeviceInfo double for domain-mapper unit tests.

    ha_mqtt_sdk.DeviceInfo is a TypedDict (a plain dict at runtime)
    since v0.4+, so this must subclass dict to satisfy the
    isinstance(device_info, dict) check in make_battery_entity().
    Subclassing dict means every existing MockDeviceInfo() call site
    across the test suite keeps working unchanged.
    """

    pass


class MockAttrs:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class MockContext:
    def __init__(
        self,
        capabilities=None,
        attrs=None,
        name="Test Light",
        lid="light_abc_1",
    ):
        self.logical_id = lid
        self.device_name = name
        self.capabilities = capabilities or []
        self.attributes = MockAttrs(attrs or {})


# Capability constants matching domains/light.py
CAP_IS_ON = "isOn"
CAP_DIMMING = "lightLevel"
CAP_CT = "colorTemperature"
CAP_HUE = "colorHue"
CAP_SAT = "colorSaturation"

# Real TRADFRI colour temperature range (from fixture data)
TRADFRI_CT_ATTRS = {
    "colorTemperatureMin": 4000,  # cool (higher K = lower mireds)
    "colorTemperatureMax": 2202,  # warm (lower K = higher mireds)
}


# ── map_light() — structure ───────────────────────────────────────────────────


class TestMapLightStructure:
    @pytest.mark.unit
    def test_returns_single_element_list(self):
        """map_light always returns a list with exactly one entity."""
        ctx = MockContext(capabilities=[CAP_IS_ON])
        result = map_light(ctx, MockDeviceInfo())
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.unit
    def test_entity_domain_is_light(self):
        """Entity domain is HADomain.LIGHT."""
        ctx = MockContext(capabilities=[CAP_IS_ON])
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].domain.value == "light"

    @pytest.mark.unit
    def test_entity_name_equals_device_name(self):
        """Entity name equals the device_name from context."""
        ctx = MockContext(capabilities=[CAP_IS_ON], name="Woonkamerlamp")
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].name == "Woonkamerlamp"

    @pytest.mark.unit
    def test_unique_id_has_no_suffix(self):
        """Primary light entity unique_id has no suffix."""
        ctx = MockContext(capabilities=[CAP_IS_ON], lid="light_abc_1")
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].unique_id == "dirigera_light_abc_1"

    @pytest.mark.unit
    def test_unique_id_hyphens_replaced(self):
        """Hyphens in logical_id are replaced with underscores."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON],
            lid="f47bd1c3-3e57-47c3-b762-1e27bd8d791c_1",
        )
        result = map_light(ctx, MockDeviceInfo())
        assert "-" not in result[0].unique_id
        assert result[0].unique_id.startswith("dirigera_")


# ── Capability tier detection ─────────────────────────────────────────────────


class TestCapabilityTierDetection:
    @pytest.mark.unit
    def test_tier_1_on_off_only(self):
        """isOn only → supported_color_modes=['onoff']."""
        ctx = MockContext(capabilities=[CAP_IS_ON])
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["supported_color_modes"] == ["onoff"]

    @pytest.mark.unit
    def test_tier_1_no_brightness_scale(self):
        """On/off only light has no brightness_scale."""
        ctx = MockContext(capabilities=[CAP_IS_ON])
        result = map_light(ctx, MockDeviceInfo())
        assert "brightness_scale" not in result[0].extra

    @pytest.mark.unit
    def test_tier_2_dimmable(self):
        """isOn + lightLevel → supported_color_modes=['brightness']."""
        ctx = MockContext(capabilities=[CAP_IS_ON, CAP_DIMMING])
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["supported_color_modes"] == ["brightness"]

    @pytest.mark.unit
    def test_tier_2_has_brightness_scale(self):
        """Dimmable light has brightness_scale=100."""
        ctx = MockContext(capabilities=[CAP_IS_ON, CAP_DIMMING])
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].extra["brightness_scale"] == 100

    @pytest.mark.unit
    def test_tier_3_colour_temperature(self):
        """isOn + lightLevel + colorTemperature → color_temp mode."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["supported_color_modes"] == ["color_temp"]

    @pytest.mark.unit
    def test_tier_3_no_json_schema(self):
        """CT-only light does not use JSON schema."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].extra.get("schema") != "json"

    @pytest.mark.unit
    def test_tier_4_full_colour_rgb(self):
        """Full colour → supported_color_modes includes 'hs'."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT, CAP_HUE, CAP_SAT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert "hs" in extra["supported_color_modes"]
        assert "color_temp" in extra["supported_color_modes"]

    @pytest.mark.unit
    def test_tier_4_uses_json_schema(self):
        """Full colour light uses JSON schema."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT, CAP_HUE, CAP_SAT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].extra.get("schema") == "json"

    @pytest.mark.unit
    def test_rgb_only_no_ct_in_modes(self):
        """RGB without CT only has 'hs' in supported_color_modes."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_HUE, CAP_SAT],
        )
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert "hs" in extra["supported_color_modes"]
        assert "color_temp" not in extra["supported_color_modes"]

    @pytest.mark.unit
    def test_empty_capabilities_defaults_to_onoff(self):
        """Empty capabilities list defaults to on/off tier."""
        ctx = MockContext(capabilities=[])
        result = map_light(ctx, MockDeviceInfo())
        assert result[0].extra["supported_color_modes"] == ["onoff"]


# ── Colour temperature range (Kelvin → Mireds) ────────────────────────────────


class TestColourTemperatureRange:
    @pytest.mark.unit
    def test_min_mireds_set_from_colorTemperatureMin(self):
        """min_mireds = 1M / colorTemperatureMin (cooler K → smaller mireds)."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs={"colorTemperatureMin": 4000, "colorTemperatureMax": 2202},
        )
        result = map_light(ctx, MockDeviceInfo())
        # min_mireds = 1M / 4000 = 250
        assert result[0].extra["min_mireds"] == 250

    @pytest.mark.unit
    def test_max_mireds_set_from_colorTemperatureMax(self):
        """max_mireds = 1M / colorTemperatureMax (warmer K → larger mireds)."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs={"colorTemperatureMin": 4000, "colorTemperatureMax": 2202},
        )
        result = map_light(ctx, MockDeviceInfo())
        # max_mireds = 1M / 2202 ≈ 454
        assert result[0].extra["max_mireds"] == pytest.approx(454, abs=2)

    @pytest.mark.unit
    def test_mireds_absent_when_ct_range_missing(self):
        """min/max_mireds absent when CT range not in attributes."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs={},  # no CT range
        )
        result = map_light(ctx, MockDeviceInfo())
        assert "min_mireds" not in result[0].extra
        assert "max_mireds" not in result[0].extra

    @pytest.mark.unit
    def test_real_tradfri_cws_mireds(self):
        """Real TRADFRI CWS range: 4000K(cool)/2202K(warm) → 250/454 mireds."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT, CAP_HUE, CAP_SAT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["min_mireds"] == 250
        assert extra["max_mireds"] == pytest.approx(454, abs=2)

    @pytest.mark.unit
    def test_min_mireds_less_than_max_mireds(self):
        """min_mireds is always less than max_mireds (HA convention)."""
        ctx = MockContext(
            capabilities=[CAP_IS_ON, CAP_DIMMING, CAP_CT],
            attrs=TRADFRI_CT_ATTRS,
        )
        result = map_light(ctx, MockDeviceInfo())
        extra = result[0].extra
        assert extra["min_mireds"] < extra["max_mireds"]


# ── _kelvin_to_mireds() ───────────────────────────────────────────────────────


class TestKelvinToMireds:
    @pytest.mark.unit
    def test_4000k_to_250_mireds(self):
        """4000K → 250 mireds."""
        assert _kelvin_to_mireds(4000) == 250

    @pytest.mark.unit
    def test_2000k_to_500_mireds(self):
        """2000K → 500 mireds."""
        assert _kelvin_to_mireds(2000) == 500

    @pytest.mark.unit
    def test_6500k_to_154_mireds(self):
        """6500K → 154 mireds (rounded)."""
        assert _kelvin_to_mireds(6500) == 154

    @pytest.mark.unit
    def test_2202k_rounds_correctly(self):
        """2202K → 454 mireds (1M/2202 ≈ 454.1)."""
        assert _kelvin_to_mireds(2202) == 454

    @pytest.mark.unit
    def test_returns_integer(self):
        """_kelvin_to_mireds always returns an int."""
        result = _kelvin_to_mireds(3000)
        assert isinstance(result, int)

    @pytest.mark.unit
    def test_zero_kelvin_raises(self):
        """0K raises ValueError."""
        with pytest.raises(ValueError):
            _kelvin_to_mireds(0)

    @pytest.mark.unit
    def test_negative_kelvin_raises(self):
        """Negative Kelvin raises ValueError."""
        with pytest.raises(ValueError):
            _kelvin_to_mireds(-100)

    @pytest.mark.unit
    def test_float_kelvin_accepted(self):
        """Float Kelvin values are accepted."""
        result = _kelvin_to_mireds(4000.0)
        assert result == 250


# ── _build_light_extra() ──────────────────────────────────────────────────────


class TestBuildLightExtra:
    @pytest.mark.unit
    def test_on_off_only(self):
        """On/off tier produces minimal extra dict."""
        extra = _build_light_extra({}, False, False, False)
        assert extra["supported_color_modes"] == ["onoff"]
        assert "brightness_scale" not in extra
        assert "schema" not in extra

    @pytest.mark.unit
    def test_dimmable(self):
        """Dimmable tier includes brightness_scale."""
        extra = _build_light_extra({}, True, False, False)
        assert extra["supported_color_modes"] == ["brightness"]
        assert extra["brightness_scale"] == 100

    @pytest.mark.unit
    def test_colour_temp(self):
        """CT tier includes color_temp mode and mireds."""
        attrs = {"colorTemperatureMin": 4000, "colorTemperatureMax": 2202}
        extra = _build_light_extra(attrs, True, True, False)
        assert extra["supported_color_modes"] == ["color_temp"]
        assert "min_mireds" in extra
        assert "max_mireds" in extra

    @pytest.mark.unit
    def test_full_colour(self):
        """Full colour tier includes hs + color_temp + json schema."""
        attrs = {"colorTemperatureMin": 4000, "colorTemperatureMax": 2202}
        extra = _build_light_extra(attrs, True, True, True)
        assert extra["schema"] == "json"
        assert "hs" in extra["supported_color_modes"]
        assert "color_temp" in extra["supported_color_modes"]

    @pytest.mark.unit
    def test_invalid_ct_range_handled_gracefully(self):
        """Invalid CT range does not raise — mireds simply absent."""
        attrs = {"colorTemperatureMin": 0, "colorTemperatureMax": 0}
        extra = _build_light_extra(attrs, True, True, False)
        # Should not raise, mireds may or may not be present
        assert "supported_color_modes" in extra


# ── Real fixture integration ──────────────────────────────────────────────────


class TestMapLightWithRealFixture:
    @pytest.mark.unit
    def test_tradfri_cws_full_colour(self, light_raw):
        """Real TRADFRI CWS light maps to full colour entity."""
        from app.dirigera.models import DirigeraDevice
        from app.mapping.device_registry import build_device_contexts

        device = DirigeraDevice.model_validate(light_raw)
        regular, _ = build_device_contexts([device])
        ctx = regular[0]

        result = map_light(ctx, MockDeviceInfo())

        assert len(result) == 1
        entity = result[0]
        assert entity.domain.value == "light"
        assert entity.name == "Raamverlichting"
        assert entity.extra["schema"] == "json"
        assert "hs" in entity.extra["supported_color_modes"]
        assert "color_temp" in entity.extra["supported_color_modes"]
        assert "min_mireds" in entity.extra
        assert "max_mireds" in entity.extra


# ── DEVICE_TYPES registry ─────────────────────────────────────────────────────


class TestLightDeviceTypes:
    @pytest.mark.unit
    def test_light_key_registered(self):
        """DEVICE_TYPES maps 'light' to map_light."""
        assert "light" in DEVICE_TYPES
        assert DEVICE_TYPES["light"] is map_light

    @pytest.mark.unit
    def test_only_one_key_registered(self):
        """Only 'light' is registered in this module."""
        assert len(DEVICE_TYPES) == 1
