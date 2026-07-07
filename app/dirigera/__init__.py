"""
Dirigera hub communication layer.

Public API for interacting with the Dirigera hub:
- Pydantic models for devices and WebSocket events
- Async REST client for device discovery and commands
- Async WebSocket client for real-time events

This module re-exports the most important classes so consumers
can import from a single location instead of individual files.

Example:
    from app.dirigera import (
        DirigeraRestClient,
        DirigeraWebSocketClient,
        DirigeraDevice,
        DirigeraWebSocketEvent
    )
"""

from .models import (
    DirigeraAttributes,
    DirigeraCapabilities,
    DirigeraDevice,
    DirigeraEventAttributes,
    DirigeraRoom,
    DirigeraWebSocketEvent,
    DirigeraWebSocketEventData,
)

from .rest_client import DirigeraRestClient
from .websocket_client import DirigeraWebSocketClient

__all__ = [
    # Models
    "DirigeraAttributes",
    "DirigeraCapabilities",
    "DirigeraDevice",
    "DirigeraEventAttributes",
    "DirigeraRoom",
    "DirigeraWebSocketEvent",
    "DirigeraWebSocketEventData",
    # Clients
    "DirigeraRestClient",
    "DirigeraWebSocketClient",
]
