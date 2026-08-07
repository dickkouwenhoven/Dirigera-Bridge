"""
speaker.py

Home Assistant entity mapper for Dirigera speaker devices.

Role & Responsibility:
    Maps Dirigera speaker DeviceContexts to a set of HA entities that
    together expose the speaker's controllable/observable surface.
    Handles IKEA SYMFONISK speakers paired to the Dirigera hub.

    Supported Dirigera deviceTypes (registered in DEVICE_TYPES):
        speaker — IKEA SYMFONISK speaker series

    Home Assistant's MQTT discovery protocol has no 'media_player'
    domain — media_player is a UI/state-machine concept HA builds on
    top of its own integrations, not something exposed over plain
    MQTT discovery the way light/switch/sensor/etc. are. HA-MQTT-SDK
    is deliberately strict and only implements domains HA's MQTT
    discovery protocol actually defines, so composing a "virtual"
    media_player out of primitive MQTT domains is DirigeraApi's job,
    not the SDK's — this file is that composition.

What it does:
    Produces the following HA entities per speaker device, composed
    entirely from HADomain members that already exist in the SDK:

    binary_sensor — reachable   (device_class: connectivity, always)
    sensor        — playback    (raw playback state string, always)
    number        — volume      (0-100, step 1, always)
    button        — next        (payload_press='NEXT', always)
    button        — previous    (payload_press='PREVIOUS', always)
    switch        — power       (isOn, always — see verification note)

Arguments / Configuration:
    No runtime configuration. Pure mapping functions.

Used by:
    - app/mapping/domains/__init__.py  (registered via DEVICE_TYPES)
    - app/mapping/device_mapper.py     (calls map_speaker())
    - app/mapping/state_mapper.py      (must route to matching
                                        unique_id suffixes below)
    - app/mapping/command_mapper.py    (must route incoming payloads
                                        from each of these entities)

Not responsible for:
    - State updates (state_mapper.py reads playback/volume/isOn/
      isReachable and routes to the unique_ids this file defines)
    - Command translation (command_mapper.py maps each entity's HA
      payload to the matching Dirigera REST attribute update)
    - MQTT publishing (ha_client.py)

Design notes — what is CONFIRMED vs ASSUMED:

    This redesign was done without a full real raw-JSON capture of a
    SYMFONISK device's `attributes` block (no SYMFONISK was available
    to capture from). What follows was checked against real sources
    (a maintained third-party Dirigera client library with working
    command methods, and one partial real hub capture from a GitHub
    issue thread) rather than guessed outright, but real-device
    verification once someone captures a live payload is still
    recommended before fully trusting this in production.

    CONFIRMED (cross-checked against a real, maintained Dirigera
    client library's working command API):
        - volume is an integer 0-100 (not HA's older 0.0-1.0 float
          media_player convention) — matches the 'number' domain's
          native range exactly, no conversion needed either direction.
        - Track skip is issued as playback='playbackNext' /
          'playbackPrevious' — these are one-shot COMMANDS, not
          states. There is no evidence of any persisted "current
          track" state exposed by Dirigera's local API at all, so no
          entity is created for track metadata.
        - The device envelope (id/type/deviceType/isReachable/
          lastSeen) matches every other Dirigera device already
          handled elsewhere in this codebase — isReachable is real
          and confirmed present.

    ASSUMED / UNVERIFIED (kept because the prior version of this file
    already assumed them and nothing found contradicts them, but no
    direct confirmation was found either):
        - That the speaker deviceType exposes its own 'isOn' power
          attribute, separate from playback state. If a real capture
          shows no 'isOn' field, the "power" switch entity below
          should be removed rather than silently reporting nothing.
        - The exact playback state string values sent by real
          hardware. A real (JS) client library's command API uses
          'playbackPlaying' / 'playbackPaused' (prefixed) — but this
          mapper does not need to know the exact set, since the
          "playback" entity is a plain diagnostic sensor that simply
          forwards whatever string Dirigera sends, unlike the old
          design which tried to translate into fixed media_player
          states ('idle', 'buffering', etc.) that may not even be
          the real vocabulary.

    NOT SUPPORTED — checked and found no evidence either exists at
    the Dirigera local hub API layer, so intentionally left out
    rather than built against a guess:
        - Playlist / now-playing track metadata (title, artist,
          artwork). This is Sonos app/cloud territory, not something
          the local Dirigera REST/WebSocket API appears to surface.
        - Audio quality / bitrate reporting.

    - The SYMFONISK is mains powered — no battery entity.
    - Each entity gets its own unique_id suffix (reachable, playback,
      volume, next, previous, power) — there is no single "primary"
      entity the way there is for e.g. a light, since this device is
      inherently a composition of several small, equally-important
      controls rather than one dominant control with accessories.
"""

from __future__ import annotations

import logging
from typing import List

from ..device_registry import DeviceContext
from ha_mqtt_sdk import HADomain
from ha_mqtt_sdk import Entity
from ha_mqtt_sdk import DeviceInfo

from . import make_unique_id

__all__ = [
    "DEVICE_TYPES",
    "map_speaker",
]

logger = logging.getLogger(__name__)


