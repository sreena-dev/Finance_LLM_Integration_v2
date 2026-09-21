-- Trial Balance: per-user data isolation.
--
-- The gateway grew a real login system (backend/app/auth/) after Trial Balance
-- was migrated from a standalone codebase that had no concept of a logged-in
-- user at all. Every TB route was gated behind sign-in, but none of TB's own
-- Postgres tables recorded WHO created a row, and no route filtered by caller
-- identity -- GET /documents with no query params already disclosed every
-- tb_doc_id in the system to any authenticated user, and DELETE /documents/{id}
-- would delete any user's LIVE document given that string.
--
-- Scope, deliberately not "every table":
--   * live_document_table / pipeline_sessions get a user_id column -- these are
--     the two tables TB's OWN code actually creates rows in.
--   * live_tb_table (GL lines) gets none -- scoped via its parent
--     live_document_table.tb_doc_id at read/delete time, not duplicated onto
--     every line row.
--   * pipeline_findings / pipeline_artifacts / pipeline_artifact_files get none
--     -- scoped via their parent pipeline_sessions.session_id; router.py checks
--     ownership once at each route's entry point rather than filtering every
--     low-level helper's SQL.
--   * document_table / tb_table (MAIN) get none -- TB's own code never writes
--     these (confirmed read-only; populated by a separate, out-of-scope
--     promotion process this codebase doesn't own), and there is no recorded
--     uploader for existing rows to retrofit. Stays shared-by-design, same as
--     before this migration -- see router.py's list_documents()/get_document()
--     docstrings.
--   * priority_companies gets none -- a cross-engagement autocomplete
--     suggestion list, not per-user data; isolating it would break its purpose.
--
-- Nullable, not NOT NULL: existing rows predate the user concept and have no
-- owner to backfill. The application always sets user_id on new inserts;
-- pre-existing (and future NULL-owner) rows become invisible orphans under the
-- new per-user filters rather than being reassigned to whoever asks first --
-- acceptable for pre-production data, not something to run against a live
-- fleet without a real backfill decision first.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS) -- safe to apply more than once.

ALTER TABLE pipeline_sessions ADD COLUMN IF NOT EXISTS user_id UUID;
CREATE INDEX IF NOT EXISTS idx_pipeline_sessions_user_id ON pipeline_sessions(user_id);

ALTER TABLE live_document_table ADD COLUMN IF NOT EXISTS user_id UUID;
CREATE INDEX IF NOT EXISTS idx_live_document_table_user_id ON live_document_table(user_id);
