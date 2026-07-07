"""
app/core — cross-cutting infrastructure.

Exports the primary symbols used across the application so callers
can import from 'app.core' directly rather than from submodules.

"""

from .discovery_cache import DiscoveryCache
from .errors import DirigeraBridgeError, ErrorCode
from .event_bus import AsyncEventBus, DirigeraEvent, EventType
from .lifecycle import ServiceLifecycle, LifecycleState
from .metrics import MetricsStore, MetricName
from .retry import RetryConfig, retry_with_backoff
from .state_cache import StateCache

__all__ = [
    # Discovery Cache
    "DiscoveryCache",
    # Errors
    "DirigeraBridgeError",
    "ErrorCode",
    # Event Bus
    "AsyncEventBus",
    "DirigeraEvent",
    "EventType",
    # Lifecycle
    "ServiceLifecycle",
    "LifecycleState",
    # Metrics
    "MetricsStore",
    "MetricName",
    # Retry
    "RetryConfig",
    "retry_with_backoff",
    # State Cache
    "StateCache",
]
