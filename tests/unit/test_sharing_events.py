"""Unit tests for the SSE change-notification layer (yeaboi.sharing.events).

Pure in-process tests — no sockets, no servers. The wire behaviour these feed
lives in ``test_sharing_sse.py``.
"""

import threading
import time

from yeaboi.sharing.events import ChangeWatcher, EventHub, Subscription, state_etag


class TestSubscription:
    def test_wait_returns_true_when_notified(self):
        sub = Subscription("1.2.3.4")
        sub.notify()
        assert sub.wait(0.05) is True

    def test_wait_returns_false_on_timeout(self):
        assert Subscription().wait(0.01) is False

    def test_wait_auto_clears_so_the_next_wait_blocks(self):
        # Without the clear, one notify would make every later wait return
        # instantly and the stream would spin sending duplicate frames.
        sub = Subscription()
        sub.notify()
        assert sub.wait(0.05) is True
        assert sub.wait(0.01) is False

    def test_close_wakes_a_blocked_waiter(self):
        sub = Subscription()
        woke: list[bool] = []

        def _wait() -> None:
            sub.wait(5.0)
            woke.append(sub.closed)

        t = threading.Thread(target=_wait, daemon=True)
        t.start()
        time.sleep(0.05)
        sub.close()
        t.join(timeout=2)
        assert woke == [True]

    def test_repeated_notifies_coalesce(self):
        # Three changes while a frame is being written must produce ONE wake-up,
        # not three — that is the whole reason this is an Event and not a queue.
        sub = Subscription()
        for _ in range(3):
            sub.notify()
        assert sub.wait(0.05) is True
        assert sub.wait(0.01) is False


class TestEventHub:
    def test_subscribe_and_count(self):
        hub = EventHub()
        assert hub.subscriber_count == 0
        hub.subscribe("10.0.0.1")
        assert hub.subscriber_count == 1

    def test_unsubscribe_drops_the_stream(self):
        hub = EventHub()
        sub = hub.subscribe("10.0.0.1")
        assert sub is not None
        hub.unsubscribe(sub)
        assert hub.subscriber_count == 0
        assert sub.closed is True

    def test_unsubscribe_twice_is_harmless(self):
        hub = EventHub()
        sub = hub.subscribe("10.0.0.1")
        assert sub is not None
        hub.unsubscribe(sub)
        hub.unsubscribe(sub)  # the handler's finally may double-call
        assert hub.subscriber_count == 0

    def test_publish_wakes_every_subscriber(self):
        hub = EventHub()
        subs = [hub.subscribe(f"10.0.0.{i}") for i in range(3)]
        hub.publish()
        assert all(s is not None and s.wait(0.05) for s in subs)

    def test_global_cap_refuses_extra_streams(self):
        hub = EventHub(max_streams=2, max_per_ip=99)
        assert hub.subscribe("a") is not None
        assert hub.subscribe("b") is not None
        assert hub.subscribe("c") is None  # caller turns this into a 503

    def test_per_ip_cap_refuses_extra_streams(self):
        hub = EventHub(max_streams=99, max_per_ip=2)
        assert hub.subscribe("10.0.0.1") is not None
        assert hub.subscribe("10.0.0.1") is not None
        assert hub.subscribe("10.0.0.1") is None
        assert hub.subscribe("10.0.0.2") is not None  # a different peer is fine

    def test_blank_ip_skips_the_per_ip_cap(self):
        # An unknown peer address must not let one client starve all the others
        # by sharing a single "" bucket.
        hub = EventHub(max_streams=99, max_per_ip=1)
        assert hub.subscribe("") is not None
        assert hub.subscribe("") is not None

    def test_close_retires_and_wakes_everything(self):
        hub = EventHub()
        subs = [hub.subscribe(f"10.0.0.{i}") for i in range(2)]
        hub.close()
        assert hub.subscriber_count == 0
        assert all(s is not None and s.closed for s in subs)

    def test_subscribe_after_close_is_refused(self):
        hub = EventHub()
        hub.close()
        assert hub.subscribe("10.0.0.1") is None


class TestChangeWatcher:
    def test_publishes_when_the_probe_value_changes(self):
        hub, value = EventHub(), [0]
        sub = hub.subscribe("10.0.0.1")
        assert sub is not None
        watcher = ChangeWatcher(hub, lambda: value[0], interval=0.01)
        watcher.start()
        try:
            value[0] = 1
            assert sub.wait(2.0) is True
        finally:
            watcher.stop()

    def test_does_not_publish_while_the_probe_is_stable(self):
        hub = EventHub()
        sub = hub.subscribe("10.0.0.1")
        assert sub is not None
        watcher = ChangeWatcher(hub, lambda: "constant", interval=0.01)
        watcher.start()
        try:
            # Includes the very first reading, which must seed rather than fire.
            assert sub.wait(0.3) is False
        finally:
            watcher.stop()

    def test_a_raising_probe_does_not_kill_the_watcher(self):
        hub, calls = EventHub(), []
        sub = hub.subscribe("10.0.0.1")
        assert sub is not None

        def _probe() -> int:
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("board busy")
            return len(calls)

        watcher = ChangeWatcher(hub, _probe, interval=0.01)
        watcher.start()
        try:
            assert sub.wait(2.0) is True
        finally:
            watcher.stop()

    def test_stop_is_safe_when_never_started(self):
        ChangeWatcher(EventHub(), lambda: 1).stop()

    def test_start_is_idempotent(self):
        watcher = ChangeWatcher(EventHub(), lambda: 1, interval=0.01)
        watcher.start()
        watcher.start()
        watcher.stop()


class TestStateEtag:
    def _snapshot(self, **over: object) -> dict:
        base: dict = {
            "revision": 3,
            "cards": [{"id": "c1", "text": "hi"}],
            "timer": {"running": False, "end_epoch": None, "duration": 0, "now_epoch": 1000.0},
        }
        base.update(over)
        return base

    def test_is_a_weak_etag(self):
        tag = state_etag(self._snapshot())
        assert tag.startswith('W/"') and tag.endswith('"')

    def test_is_stable_for_an_identical_snapshot(self):
        assert state_etag(self._snapshot()) == state_etag(self._snapshot())

    def test_ignores_the_server_clock(self):
        # The whole point: now_epoch changes every request, so a byte-level ETag
        # would never match and the header would be dead weight.
        a = self._snapshot()
        b = self._snapshot()
        b["timer"] = {**b["timer"], "now_epoch": 999999.0}
        assert state_etag(a) == state_etag(b)

    def test_changes_when_the_revision_changes(self):
        assert state_etag(self._snapshot()) != state_etag(self._snapshot(revision=4))

    def test_changes_when_content_changes_without_the_revision(self):
        assert state_etag(self._snapshot()) != state_etag(self._snapshot(cards=[{"id": "c1", "text": "bye"}]))

    def test_reacts_to_a_real_timer_field(self):
        a = self._snapshot()
        b = self._snapshot()
        b["timer"] = {**b["timer"], "running": True}
        assert state_etag(a) != state_etag(b)

    def test_key_order_does_not_affect_the_tag(self):
        a = {"revision": 1, "cards": []}
        b = {"cards": [], "revision": 1}
        assert state_etag(a) == state_etag(b)

    def test_snapshot_without_a_timer_still_works(self):
        assert state_etag({"revision": 1}).startswith('W/"')
