"""
tests/core/test_lifecycle.py

Tests for app/core/lifecycle.py

Covers:
    - LifecycleState enum completeness
    - ServiceLifecycle initial state
    - Valid state transitions (full happy path)
    - Invalid state transitions (rejected with correct error)
    - Terminal states (STOPPED, FAILED) — no further transitions
    - FAILED reachable from every non-terminal state
    - started_at timestamp — set on first RUNNING, never reset
    - Transition history — ordered, immutable copy
    - last_transition property
    - State query helpers: is_running, is_active, is_stopping, is_terminal
    - can_transition guard (non-raising check)
    - Transition callbacks — called on every transition
    - Transition callbacks — error isolation
    - Transition callbacks — unregister
"""

from datetime import UTC
from typing import Any

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.lifecycle import LifecycleState, ServiceLifecycle, StateTransition

# ── LifecycleState enum ───────────────────────────────────────────────────────


class TestLifecycleState:
    @pytest.mark.unit
    def test_all_required_states_exist(self) -> None:
        """All seven service states exist."""
        required = [
            LifecycleState.CREATED,
            LifecycleState.STARTING,
            LifecycleState.RUNNING,
            LifecycleState.RECONNECTING,
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
            LifecycleState.FAILED,
        ]
        for state in required:
            assert isinstance(state, LifecycleState)

    @pytest.mark.unit
    def test_all_values_unique(self) -> None:
        """No two states share the same string value."""
        values = [s.value for s in LifecycleState]
        assert len(values) == len(set(values))

    @pytest.mark.unit
    def test_is_str_subclass(self) -> None:
        """LifecycleState inherits from str."""
        assert isinstance(LifecycleState.RUNNING, str)


# ── ServiceLifecycle initial state ────────────────────────────────────────────


