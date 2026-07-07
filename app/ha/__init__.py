"""
Home Assistant MQTT client package.

Contains the HAClient wrapper around the HASDK AsyncEntityManager.
This is the only package in the application that imports from the SDK.

This package encapsulates:
- MQTT connection lifecycle management
- Entity registration via MQTT discovery
- State and availability publishing
- Command subscription and routing

Consumers should import HAClient directly from this package.
"""

from .ha_client import HAClient

__all__ = [
    "HAClient",
]