def map_speaker(
    context: DeviceContext,  # type: ignore[name-defined]
    device_info: DeviceInfo,
) -> List[Entity]:
    """
    Map a Dirigera speaker DeviceContext to a list of HA entities
    composing a "virtual" media player from primitive MQTT domains.

    Args:
        context (DeviceContext):  Normalised device context from
                                  device_registry.py.
        device_info (DeviceInfo): HASDK DeviceInfo for physical device
                                  grouping in HA.

    Returns:
        List[Entity]: Six entities — binary_sensor, sensor, number,
                      two buttons, and a switch. See module docstring
                      for which parts are confirmed vs assumed.
    """

    lid = context.logical_id
    name = context.device_name

    logger.debug(
        "map_speaker: mapping '%s' (logical_id=%s)",
        name,
        lid,
    )

    entities = [
        _make_reachable_sensor(lid, name, device_info),
        _make_playback_sensor(lid, name, device_info),
        _make_volume_number(lid, name, device_info),
        _make_next_button(lid, name, device_info),
        _make_previous_button(lid, name, device_info),
        _make_power_switch(lid, name, device_info),
    ]

    logger.info(
        "map_speaker: mapped speaker '%s' to %d HA entity(ies)",
        name,
        len(entities),
    )

    return entities


# ── Private entity factories ──────────────────────────────────────────────────


def _make_reachable_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Connectivity binary_sensor for isReachable.

    CONFIRMED: every Dirigera device (including speakers) reports
    isReachable — matches the same pattern already used for
    gateway.py's own connectivity sensor.
    """

    return Entity(
        domain=HADomain.BINARY_SENSOR,
        name=f"{name} Reachable",
        unique_id=make_unique_id(logical_id, "reachable"),
        device_info=device_info,
        extra={
            "device_class": "connectivity",
            "payload_on": "ON",
            "payload_off": "OFF",
            "entity_category": "diagnostic",
        },
    )


def _make_playback_sensor(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Plain string sensor reporting Dirigera's raw playback state.

    ASSUMED/UNVERIFIED: the exact string values Dirigera sends are
    not confirmed. This entity intentionally does not try to
    translate them into a fixed vocabulary (e.g. HA media_player
    states) — it just forwards whatever string state_mapper.py
    receives, so it stays correct even if the real vocabulary differs
    from what was guessed here.
    """

    return Entity(
        domain=HADomain.SENSOR,
        name=f"{name} Playback",
        unique_id=make_unique_id(logical_id, "playback"),
        device_info=device_info,
        extra=None,
    )


def _make_volume_number(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Volume control as an HA 'number' entity, 0-100 step 1.

    CONFIRMED: a real, maintained Dirigera client library's
    setVolume() command takes an integer 0-100 — matches this
    entity's range exactly with no unit conversion needed in either
    direction (unlike the old media_player-style 0.0-1.0 float).
    """

    return Entity(
        domain=HADomain.NUMBER,
        name=f"{name} Volume",
        unique_id=make_unique_id(logical_id, "volume"),
        device_info=device_info,
        extra={
            "min": 0,
            "max": 100,
            "step": 1,
            "mode": "slider",
        },
    )


def _make_next_button(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Momentary button entity for skipping to the next track.

    CONFIRMED: a real, maintained Dirigera client library issues this
    as a one-shot playback='playbackNext' command, not a persisted
    state — matches HA's 'button' domain semantics (a momentary press,
    no state to track) exactly.

    A custom payload_press ('NEXT') is set so command_mapper.py can
    distinguish this button from the previous-track button purely by
    payload content, without needing to know which MQTT topic a
    command arrived on.
    """

    return Entity(
        domain=HADomain.BUTTON,
        name=f"{name} Next Track",
        unique_id=make_unique_id(logical_id, "next"),
        device_info=device_info,
        extra={
            "payload_press": "NEXT",
        },
    )


def _make_previous_button(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Momentary button entity for skipping to the previous track.

    See _make_next_button() — same reasoning, opposite direction.
    """

    return Entity(
        domain=HADomain.BUTTON,
        name=f"{name} Previous Track",
        unique_id=make_unique_id(logical_id, "previous"),
        device_info=device_info,
        extra={
            "payload_press": "PREVIOUS",
        },
    )


def _make_power_switch(
    logical_id: str,
    name: str,
    device_info: DeviceInfo,
) -> Entity:
    """
    Power switch entity for the speaker's isOn attribute.

    ASSUMED/UNVERIFIED: it is not confirmed that Dirigera's speaker
    deviceType actually exposes its own 'isOn' attribute distinct from
    playback state. Kept because the prior version of this file
    already assumed it and nothing found contradicts it — but if a
    real device capture shows no 'isOn' field, remove this entity
    rather than leaving it silently non-functional.
    """

    return Entity(
        domain=HADomain.SWITCH,
        name=f"{name} Power",
        unique_id=make_unique_id(logical_id, "power"),
        device_info=device_info,
        extra=None,
    )


# ── Plugin registry entry ─────────────────────────────────────────────────────

# Maps Dirigera deviceType strings to mapper functions.
# Read by app/mapping/domains/__init__.py at import time.
DEVICE_TYPES = {
    "speaker": map_speaker,
}
