"""
tests/core/test_event_bus.py

Tests for app/core/event_bus.py

Covers:
    - EventType enum completeness
    - DirigeraEvent construction and validation
    - AsyncEventBus subscribe / unsubscribe (including idempotency)
    - AsyncEventBus publish — single handler, multiple handlers
    - AsyncEventBus publish — handler error isolation
    - AsyncEventBus publish_nowait — fire-and-forget scheduling
    - AsyncEventBus subscriber_count
    - AsyncEventBus clear (per-type and all)
"""

import asyncio

import pytest

from app.core.errors import DirigeraBridgeError, ErrorCode
from app.core.event_bus import DirigeraEvent, EventType


# ── EventType tests ───────────────────────────────────────────────────────────


class TestEventType:
    @pytest.mark.unit
    def test_all_values_are_strings(self):
        """Every EventType value is a non-empty string."""
        for et in EventType:
            assert isinstance(et.value, str)
            assert len(et.value) > 0

    @pytest.mark.unit
    def test_all_values_are_unique(self):
        """No two EventTypes share the same string value."""
        values = [et.value for et in EventType]
        assert len(values) == len(set(values))

    @pytest.mark.unit
    def test_required_event_types_exist(self):
        """All event types used by the application exist."""
        required = [
            EventType.STATE_CHANGED,
            EventType.DEVICE_DISCOVERED,
            EventType.DEVICE_REMOVED,
            EventType.DEVICE_REACHABLE,
            EventType.DEVICE_UNREACHABLE,
            EventType.COMMAND_RECEIVED,
            EventType.DIRIGERA_CONNECTED,
            EventType.DIRIGERA_DISCONNECTED,
            EventType.MQTT_CONNECTED,
            EventType.MQTT_DISCONNECTED,
        ]
        for et in required:
            assert isinstance(et, EventType)


# ── DirigeraEvent tests ───────────────────────────────────────────────────────


class TestDirigeraEvent:
    @pytest.mark.unit
    def test_basic_construction(self):
        """Can construct a DirigeraEvent with required fields."""
        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id="abc_1",
        )
        assert event.event_type == EventType.STATE_CHANGED
        assert event.logical_id == "abc_1"
        assert event.data == {}
        assert event.relation_id == ""

    @pytest.mark.unit
    def test_full_construction(self):
        """Can construct with all fields."""
        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id="fff75d00_1",
            data={"attribute": "isOn", "value": True},
            relation_id="fff75d00",
        )
        assert event.relation_id == "fff75d00"
        assert event.data["attribute"] == "isOn"
        assert event.data["value"] is True

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_invalid_event_type_raises(self):
        """Non-EventType event_type raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraEvent(event_type="bad_type", logical_id="abc_1")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_invalid_logical_id_raises(self):
        """Non-string logical_id raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraEvent(event_type=EventType.STATE_CHANGED, logical_id=123)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_invalid_data_raises(self):
        """Non-dict data raises DirigeraBridgeError."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            DirigeraEvent(
                event_type=EventType.STATE_CHANGED,
                logical_id="abc_1",
                data="not_a_dict",
            )
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_empty_logical_id_is_valid(self):
        """Empty logical_id is valid — used for connection events."""
        event = DirigeraEvent(
            event_type=EventType.DIRIGERA_CONNECTED,
            logical_id="",
        )
        assert event.logical_id == ""

    @pytest.mark.unit
    def test_data_defaults_to_empty_dict(self):
        """data defaults to an empty dict, not None."""
        event = DirigeraEvent(
            event_type=EventType.MQTT_CONNECTED,
            logical_id="",
        )
        assert event.data == {}
        assert isinstance(event.data, dict)


# ── AsyncEventBus subscribe / unsubscribe ─────────────────────────────────────


class TestAsyncEventBusSubscription:
    @pytest.mark.unit
    def test_subscribe_registers_handler(self, event_bus):
        """subscribe() increments subscriber_count."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 1

    @pytest.mark.unit
    def test_subscribe_is_idempotent(self, event_bus):
        """Subscribing the same handler twice does not duplicate it."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 1

    @pytest.mark.unit
    def test_subscribe_multiple_handlers(self, event_bus):
        """Multiple distinct handlers can be subscribed to the same type."""

        # noinspection PyUnusedLocal
        async def h1(event):
            pass

        # noinspection PyUnusedLocal
        async def h2(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, h1)
        event_bus.subscribe(EventType.STATE_CHANGED, h2)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 2

    @pytest.mark.unit
    def test_subscribe_different_event_types(self, event_bus):
        """Handlers for different event types are independent."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.subscribe(EventType.DEVICE_DISCOVERED, handler)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 1
        assert event_bus.subscriber_count(EventType.DEVICE_DISCOVERED) == 1

    @pytest.mark.unit
    def test_unsubscribe_removes_handler(self, event_bus):
        """unsubscribe() removes a previously registered handler."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.unsubscribe(EventType.STATE_CHANGED, handler)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 0

    @pytest.mark.unit
    def test_unsubscribe_unknown_handler_is_noop(self, event_bus):
        """Unsubscribing an unregistered handler does not raise."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.unsubscribe(EventType.STATE_CHANGED, handler)  # no-op
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 0

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_subscribe_invalid_event_type_raises(self, event_bus):
        """subscribe() raises for invalid event_type."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        with pytest.raises(DirigeraBridgeError) as exc_info:
            event_bus.subscribe("not_an_event_type", handler)
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_subscribe_non_callable_raises(self, event_bus):
        """subscribe() raises if handler is not callable."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            event_bus.subscribe(EventType.STATE_CHANGED, "not_callable")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── AsyncEventBus publish ─────────────────────────────────────────────────────


