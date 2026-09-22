"""Integration tests for backend/db.py's CHAT HISTORY section (pipeline_chat_
messages) against the real Postgres DB. Skipped automatically if no DB is
reachable (see conftest.py's db_available fixture) -- mirrors
test_session_store.py's own pattern.

Every test uses a unique conversation_id and hard-deletes its own rows in a
finally block, so a failed run never leaves stray rows for the next run to
trip over."""

import pytest

from modes.trial_balance.pipeline.db import (
    append_turns,
    delete_conversation,
    get_messages,
    history_for_agent,
    list_conversations,
    new_conversation_id,
    soft_delete_expired_chat_messages,
)

USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _cleanup(conversation_id):
    from modes.trial_balance.pipeline.db import db_cursor

    with db_cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM pipeline_chat_messages WHERE conversation_id = %s", (conversation_id,))


@pytest.fixture
def conversation(db_available):
    """A real pipeline_chat_messages conversation (one turn), cleaned up
    afterward regardless of outcome."""
    if not db_available:
        pytest.skip("No reachable Postgres DB for this test run.")
    conversation_id = new_conversation_id()
    append_turns(USER_A, conversation_id, "PYTEST_DOC", "What is the current ratio?", "It is 1.8.")
    yield conversation_id
    _cleanup(conversation_id)


class TestAppendAndGetMessages:
    def test_append_turns_writes_both_roles(self, conversation):
        rows = get_messages(USER_A, conversation)
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[0]["content"] == "What is the current ratio?"
        assert rows[1]["content"] == "It is 1.8."

    def test_seq_increments_across_multiple_turns(self, conversation):
        append_turns(USER_A, conversation, "PYTEST_DOC", "And the quick ratio?", "It is 1.2.")
        rows = get_messages(USER_A, conversation)
        assert len(rows) == 4
        assert [r["seq"] for r in rows] == [1, 2, 3, 4]

    def test_get_messages_returns_empty_for_unknown_conversation(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        assert get_messages(USER_A, new_conversation_id()) == []

    def test_get_messages_returns_empty_for_a_malformed_conversation_id(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        assert get_messages(USER_A, "not-a-uuid") == []

    def test_get_messages_is_scoped_to_the_owning_user(self, conversation):
        """The actual isolation mechanism: user_id is in the WHERE clause, so a
        different user gets [] for someone else's conversation_id -- the same
        empty-means-not-found-or-not-yours contract router.py turns into 404."""
        assert get_messages(USER_B, conversation) == []


class TestHistoryForAgent:
    def test_returns_role_content_pairs_only(self, conversation):
        history = history_for_agent(USER_A, conversation)
        assert history == [
            {"role": "user", "content": "What is the current ratio?"},
            {"role": "assistant", "content": "It is 1.8."},
        ]

    def test_respects_the_turns_limit(self, conversation):
        for i in range(5):
            append_turns(USER_A, conversation, "PYTEST_DOC", f"Question {i}", f"Answer {i}")
        history = history_for_agent(USER_A, conversation, turns=4)
        assert len(history) == 4
        # Most recent turns, not the oldest.
        assert history[-1]["content"] == "Answer 4"


class TestListConversations:
    def test_a_fresh_conversation_appears_in_the_listing(self, conversation):
        rows = list_conversations(USER_A, limit=200)
        assert any(r["conversation_id"] == conversation for r in rows)

    def test_listing_is_scoped_to_the_owning_user(self, conversation):
        rows = list_conversations(USER_B, limit=200)
        assert not any(r["conversation_id"] == conversation for r in rows)

    def test_tb_doc_id_is_recorded_on_the_summary(self, conversation):
        rows = list_conversations(USER_A, limit=200)
        row = next(r for r in rows if r["conversation_id"] == conversation)
        assert row["tb_doc_id"] == "PYTEST_DOC"

    def test_title_is_derived_from_the_first_user_turn(self, conversation):
        """Regression guard: list_conversations used to return title_src only
        and leave building a display title "to the caller" -- no caller ever
        did, so every TB conversation showed a blank title in the sidebar.
        Mirrors financial_statement/conversations.py's own _title_from()."""
        rows = list_conversations(USER_A, limit=200)
        row = next(r for r in rows if r["conversation_id"] == conversation)
        assert row["title"] == "What is the current ratio?"

    def test_title_is_trimmed_at_a_word_boundary_past_70_chars(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        conversation_id = new_conversation_id()
        long_question = "This is a very long trial balance question that goes on and on past the seventy character title cap"
        append_turns(USER_A, conversation_id, "PYTEST_DOC", long_question, "Answer.")
        try:
            rows = list_conversations(USER_A, limit=200)
            row = next(r for r in rows if r["conversation_id"] == conversation_id)
            assert len(row["title"]) <= 71
            assert row["title"][-1] == "…"
            assert row["title"][-2] != " "
        finally:
            _cleanup(conversation_id)


class TestDeleteConversation:
    def test_owner_can_delete_their_own_conversation(self, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        conversation_id = new_conversation_id()
        append_turns(USER_A, conversation_id, "PYTEST_DOC", "Q", "A")
        try:
            removed = delete_conversation(USER_A, conversation_id)
            assert removed == 2  # both turns hard-deleted
            assert get_messages(USER_A, conversation_id) == []
        finally:
            _cleanup(conversation_id)

    def test_delete_is_scoped_to_the_owning_user(self, conversation):
        """A non-owner's delete call removes nothing -- the conversation is
        still there for its real owner afterward."""
        removed = delete_conversation(USER_B, conversation)
        assert removed == 0
        assert len(get_messages(USER_A, conversation)) == 2

    def test_delete_is_hard_not_soft(self, conversation):
        """Distinguishes user-initiated delete from the retention job's
        soft-delete: the row must be genuinely gone, not just deleted_at-flagged
        (soft_delete_expired_chat_messages's own WHERE clause would otherwise
        still find it)."""
        delete_conversation(USER_A, conversation)
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM pipeline_chat_messages WHERE conversation_id = %s", (conversation,))
            assert cur.fetchone()["n"] == 0


class TestRetention:
    def test_soft_delete_expired_chat_messages_only_affects_old_rows(self, conversation, db_available):
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        # A freshly-created conversation is nowhere near 90 days old -- a
        # 90-day retention sweep must leave it untouched.
        touched = soft_delete_expired_chat_messages(older_than_days=90)
        assert conversation not in touched
        assert len(get_messages(USER_A, conversation)) == 2

    def test_soft_delete_returns_distinct_conversation_ids(self, db_available):
        """Regression guard: RETURNING DISTINCT is not valid Postgres syntax --
        this pins that the function dedupes in Python instead (one UPDATE
        touches both rows of a conversation, so the raw RETURNING would
        otherwise report the same conversation_id twice)."""
        if not db_available:
            pytest.skip("No reachable Postgres DB for this test run.")
        conversation_id = new_conversation_id()
        append_turns(USER_A, conversation_id, "PYTEST_DOC", "Q", "A")
        try:
            from modes.trial_balance.pipeline.db import db_cursor

            # Backdate both rows past the retention window directly (no public
            # helper creates an already-old row).
            with db_cursor(dict_rows=False) as cur:
                cur.execute(
                    "UPDATE pipeline_chat_messages SET created_at = now() - interval '91 days' "
                    "WHERE conversation_id = %s",
                    (conversation_id,),
                )
            touched = soft_delete_expired_chat_messages(older_than_days=90)
            assert touched.count(conversation_id) == 1
        finally:
            _cleanup(conversation_id)
