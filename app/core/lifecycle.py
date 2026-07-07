"""
lifecycle.py

Service lifecycle state machine for the Dirigera MQTT Bridge.

Role & Responsibility:
    Owns and enforces the valid states and transitions of the bridge
    service. Every component that needs to know "is the service running?"
    or "are we reconnecting?" consults the lifecycle rather than
    maintaining its own boolean flags. This prevents inconsistent state
    views across components and makes startup/shutdown sequencing
    explicit and testable.

What it does:
    - Defines LifecycleState enum with all valid service states
    - Defines the complete valid transition table (which state can
    move to which next state)
    - Provides ServiceLifecycle class with transition(), current_state,
    is_running(), is_stopping(), and can_transition() methods
    - Emits structured log lines on every transition including the
    previous state, new state, and optional reason string
    - Tracks timestamps of the last transition and service start time
    - Registers optional async callbacks that fire on state transitions
    (used by the orchestrator to trigger connect/disconnect logic)

Arguments / Configuration:
    initial_state (LifecycleState):    Starting state. Defaults to
                    LifecycleState.CREATED. Tests may
                    inject a different starting state.

Used by:
    - app/orchestrator.py            (creates and drives the lifecycle)
    - app/dirigera/websocket_client.py    (reads is_running() to decide
                        whether to reconnect)
    - app/ha/ha_client.py            (reads state before publishing)

Not responsible for:
    - Actually connecting or disconnecting anything (that is the
    orchestrator's job — lifecycle only tracks state)
    - Persisting state across restarts (in-memory only)
    - Thread safety (the entire application is single-threaded asyncio)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Awaitable, Callable, Dict, List, Optional, Set

from .errors import DirigeraBridgeError, ErrorCode

__all__ = [
    "LifecycleState",
    "StateTransition",
    "ServiceLifecycle",
]

logger = logging.getLogger(__name__)

# Type alias for async transition callback
TransitionCallback = Callable[["StateTransition"], Awaitable[None]]


# ── States ────────────────────────────────────────────────────────────────────


@unique
class LifecycleState(str, Enum):
    """
    All valid states of the bridge service.

    State descriptions:
    CREATED        Initial state after object construction.
            No connections established yet.

    STARTING    Service is initializing — loading config,
            establishing Dirigera WebSocket and MQTT
            connections, registering all entities.

    RUNNING        All connections are up and the bridge is
            actively forwarding events in both directions.

    RECONNECTING    One or both connections were lost and the
            service is actively attempting to restore them
            with exponential backoff.

    STOPPING    Graceful shutdown has been requested. The service
            is closing connections and cancelling tasks.

    STOPPED        The service has fully stopped. All connections
            are closed and all tasks have been canceled.
            Terminal state — cannot transition out of STOPPED
            without creating a new instance.

    FAILED        An unrecoverable error has occurred. The service
            cannot continue and must be restarted externally.
            Terminal state.
    """

    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RECONNECTING = "RECONNECTING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


# ── Valid transitions ─────────────────────────────────────────────────────────

# Defines which states each state may transition to.
# Any transition not in this table is illegal and raises an error.
_VALID_TRANSITIONS: Dict[LifecycleState, Set[LifecycleState]] = {
    LifecycleState.CREATED: {
        LifecycleState.STARTING,
        LifecycleState.STOPPING,
        LifecycleState.FAILED,
    },
    LifecycleState.STARTING: {
        LifecycleState.RUNNING,
        LifecycleState.STOPPING,
        LifecycleState.FAILED,
    },
    LifecycleState.RUNNING: {
        LifecycleState.RECONNECTING,
        LifecycleState.STOPPING,
        LifecycleState.FAILED,
    },
    LifecycleState.RECONNECTING: {
        LifecycleState.RUNNING,
        LifecycleState.STOPPING,
        LifecycleState.FAILED,
    },
    LifecycleState.STOPPING: {LifecycleState.STOPPED, LifecycleState.FAILED},
    # Terminal states — no outgoing transitions
    LifecycleState.STOPPED: set(),
    LifecycleState.FAILED: set(),
}


# ── Transition record ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StateTransition:
    """
    Immutable record of a single lifecycle state transition.

    Passed to all registered transition callbacks and stored in the
    transition history.

    Args:
        from_state (LifecycleState):    The state before the transition.
        to_state (LifecycleState):    The state after the transition.
        reason (str):            Human-readable reason for the
                        transition. Empty string if none
                        was provided.
        timestamp (datetime):        UTC timestamp of the transition.
    """

    from_state: LifecycleState
    to_state: LifecycleState
    reason: str
    timestamp: datetime


# ── Lifecycle state machine ───────────────────────────────────────────────────


class ServiceLifecycle:
    """
    Lifecycle state machine for the Dirigera MQTT Bridge service.

    Enforces valid state transitions, emits structured log lines,
    tracks transition history, and fires async callbacks on transition.

    Args:
        initial_state (LifecycleState):    Starting state.
                        Defaults to "CREATED".

    Raises:
        DirigeraBridgeError: If initial_state is not a LifecycleState.
    """

    def __init__(
        self,
        initial_state: LifecycleState = LifecycleState.CREATED,
    ) -> None:

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(initial_state, LifecycleState):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"initial_state must be LifecycleState, "
                f"got {type(initial_state).__name__}",
            )

        self._state: LifecycleState = initial_state
        self._started_at: Optional[datetime] = None
        self._last_transition: Optional[StateTransition] = None
        self._history: List[StateTransition] = []
        self._callbacks: List[TransitionCallback] = []

        logger.info(
            "ServiceLifecycle initialised in state '%s'",
            initial_state.value,
        )

    # ── Public API — state queries ────────────────────────────────────────

    @property
    def current_state(self) -> LifecycleState:
        """
        The current lifecycle state.

        Returns:
            LifecycleState: Current state.
        """
        return self._state

    @property
    def last_transition(self) -> Optional[StateTransition]:
        """
        The most recent state transition, or None if no transition
        has occurred yet.

        Returns:
            StateTransition | None
        """
        return self._last_transition

    @property
    def started_at(self) -> Optional[datetime]:
        """
        UTC datetime when the service entered RUNNING state for the
        first time, or None if it has not reached RUNNING yet.

        Returns:
            datetime | None
        """
        return self._started_at

    @property
    def history(self) -> List[StateTransition]:
        """
        Ordered list of all state transitions since creation.

        Returns a copy so callers cannot mutate internal history.

        Returns:
            List[StateTransition]
        """
        return list(self._history)

    def is_running(self) -> bool:
        """
        Return True if the service is in RUNNING state.

        Use this in reconnect loops and health checks.

        Returns:
            bool
        """
        return self._state == LifecycleState.RUNNING

    def is_active(self) -> bool:
        """
        Return True if the service is in any operational state —
        RUNNING or RECONNECTING.

        Use this to guard operations that should proceed even during
        brief reconnect windows.

        Returns:
            bool
        """
        return self._state in (
            LifecycleState.RUNNING,
            LifecycleState.RECONNECTING,
        )

    def is_stopping(self) -> bool:
        """
        Return True if the service is in STOPPING or STOPPED state.

        Use this in loops that should exit when a shutdown is requested.

        Returns:
            bool
        """
        return self._state in (
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
        )

    def is_terminal(self) -> bool:
        """
        Return True if the service is in a terminal state (STOPPED
        or FAILED) from which it cannot transition further.

        Returns:
            bool
        """
        return self._state in (
            LifecycleState.STOPPED,
            LifecycleState.FAILED,
        )

    def can_transition(self, to_state: LifecycleState) -> bool:
        """
        Return True if transitioning from the current state to
        to_state is valid according to the transition table.

        Does not raise — use this for guard checks before calling
        transition() when you are unsure if a transition is valid.

        Args:
            to_state (LifecycleState): Candidate next state.

        Returns:
            bool: True if the transition is valid.

        Raises:
             DirigeraBridgeError: If to_state is not a LifecycleState.
        """

        if not isinstance(to_state, LifecycleState):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"can_transition: to_state must be LifecycleState, "
                f"got {type(to_state).__name__}",
            )

        return to_state in _VALID_TRANSITIONS.get(self._state, set())

    # ── Public API — state mutation ───────────────────────────────────────

    async def transition(
        self,
        to_state: LifecycleState,
        reason: str = "",
    ) -> StateTransition:
        """
        Transition the service to a new lifecycle state.

        Validates the transition against the transition table, records
        it in history, emits a structured log line, and fires all
        registered async callbacks concurrently.

        Args:
            to_state (LifecycleState):    The state to transition to.
            reason (str):            Optional human-readable reason
                            for the transition. Included in
                            log output and callbacks.

        Returns:
            StateTransition:    Immutable record of the transition that
                        was just performed.

        Raises:
            DirigeraBridgeError:    LIFECYCLE_INVALID_TRANSITION if the
                        transition is not in the valid table.
            DirigeraBridgeError:    INTERNAL_INVALID_ARGUMENT if to_state
                        is not a LifecycleState.
        """

        # ── Validation ────────────────────────────────────────────────────
        if not isinstance(to_state, LifecycleState):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"transition: to_state must be LifecycleState, got {type(to_state).__name__}",
            )

        if not isinstance(reason, str):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                f"transition: reason must be str, got {type(reason).__name__}",
            )

        # ── Guard: check transition is valid ──────────────────────────────
        if not self.can_transition(to_state):
            raise DirigeraBridgeError(
                ErrorCode.LIFECYCLE_INVALID_TRANSITION,
                f"Invalid lifecycle transition: "
                f"{self._state.value} → {to_state.value}"
                + (f" (reason: {reason})" if reason else ""),
            )

        # ── Build transition record ───────────────────────────────────────
        transition_record = StateTransition(
            from_state=self._state,
            to_state=to_state,
            reason=reason,
            timestamp=datetime.now(tz=timezone.utc),
        )

        from_state = self._state

        # ── Apply transition ──────────────────────────────────────────────
        self._state = to_state
        self._last_transition = transition_record
        self._history.append(transition_record)

        # Track when the service first reaches RUNNING
        if to_state == LifecycleState.RUNNING and self._started_at is None:
            self._started_at = transition_record.timestamp

        # ── Log ───────────────────────────────────────────────────────────
        reason_suffix = f" — {reason}" if reason else ""
        logger.info(
            "Lifecycle: %s → %s%s",
            from_state.value,
            to_state.value,
            reason_suffix,
        )

        if to_state == LifecycleState.FAILED:
            logger.error(
                "Service entered FAILED state%s",
                reason_suffix,
            )

        # ── Fire callbacks ────────────────────────────────────────────────
        await self._fire_callbacks(transition_record)

        return transition_record

    def register_callback(self, callback: TransitionCallback) -> None:
        """
        Register an async callback that is called on every state
        transition.

        The callback receives the StateTransition record and can
        inspect from_state / to_state to decide what to do.
        Callbacks are called concurrently. Errors in callbacks are
        logged but do not abort the transition.

        Args:
            callback (TransitionCallback):    Async callable with
                signature async def cb(transition: StateTransition).

        Raises:
            DirigeraBridgeError: If callback is not callable.
        """

        if not callable(callback):
            raise DirigeraBridgeError(
                ErrorCode.INTERNAL_INVALID_ARGUMENT,
                "register_callback: callback must be callable",
            )

        if callback not in self._callbacks:
            self._callbacks.append(callback)
            logger.debug(
                "Lifecycle callback registered: '%s'",
                getattr(callback, "__name__", repr(callback)),
            )

    def unregister_callback(self, callback: TransitionCallback) -> None:
        """
        Remove a previously registered transition callback.

        Safe to call even if the callback is not registered (no-op).

        Args:
            callback (TransitionCallback): The callback to remove.
        """

        if callback in self._callbacks:
            self._callbacks.remove(callback)
            logger.debug(
                "Lifecycle callback unregistered: '%s'",
                getattr(callback, "__name__", repr(callback)),
            )

    # ── Internal ─────────────────────────────────────────────────────────

    async def _fire_callbacks(
        self,
        transition: StateTransition,
    ) -> None:
        """
        Fire all registered callbacks concurrently for a transition.

        Errors in individual callbacks are caught and logged without
        re-raising so a broken callback never blocks a transition.
        """

        if not self._callbacks:
            return

        results = await asyncio.gather(
            *[cb(transition) for cb in self._callbacks],
            return_exceptions=True,
        )

        for cb, result in zip(self._callbacks, results):
            if isinstance(result, Exception):
                logger.error(
                    "Lifecycle callback '%s' raised an error during "
                    "transition %s → %s: %s",
                    getattr(cb, "__name__", repr(cb)),
                    transition.from_state.value,
                    transition.to_state.value,
                    result,
                )