class TestAsyncEventBusPublish:
    @pytest.mark.unit
    async def test_publish_calls_handler(self, event_bus):
        """publish() calls the registered handler."""
        received = []

        # noinspection PyShadowingNames
        async def handler(event):
            received.append(event)

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event = DirigeraEvent(EventType.STATE_CHANGED, "abc_1")
        await event_bus.publish(event)

        assert len(received) == 1
        assert received[0].logical_id == "abc_1"

    @pytest.mark.unit
    async def test_publish_calls_all_handlers(self, event_bus):
        """publish() calls all registered handlers for the event type."""
        results = []

        # noinspection PyUnusedLocal
        async def h1(event):
            results.append("h1")

        # noinspection PyUnusedLocal
        async def h2(event):
            results.append("h2")

        # noinspection PyUnusedLocal
        async def h3(event):
            results.append("h3")

        event_bus.subscribe(EventType.DEVICE_DISCOVERED, h1)
        event_bus.subscribe(EventType.DEVICE_DISCOVERED, h2)
        event_bus.subscribe(EventType.DEVICE_DISCOVERED, h3)

        await event_bus.publish(DirigeraEvent(EventType.DEVICE_DISCOVERED, "dev_1"))

        assert set(results) == {"h1", "h2", "h3"}

    @pytest.mark.unit
    async def test_publish_only_calls_matching_type(self, event_bus):
        """publish() does not call handlers for other event types."""
        state_received = []
        mqtt_received = []

        async def state_handler(event):
            state_received.append(event)

        async def mqtt_handler(event):
            mqtt_received.append(event)

        event_bus.subscribe(EventType.STATE_CHANGED, state_handler)
        event_bus.subscribe(EventType.MQTT_CONNECTED, mqtt_handler)

        await event_bus.publish(DirigeraEvent(EventType.STATE_CHANGED, "dev_1"))

        assert len(state_received) == 1
        assert len(mqtt_received) == 0

    @pytest.mark.unit
    async def test_publish_no_handlers_does_not_raise(self, event_bus):
        """publish() with no handlers is a no-op and does not raise."""
        await event_bus.publish(DirigeraEvent(EventType.COMMAND_RECEIVED, "dev_1"))

    @pytest.mark.unit
    async def test_publish_failing_handler_does_not_block_others(self, event_bus):
        """A handler that raises does not prevent other handlers running."""
        results = []

        # noinspection PyUnusedLocal
        async def bad_handler(event):
            raise RuntimeError("handler crashed")

        # noinspection PyUnusedLocal
        async def good_handler(event):
            results.append("ok")

        event_bus.subscribe(EventType.STATE_CHANGED, bad_handler)
        event_bus.subscribe(EventType.STATE_CHANGED, good_handler)

        # Should not raise even though bad_handler raises
        await event_bus.publish(DirigeraEvent(EventType.STATE_CHANGED, "dev_1"))

        assert results == ["ok"]

    # noinspection PyTypeChecker
    @pytest.mark.unit
    async def test_publish_invalid_event_raises(self, event_bus):
        """publish() raises for non-DirigeraEvent input."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            await event_bus.publish("not_an_event")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    async def test_publish_passes_correct_event_to_handler(self, event_bus):
        """Handler receives the exact event that was published."""
        received = []

        # noinspection PyShadowingNames
        async def handler(event):
            received.append(event)

        event_bus.subscribe(EventType.STATE_CHANGED, handler)

        event = DirigeraEvent(
            event_type=EventType.STATE_CHANGED,
            logical_id="fff75d00_1",
            data={"attribute": "isOn", "value": False},
            relation_id="fff75d00",
        )
        await event_bus.publish(event)

        assert received[0] is event
        assert received[0].data["attribute"] == "isOn"
        assert received[0].relation_id == "fff75d00"


# ── AsyncEventBus publish_nowait ──────────────────────────────────────────────


class TestAsyncEventBusPublishNowait:
    @pytest.mark.unit
    async def test_publish_nowait_schedules_delivery(self, event_bus):
        """publish_nowait schedules the event for delivery."""
        received = []

        async def handler(event):
            received.append(event)

        event_bus.subscribe(EventType.MQTT_CONNECTED, handler)
        event_bus.publish_nowait(DirigeraEvent(EventType.MQTT_CONNECTED, ""))

        # Allow event loop to process the scheduled task
        await asyncio.sleep(0.02)

        assert len(received) == 1

    # noinspection PyTypeChecker
    @pytest.mark.unit
    async def test_publish_nowait_invalid_event_raises(self, event_bus):
        """publish_nowait raises immediately for invalid input."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            event_bus.publish_nowait("not_an_event")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT


