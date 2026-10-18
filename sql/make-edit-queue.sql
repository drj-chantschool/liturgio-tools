-- ============================================================
-- liturgio-api write path: the review queue (liturgio-api issue #2, part of #1)
--
-- The prod app never writes the real tables. It records *proposed*
-- edits here, and the owner reviews and applies them to local (the
-- master) with scripts/apply_queue.py, as jcost. Local edits made
-- through the local (read-write) app bypass this table entirely.
--
-- Like app_user, edit_queue lives only on DO: scripts/sync_to_do.py
-- never dumps, swaps or drops it, so a refresh keeps pending edits
-- and their history. The local copy exists for schema parity and
-- testing and is normally empty. Run this file on both, and on DO
-- create the queue user as doadmin (password generated per run):
--
--   CREATE USER 'liturgio_queue'@'%' IDENTIFIED BY '<generated>';
--   GRANT INSERT, SELECT ON liturgio.edit_queue TO 'liturgio_queue'@'%';
--
-- No UPDATE/DELETE for liturgio_queue: a hijacked app or account can
-- add rows but can't rewrite or erase history. Status changes come
-- from the admin-IP-bound liturgio_sync user (issue #5).
-- ============================================================

CREATE TABLE IF NOT EXISTS edit_queue (
    edit_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    submitted_by  INT UNSIGNED    NOT NULL,     -- app_user.user_id (DO's user set)
    submitted_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_table  VARCHAR(64)     NOT NULL,     -- checked against an allowlist on submit AND on apply
    target_pk     JSON            NOT NULL,     -- {"col": value, ...}
    -- values of the edited columns when the edit was made; apply only if
    -- local still has them (optimistic concurrency), else status='conflict'
    base_values   JSON            NOT NULL,
    new_values    JSON            NOT NULL,     -- same keys as base_values
    status        ENUM('pending','applied','rejected','conflict') NOT NULL DEFAULT 'pending',
    resolved_at   DATETIME        NULL,
    note          TEXT            NULL,         -- reviewer's note (reason for reject/conflict)
    KEY ix_edit_queue_status (status, submitted_at),
    CONSTRAINT fk_edit_queue_user FOREIGN KEY (submitted_by) REFERENCES app_user (user_id)
);