class TestServiceLifecycleInitialState:
    @pytest.mark.unit
    def test_default_initial_state_is_created(self) -> None:
        """Default initial state is CREATED."""
        lc = ServiceLifecycle()
        assert lc.current_state == LifecycleState.CREATED

    @pytest.mark.unit
    def test_custom_initial_state(self) -> None:
        """Can inject a custom initial state (useful in tests)."""
        lc = ServiceLifecycle(initial_state=LifecycleState.RUNNING)
        assert lc.current_state == LifecycleState.RUNNING

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_invalid_initial_state_raises(self) -> None:
        """Non-LifecycleState initial_state raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            ServiceLifecycle(initial_state="bad")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_initial_properties(self, lifecycle: ServiceLifecycle) -> None:
        """Freshly created lifecycle has empty history and no timestamps."""
        assert lifecycle.started_at is None
        assert lifecycle.last_transition is None
        assert lifecycle.history == []


# ── Valid state transitions ───────────────────────────────────────────────────


class TestValidTransitions:
    @pytest.mark.unit
    async def test_created_to_starting(self, lifecycle: ServiceLifecycle) -> None:
        """CREATED → STARTING is valid."""
        t = await lifecycle.transition(LifecycleState.STARTING, reason="boot")
        assert lifecycle.current_state == LifecycleState.STARTING
        assert isinstance(t, StateTransition)
        assert t.from_state == LifecycleState.CREATED
        assert t.to_state == LifecycleState.STARTING
        assert t.reason == "boot"

    @pytest.mark.unit
    async def test_starting_to_running(self, lifecycle: ServiceLifecycle) -> None:
        """STARTING → RUNNING is valid."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        assert lifecycle.current_state == LifecycleState.RUNNING

    @pytest.mark.unit
    async def test_running_to_reconnecting(self, lifecycle: ServiceLifecycle) -> None:
        """RUNNING → RECONNECTING is valid."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        await lifecycle.transition(LifecycleState.RECONNECTING)
        assert lifecycle.current_state == LifecycleState.RECONNECTING

    @pytest.mark.unit
    async def test_reconnecting_to_running(self, lifecycle: ServiceLifecycle) -> None:
        """RECONNECTING → RUNNING is valid (connection restored)."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        await lifecycle.transition(LifecycleState.RECONNECTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        assert lifecycle.current_state == LifecycleState.RUNNING

    @pytest.mark.unit
    async def test_running_to_stopping(self, lifecycle: ServiceLifecycle) -> None:
        """RUNNING → STOPPING is valid."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        await lifecycle.transition(LifecycleState.STOPPING)
        assert lifecycle.current_state == LifecycleState.STOPPING

    @pytest.mark.unit
    async def test_stopping_to_stopped(self, lifecycle: ServiceLifecycle) -> None:
        """STOPPING → STOPPED is valid."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        await lifecycle.transition(LifecycleState.STOPPING)
        await lifecycle.transition(LifecycleState.STOPPED)
        assert lifecycle.current_state == LifecycleState.STOPPED

    @pytest.mark.unit
    async def test_starting_to_stopping(self, lifecycle: ServiceLifecycle) -> None:
        """STARTING → STOPPING is valid (startup aborted)."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.STOPPING)
        assert lifecycle.current_state == LifecycleState.STOPPING

    @pytest.mark.unit
    async def test_transition_returns_state_transition(self, lifecycle: ServiceLifecycle) -> None:
        """transition() returns a StateTransition record."""
        t = await lifecycle.transition(LifecycleState.STARTING)
        assert isinstance(t, StateTransition)
        assert t.from_state == LifecycleState.CREATED
        assert t.to_state == LifecycleState.STARTING
        assert t.timestamp is not None
        assert t.timestamp.tzinfo == UTC


# ── Invalid transitions ───────────────────────────────────────────────────────


class TestInvalidTransitions:
    @pytest.mark.unit
    async def test_created_to_running_is_invalid(self, lifecycle: ServiceLifecycle) -> None:
        """CREATED → RUNNING is not a valid transition."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await lifecycle.transition(LifecycleState.RUNNING)
        assert exc_info.value.code == ErrorCode.LIFECYCLE_INVALID_TRANSITION

    @pytest.mark.unit
    async def test_created_to_stopped_is_invalid(self, lifecycle: ServiceLifecycle) -> None:
        """CREATED → STOPPED is not a valid transition."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await lifecycle.transition(LifecycleState.STOPPED)
        assert exc_info.value.code == ErrorCode.LIFECYCLE_INVALID_TRANSITION

    @pytest.mark.unit
    async def test_stopped_has_no_outgoing_transitions(self) -> None:
        """STOPPED → anything raises LIFECYCLE_INVALID_TRANSITION."""
        lc = ServiceLifecycle(initial_state=LifecycleState.STOPPED)
        for state in LifecycleState:
            with pytest.raises(DirigeraBridgeError) as exc_info:
                await lc.transition(state)
            assert exc_info.value.code == ErrorCode.LIFECYCLE_INVALID_TRANSITION

    @pytest.mark.unit
    async def test_failed_has_no_outgoing_transitions(self) -> None:
        """FAILED → anything raises LIFECYCLE_INVALID_TRANSITION."""
        lc = ServiceLifecycle(initial_state=LifecycleState.FAILED)
        for state in LifecycleState:
            with pytest.raises(DirigeraBridgeError) as exc_info:
                await lc.transition(state)
            assert exc_info.value.code == ErrorCode.LIFECYCLE_INVALID_TRANSITION

    # noinspection PyTypeChecker
    @pytest.mark.unit
    async def test_invalid_to_state_type_raises(self, lifecycle: ServiceLifecycle) -> None:
        """Non-LifecycleState to_state raises INTERNAL_INVALID_ARGUMENT."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await lifecycle.transition("bad_state")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_state_not_changed_on_invalid_transition(
        self,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """State remains unchanged when an invalid transition is attempted."""
        with pytest.raises(DirigeraBridgeError):
            await lifecycle.transition(LifecycleState.STOPPED)
        assert lifecycle.current_state == LifecycleState.CREATED