# ── AsyncEventBus clear ───────────────────────────────────────────────────────


class TestAsyncEventBusClear:
    @pytest.mark.unit
    def test_clear_specific_type(self, event_bus):
        """clear(event_type) removes only handlers for that type."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.subscribe(EventType.DEVICE_DISCOVERED, handler)

        event_bus.clear(EventType.STATE_CHANGED)

        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 0
        assert event_bus.subscriber_count(EventType.DEVICE_DISCOVERED) == 1

    @pytest.mark.unit
    def test_clear_all(self, event_bus):
        """clear() with no argument removes all handlers for all types."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.subscribe(EventType.DEVICE_DISCOVERED, handler)
        event_bus.subscribe(EventType.MQTT_CONNECTED, handler)

        event_bus.clear()

        for et in EventType:
            assert event_bus.subscriber_count(et) == 0

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_clear_invalid_event_type_raises(self, event_bus):
        """clear() raises for invalid event_type argument."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            event_bus.clear("not_an_event_type")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT

    @pytest.mark.unit
    def test_clear_empty_bus_is_noop(self, event_bus):
        """clear() on an empty bus does not raise."""
        event_bus.clear()
        for et in EventType:
            assert event_bus.subscriber_count(et) == 0


# ── AsyncEventBus subscriber_count ───────────────────────────────────────────


class TestAsyncEventBusSubscriberCount:
    @pytest.mark.unit
    def test_initial_count_is_zero(self, event_bus):
        """All event types start with zero subscribers."""
        for et in EventType:
            assert event_bus.subscriber_count(et) == 0

    @pytest.mark.unit
    def test_count_increments_on_subscribe(self, event_bus):
        """subscriber_count reflects the number of subscribed handlers."""

        # noinspection PyUnusedLocal
        async def h1(event):
            pass

        # noinspection PyUnusedLocal
        async def h2(event):
            pass

        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 0
        event_bus.subscribe(EventType.STATE_CHANGED, h1)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 1
        event_bus.subscribe(EventType.STATE_CHANGED, h2)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 2

    @pytest.mark.unit
    def test_count_decrements_on_unsubscribe(self, event_bus):
        """subscriber_count decrements when a handler is removed."""

        # noinspection PyUnusedLocal
        async def handler(event):
            pass

        event_bus.subscribe(EventType.STATE_CHANGED, handler)
        event_bus.unsubscribe(EventType.STATE_CHANGED, handler)
        assert event_bus.subscriber_count(EventType.STATE_CHANGED) == 0

    # noinspection PyTypeChecker
    @pytest.mark.unit
    def test_count_invalid_event_type_raises(self, event_bus):
        """subscriber_count raises for invalid event_type."""
        with pytest.raises(DirigeraBridgeError) as exc_info:
            event_bus.subscriber_count("bad_type")
        assert exc_info.value.code == ErrorCode.INTERNAL_INVALID_ARGUMENT
