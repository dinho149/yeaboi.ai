"""Tests for the Niko conversation store (niko/store.py).

The property worth defending is the one the platform this was ported from got
wrong: tool calls survive the round trip. A conversation replayed without them
shows an answer with no visible reason for it, and "which numbers did you read?"
becomes unanswerable one restart later.
"""

from __future__ import annotations

import sqlite3

import pytest

from yeaboi.agent.state import NikoToolCall
from yeaboi.niko.store import MAX_TITLE_CHARS, NikoStore, _json_to_calls


@pytest.fixture()
def store(tmp_path):
    with NikoStore(db_path=tmp_path / "sessions.db") as s:
        yield s


class TestConversations:
    def test_create_returns_an_addressable_thread(self, store):
        conversation = store.create()
        assert conversation.id
        assert store.get(conversation.id) == conversation

    def test_get_unknown_is_none_not_an_error(self, store):
        assert store.get("nope") is None

    def test_listed_newest_used_first(self, store):
        first = store.create()
        second = store.create()
        store.add_message(first.id, role="user", content="later")
        assert [c.id for c in store.conversations()] == [first.id, second.id]

    def test_message_count_rides_along(self, store):
        conversation = store.create()
        store.add_message(conversation.id, role="user", content="hi")
        store.add_message(conversation.id, role="assistant", content="hey")
        assert store.get(conversation.id).message_count == 2

    def test_title_is_capped(self, store):
        conversation = store.create()
        store.set_title(conversation.id, "x" * 500)
        assert len(store.get(conversation.id).title) == MAX_TITLE_CHARS


class TestArchiveAndPurge:
    def test_archive_hides_without_losing(self, store):
        conversation = store.create()
        assert store.archive(conversation.id) is True
        assert store.conversations() == []
        assert len(store.conversations(include_archived=True)) == 1

    def test_archive_twice_reports_nothing_to_do(self, store):
        conversation = store.create()
        store.archive(conversation.id)
        assert store.archive(conversation.id) is False

    def test_purge_takes_the_messages_with_it(self, store):
        conversation = store.create()
        store.add_message(conversation.id, role="user", content="hi")
        assert store.purge(conversation.id) is True
        assert store.get(conversation.id) is None
        assert store.messages(conversation.id) == []

    def test_purge_unknown_is_false(self, store):
        assert store.purge("nope") is False


class TestMessages:
    def test_tool_calls_survive_the_round_trip(self, store):
        conversation = store.create()
        call = NikoToolCall(name="llm_usage", arguments={"limit": 5}, ok=True, result={"total": 1.25})
        store.add_message(conversation.id, role="assistant", content="$1.25", tool_calls=(call,))
        assert store.messages(conversation.id)[0].tool_calls == (call,)

    def test_a_failed_tool_call_keeps_its_reason(self, store):
        conversation = store.create()
        call = NikoToolCall(name="ship_status", ok=False, error="no runs yet")
        store.add_message(conversation.id, role="assistant", content="", tool_calls=(call,))
        restored = store.messages(conversation.id)[0].tool_calls[0]
        assert (restored.ok, restored.error) == (False, "no runs yet")

    def test_route_is_snapshotted_per_turn(self, store):
        conversation = store.create()
        store.add_message(conversation.id, role="user", content="a", route="/agents/usage")
        store.add_message(conversation.id, role="user", content="b", route="/team/retro")
        assert [m.route for m in store.messages(conversation.id)] == ["/agents/usage", "/team/retro"]

    def test_ordered_oldest_first_even_within_a_second(self, store):
        conversation = store.create()
        for i in range(5):
            store.add_message(conversation.id, role="user", content=str(i))
        assert [m.content for m in store.messages(conversation.id)] == ["0", "1", "2", "3", "4"]

    def test_adding_a_message_bumps_updated_at(self, store):
        conversation = store.create()
        store._conn.execute(
            "UPDATE niko_conversations SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (conversation.id,)
        )
        store.add_message(conversation.id, role="user", content="hi")
        assert store.get(conversation.id).updated_at > "2000-01-01"


class TestUnreadableRows:
    def test_a_corrupt_tool_blob_loses_the_cards_not_the_conversation(self, store):
        conversation = store.create()
        store.add_message(conversation.id, role="assistant", content="hello")
        store._conn.execute("UPDATE niko_messages SET tool_calls_json = '{{not json'")
        store._conn.commit()
        message = store.messages(conversation.id)[0]
        assert message.content == "hello"
        assert message.tool_calls == ()

    def test_unknown_fields_are_dropped_not_fatal(self):
        assert _json_to_calls('[{"name": "llm_usage", "invented_later": 1}]')[0].name == "llm_usage"

    def test_a_non_list_blob_is_no_calls(self):
        assert _json_to_calls('{"name": "x"}') == ()


class TestSchema:
    def test_schema_is_additive_so_an_existing_db_opens(self, tmp_path):
        path = tmp_path / "sessions.db"
        sqlite3.connect(str(path)).execute("CREATE TABLE unrelated (a TEXT)")
        with NikoStore(db_path=path) as store:
            assert store.create().id

    def test_reopening_finds_the_conversation(self, tmp_path):
        path = tmp_path / "sessions.db"
        with NikoStore(db_path=path) as store:
            conversation_id = store.create().id
            store.add_message(conversation_id, role="user", content="hi")
        with NikoStore(db_path=path) as store:
            assert [m.content for m in store.messages(conversation_id)] == ["hi"]