# ── FAILED reachable from all non-terminal states ─────────────────────────────


class TestFailedTransition:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "start_state",
        [
            LifecycleState.CREATED,
            LifecycleState.STARTING,
            LifecycleState.RUNNING,
            LifecycleState.RECONNECTING,
            LifecycleState.STOPPING,
        ],
    )
    async def test_failed_reachable_from_non_terminal(
        self,
        start_state: LifecycleState,
    ) -> None:
        """FAILED is reachable from every non-terminal state."""
        lc = ServiceLifecycle(initial_state=start_state)
        assert lc.can_transition(LifecycleState.FAILED), f"FAILED not reachable from {start_state}"
        await lc.transition(LifecycleState.FAILED, reason="test failure")
        assert lc.current_state == LifecycleState.FAILED


# ── started_at timestamp ──────────────────────────────────────────────────────


class TestStartedAt:
    @pytest.mark.unit
    async def test_started_at_none_before_running(self, lifecycle: ServiceLifecycle) -> None:
        """started_at is None until RUNNING is first reached."""
        await lifecycle.transition(LifecycleState.STARTING)
        assert lifecycle.started_at is None

    @pytest.mark.unit
    async def test_started_at_set_on_first_running(self, lifecycle: ServiceLifecycle) -> None:
        """started_at is set when RUNNING is first entered."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        assert lifecycle.started_at is not None

    @pytest.mark.unit
    async def test_started_at_not_reset_on_second_running(
        self,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """started_at is not updated when RUNNING is re-entered."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        first_started = lifecycle.started_at

        await lifecycle.transition(LifecycleState.RECONNECTING)
        await lifecycle.transition(LifecycleState.RUNNING)

        assert lifecycle.started_at == first_started

    # noinspection PyUnresolvedReferences
    @pytest.mark.unit
    async def test_started_at_has_utc_timezone(self, lifecycle: ServiceLifecycle) -> None:
        """started_at timestamp is timezone-aware (UTC)."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)
        assert lifecycle.started_at is not None
        assert lifecycle.started_at.tzinfo == UTC


# ── Transition history ────────────────────────────────────────────────────────


class TestTransitionHistory:
    @pytest.mark.unit
    async def test_history_records_transitions(self, lifecycle: ServiceLifecycle) -> None:
        """Each transition is recorded in history in order."""
        await lifecycle.transition(LifecycleState.STARTING)
        await lifecycle.transition(LifecycleState.RUNNING)

        history = lifecycle.history
        assert len(history) == 2
        assert history[0].to_state == LifecycleState.STARTING
        assert history[1].to_state == LifecycleState.RUNNING

    # noinspection PyTypeChecker
    @pytest.mark.unit
    async def test_history_is_a_copy(self, lifecycle: ServiceLifecycle) -> None:
        """history returns a copy — mutating it does not affect internal state."""
        await lifecycle.transition(LifecycleState.STARTING)
        h = lifecycle.history
        h.append("injected")  # type: ignore[arg-type]
        assert len(lifecycle.history) == 1

    # noinspection PyUnresolvedReferences
    @pytest.mark.unit
    async def test_last_transition_reflects_most_recent(self, lifecycle: ServiceLifecycle) -> None:
        """last_transition always reflects the most recent transition."""
        await lifecycle.transition(LifecycleState.STARTING)

        transition = lifecycle.last_transition
        assert transition is not None
        assert transition.to_state == LifecycleState.STARTING

        await lifecycle.transition(LifecycleState.RUNNING)
        transition = lifecycle.last_transition
        assert transition is not None
        assert transition.to_state == LifecycleState.RUNNING


# ── State query helpers ───────────────────────────────────────────────────────


class TestStateQueryHelpers:
    @pytest.mark.unit
    async def test_is_running_true_only_in_running(self, lifecycle: ServiceLifecycle) -> None:
        """is_running() returns True only in RUNNING state."""
        assert lifecycle.is_running() is False
        await lifecycle.transition(LifecycleState.STARTING)
        assert lifecycle.is_running() is False
        await lifecycle.transition(LifecycleState.RUNNING)
        assert lifecycle.is_running() is True
        await lifecycle.transition(LifecycleState.RECONNECTING)
        assert lifecycle.is_running() is False

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "active_state",
        [
            LifecycleState.RUNNING,
            LifecycleState.RECONNECTING,
        ],
    )
    def test_is_active_true_for_operational_states(
        self,
        active_state: LifecycleState,
    ) -> None:
        """is_active() returns True for RUNNING and RECONNECTING."""
        lc = ServiceLifecycle(initial_state=active_state)
        assert lc.is_active() is True

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "inactive_state",
        [
            LifecycleState.CREATED,
            LifecycleState.STARTING,
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
            LifecycleState.FAILED,
        ],
    )
    def test_is_active_false_for_non_operational_states(
        self,
        inactive_state: LifecycleState,
    ) -> None:
        """is_active() returns False for non-operational states."""
        lc = ServiceLifecycle(initial_state=inactive_state)
        assert lc.is_active() is False

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "stopping_state",
        [
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
        ],
    )
    def test_is_stopping_true_for_stopping_and_stopped(
        self,
        stopping_state: LifecycleState,
    ) -> None:
        """is_stopping() returns True for STOPPING and STOPPED."""
        lc = ServiceLifecycle(initial_state=stopping_state)
        assert lc.is_stopping() is True

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "terminal_state",
        [
            LifecycleState.STOPPED,
            LifecycleState.FAILED,
        ],
    )
    def test_is_terminal_true_for_terminal_states(
        self,
        terminal_state: LifecycleState,
    ) -> None:
        """is_terminal() returns True for STOPPED and FAILED."""
        lc = ServiceLifecycle(initial_state=terminal_state)
        assert lc.is_terminal() is True

    @pytest.mark.unit
    def test_is_terminal_false_for_non_terminal(self, lifecycle: ServiceLifecycle) -> None:
        """is_terminal() returns False for CREATED (non-terminal)."""
        assert lifecycle.is_terminal() is False


# ── can_transition guard ──────────────────────────────────────────────────────


class TestCanTransition:
    @pytest.mark.unit
    def test_can_transition_valid(self, lifecycle: ServiceLifecycle) -> None:
        """can_transition returns True for valid next states."""
        assert lifecycle.can_transition(LifecycleState.STARTING) is True
        assert lifecycle.can_transition(LifecycleState.FAILED) is True

    @pytest.mark.unit
    def test_can_transition_invalid(self, lifecycle: ServiceLifecycle) -> None:
        """can_transition returns False for invalid next states."""
        assert lifecycle.can_transition(LifecycleState.RUNNING) is False
        assert lifecycle.can_transition(LifecycleState.STOPPED) is False

    @pytest.mark.unit
    def test_can_transition_does_not_raise(self, lifecycle: ServiceLifecycle) -> None:
        """can_transition never raises — it only returns bool."""
        result = lifecycle.can_transition(LifecycleState.RUNNING)
        assert isinstance(result, bool)

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_can_transition_invalid_type_raises(self, lifecycle: ServiceLifecycle) -> None:
        """can_transition raises for non-LifecycleState argument."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            lifecycle.can_transition("not_a_state")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── Transition callbacks ──────────────────────────────────────────────────────


