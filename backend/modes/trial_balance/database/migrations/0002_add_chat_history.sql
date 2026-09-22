-- Trial Balance: durable, 90-day-retained chat history for /ask and /ask-general.
--
-- Today conversation memory lives ONLY in Valkey (agentchat:{user_id}:{session_id},
-- 2-hour TTL, see pipeline/agent_memory.py) -- nothing in Postgres records that a
-- conversation ever happened. This doesn't match the 90-day audit-evidence
-- retention window the rest of TB's audit trail already follows
-- (pipeline_sessions/pipeline_findings via soft_delete_expired_sessions()).
--
-- Mirrors the shape of the platform's own artha_fs_messages table (Financial
-- Statement's chat history, backend/app/auth/schema.py) -- one append-only
-- table, "a conversation" is a GROUP BY over conversation_id, user_id is in the
-- WHERE clause of every read/delete, never checked afterward. Lives in TB's OWN
-- Postgres (not the platform DB) specifically so it can reuse the
-- soft_delete_expired_sessions() retention pattern already built and proven
-- here -- the platform's own artha_fs_messages has NO retention/expiry
-- mechanism today (confirmed), so mirroring it exactly would mean building that
-- machinery from scratch instead of reusing what already works.
--
-- No FK to anything -- TB's own Postgres has no artha_users table to reference
-- (different Postgres instance from the platform DB), the same trade-off
-- pipeline_sessions.user_id/live_document_table.user_id already accepted.
--
-- deleted_at is for the RETENTION JOB's soft-delete only (soft_delete_expired_
-- chat_messages, mirroring soft_delete_expired_sessions). A user's own
-- DELETE /conversations/{id} is a hard DELETE instead (see db.py's
-- delete_conversation) -- a user removing their own conversation should
-- genuinely remove it, not just hide it from themselves.
--
-- Idempotent -- safe to apply more than once.

CREATE TABLE IF NOT EXISTS pipeline_chat_messages (
    message_id      UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    user_id         UUID NOT NULL,
    tb_doc_id       TEXT,              -- which document this conversation is about (/ask only; NULL for /ask-general)
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    payload         JSONB,             -- full tool response for assistant turns, so a reopened conversation renders richly
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_chat_messages_conv_seq
    ON pipeline_chat_messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_pipeline_chat_messages_user_recent
    ON pipeline_chat_messages(user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pipeline_chat_messages_conv
    ON pipeline_chat_messages(conversation_id, seq) WHERE deleted_at IS NULL;
