"""
Mapping layer for Dirigera → Home Assistant translation.

Contains the device registry (physical grouping), device mapper
(routing to domain mappers), state mapper (Dirigera → HA payloads),
command mapper (HA → Dirigera payloads), and all domain mapper files.

This package is responsible for:
- Translating HA commands → Dirigera REST payloads (CommandMapper)
- Translating Dirigera devices → HA entities (DeviceMapper)
- Translating Dirigera state events → HA MQTT payloads (StateMapper)
- Normalizing raw Dirigera devices into DeviceContext objects

Public API:
    - CommandMapper / CommandPayload
    - DeviceMapper
    - StateMapper / StatePayload
    - DeviceContext / build_device_contexts

Example:
    from app.mapping import (
        CommandMapper,
        DeviceMapper,
        StateMapper,
        build_device_contexts
    )
"""

# ── Command mapping ──────────────────────────────────────────────────────
from .command_mapper import (
    CommandMapper,
    CommandPayload,
)

# ── Device mapping ───────────────────────────────────────────────────────
from .device_mapper import DeviceMapper

# ── Device registry / context building ───────────────────────────────────
from .device_registry import (
    DeviceContext,
    build_device_contexts,
)

# ── State mapping ────────────────────────────────────────────────────────
from .state_mapper import (
    StateMapper,
    StatePayload,
)

__all__ = [
    # Command mapping
    "CommandMapper",
    "CommandPayload",
    # Device mapping
    "DeviceMapper",
    # Device registry / context
    "DeviceContext",
    "build_device_contexts",
    # State mapping
    "StateMapper",
    "StatePayload",
]