class TestTransitionCallbacks:
    @pytest.mark.unit
    async def test_callback_called_on_transition(self, lifecycle: ServiceLifecycle) -> None:
        """Registered callback is called on each transition."""
        fired = []

        async def cb(t: StateTransition) -> None:
            fired.append((t.from_state, t.to_state))

        lifecycle.register_callback(cb)
        await lifecycle.transition(LifecycleState.STARTING)

        assert len(fired) == 1
        assert fired[0] == (LifecycleState.CREATED, LifecycleState.STARTING)

    @pytest.mark.unit
    async def test_multiple_callbacks_all_called(self, lifecycle: ServiceLifecycle) -> None:
        """All registered callbacks fire on each transition."""
        results = []

        # noinspection unused-parameter
        async def cb1(transition: StateTransition) -> None:
            results.append("cb1")

        # noinspection unused-parameter
        async def cb2(transition: StateTransition) -> None:
            results.append("cb2")

        lifecycle.register_callback(cb1)
        lifecycle.register_callback(cb2)
        await lifecycle.transition(LifecycleState.STARTING)

        assert set(results) == {"cb1", "cb2"}

    @pytest.mark.unit
    async def test_failing_callback_does_not_abort_transition(
        self,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """A callback that raises does not abort the transition."""

        # noinspection unused-parameter
        async def bad_cb(transition: StateTransition) -> None:
            raise RuntimeError("callback error")

        lifecycle.register_callback(bad_cb)
        await lifecycle.transition(LifecycleState.STARTING)

        # Transition still completed despite callback error
        assert lifecycle.current_state == LifecycleState.STARTING

    @pytest.mark.unit
    async def test_unregister_callback(self, lifecycle: ServiceLifecycle) -> None:
        """Unregistered callback is not called on subsequent transitions."""
        fired = []

        async def cb(t: StateTransition) -> None:
            fired.append(t.to_state)

        lifecycle.register_callback(cb)
        await lifecycle.transition(LifecycleState.STARTING)
        assert len(fired) == 1

        lifecycle.unregister_callback(cb)
        await lifecycle.transition(LifecycleState.RUNNING)
        assert len(fired) == 1  # not called again

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_register_non_callable_raises(self, lifecycle: ServiceLifecycle) -> None:
        """register_callback raises for non-callable argument."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            lifecycle.register_callback("not_callable")  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_callback_receives_correct_transition_record(
        self,
        lifecycle: ServiceLifecycle,
    ) -> None:
        """Callback receives a StateTransition with correct fields."""
        received = []

        # noinspection PyShadowingNames
        async def cb(t: Any) -> None:
            received.append(t)

        lifecycle.register_callback(cb)
        await lifecycle.transition(LifecycleState.STARTING, reason="test")

        assert len(received) == 1
        t = received[0]
        assert isinstance(t, StateTransition)
        assert t.from_state == LifecycleState.CREATED
        assert t.to_state == LifecycleState.STARTING
        assert t.reason == "test"
        assert t.timestamp is not None


# ── transition() validation — line 376 ──────────────────────────────────────


class TestTransitionValidation:
    @pytest.mark.unit
    async def test_non_string_reason_raises(self) -> None:
        """A non-str reason raises INTERNAL_INVALID_ARGUMENT — only
        to_state's invalid-type case was previously covered."""
        lifecycle = ServiceLifecycle()

        with pytest.raises(DirigeraBridgeError) as exc_info:
            await lifecycle.transition(
                LifecycleState.STARTING,
                reason=123,  # type: ignore[arg-type]
            )

        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── callback (un)registration — lines 453->exit, 470->exit ──────────────────


class TestCallbackRegistration:
    @pytest.mark.unit
    def test_registering_same_callback_twice_is_idempotent(self) -> None:
        """Registering the same callback twice does not duplicate it
        (covers the 'already registered' skip branch)."""
        lifecycle = ServiceLifecycle()

        async def cb(_transition: Any) -> None:
            pass

        lifecycle.register_callback(cb)
        lifecycle.register_callback(cb)

        assert lifecycle._callbacks.count(cb) == 1

    @pytest.mark.unit
    def test_unregistering_unknown_callback_is_a_safe_no_op(self) -> None:
        """Unregistering a callback that was never registered is a
        no-op, not an error (covers the 'not registered' skip branch)."""
        lifecycle = ServiceLifecycle()

        async def cb(_transition: Any) -> None:
            pass

        lifecycle.unregister_callback(cb)  # must not raise

        assert cb not in lifecycle._callbacks
