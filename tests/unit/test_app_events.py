"""The ambient event bus — pub/sub, overflow, SSE framing, cleanup."""

from __future__ import annotations

import json
import queue

import pytest

from yeaboi.app.events import MAX_QUEUED_EVENTS, MAX_SUBSCRIBERS, EventBus


class TestPublishSubscribe:
    def test_events_carry_seq_and_ts(self):
        bus = EventBus()
        event = bus.publish("progress", op_id="x")
        assert event["type"] == "progress"
        assert event["op_id"] == "x"
        assert event["seq"] == 1
        assert event["ts"] > 0
        assert bus.publish("progress")["seq"] == 2

    def test_subscriber_receives_published_events(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("notification", text="hi")
        assert q.get_nowait()["text"] == "hi"

    def test_publish_without_subscribers_is_fine(self):
        EventBus().publish("progress")

    def test_overflow_drops_oldest(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(MAX_QUEUED_EVENTS + 5):
            bus.publish("progress", i=i)
        first = q.get_nowait()
        assert first["i"] == 5  # 0..4 were dropped oldest-first

    def test_subscriber_cap(self):
        bus = EventBus()
        for _ in range(MAX_SUBSCRIBERS):
            bus.subscribe()
        with pytest.raises(RuntimeError, match="too many"):
            bus.subscribe()

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.publish("progress")
        with pytest.raises(queue.Empty):
            q.get_nowait()


class TestSseStream:
    def test_connected_comment_then_data_frames(self):
        bus = EventBus()
        stream = bus.sse_stream()
        assert next(stream) == b": connected\n\n"
        bus.publish("progress", op_id="x")
        frame = next(stream)
        assert frame.startswith(b"data: ") and frame.endswith(b"\n\n")
        payload = json.loads(frame[len(b"data: ") :])
        assert payload["type"] == "progress"
        stream.close()

    def test_idle_emits_ping(self):
        bus = EventBus()
        stream = bus.sse_stream(ping_seconds=0.01)
        next(stream)  # connected
        assert next(stream) == b": ping\n\n"
        stream.close()

    def test_close_unsubscribes(self):
        bus = EventBus()
        stream = bus.sse_stream()
        next(stream)
        assert bus.subscriber_count == 1
        stream.close()
        assert bus.subscriber_count == 0
