-- intake_queue: questions that agentic parsers can't settle alone, answered by a human at /intake,
-- then carried out by another agent. One row = one question (a ruling, a chant-identity call, an
-- index fix, a confirmation). Not to be confused with edit_queue (prod → local data proposals).
--
-- Lifecycle (status):
--   open      raised by a parser agent; waiting for an answer
--   answered  the human wrote `answer` at /intake (they can still edit or clear it, which reopens)
--   claimed   an executing agent took it:
--               UPDATE intake_queue SET status='claimed', claimed_by=:me, claimed_at=NOW()
--               WHERE question_id=:id AND status='answered'      -- rowcount 0 ⇒ someone else has it
--   done      the agent carried out the answer and wrote `resolution` (what it changed, where)
--   withdrawn the raising agent retracted it, or it was folded into another question (see parent_id)
--
-- Writing a question (parser agents, direct INSERT):
--   * title: one line for the list.  prompt: the whole question, self-contained; say what is
--     already loaded/ready and what is waiting on the answer. Proposed answers go in `options`.
--   * Before inserting, look for an open question with the same topic_key; if there is one, append
--     the new page to its `pages` / `blocks` (JSON_ARRAY_APPEND) instead of raising a duplicate.
--   * context must be enough for an agent with no memory of the session to act on the answer:
--     spec files, the contract README section, the log entry, helper scripts.
--
-- JSON column shapes (all arrays; every element an object unless noted):
--   pages    [{"book": "LIBER_HYMNARIUS", "page": "16", "note": "Epiphany invitatory, row 4"}]
--            book/page = books(book, printed_page_num); page is the printed label (a string).
--            Order matters: the first page is the one the question is about.
--   chants   [{"chant_uuid": "gregobase:18100", "note": "melody actually sung (recommended)"}]
--            chant_uuid as in lit_part_sources ('gregobase:<id>' | 'local:<uuid>').
--   options  [{"label": "Link 18100", "detail": "…", "recommended": true}]
--   refs     [{"table": "chant_indices", "pk": {"row_id": 5561}, "note": "links 13372"}]
--            DB rows the question touches (or would change).
--   context  [{"label": "Spec", "path": "propers-parser/lhymn/specs/p0016.json"},
--             {"label": "Artifact", "url": "https://…"}]
--            path = repo-relative (optionally '#heading'); url = absolute.
--   blocks   ["LIBER_HYMNARIUS:16"]  (strings) work units held until this is done, as
--            '<book>:<printed page>' so the executing agent knows what to release.

CREATE TABLE IF NOT EXISTS intake_queue (
  question_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  status           ENUM('open','answered','claimed','done','withdrawn') NOT NULL DEFAULT 'open',
  priority         SMALLINT NOT NULL DEFAULT 0,          -- higher first; e.g. number of pages it releases
  pipeline         VARCHAR(40)  NOT NULL,                -- raising pipeline: 'lh-batch', 'gr-batch', …
  book             VARCHAR(40)  NULL,                    -- main book (books.book), for filtering
  category         VARCHAR(40)  NOT NULL,                -- 'modeling' | 'chant_identity' | 'index_fix'
                                                         -- | 'scope' | 'confirm' | 'structural' | …
  topic_key        VARCHAR(100) NULL,                    -- stable key for a recurring question class
  title            VARCHAR(255) NOT NULL,
  prompt           MEDIUMTEXT   NOT NULL,
  pages            JSON NOT NULL DEFAULT (JSON_ARRAY()),
  chants           JSON NOT NULL DEFAULT (JSON_ARRAY()),
  options          JSON NOT NULL DEFAULT (JSON_ARRAY()),
  refs             JSON NOT NULL DEFAULT (JSON_ARRAY()),
  context          JSON NOT NULL DEFAULT (JSON_ARRAY()),
  blocks           JSON NOT NULL DEFAULT (JSON_ARRAY()),
  parent_id        BIGINT UNSIGNED NULL,                 -- follow-up to / folded into another question
  raised_by        VARCHAR(120) NOT NULL,                -- agent + run, e.g. 'lh-batch director, batch 51'
  raised_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  answer           MEDIUMTEXT NULL,
  is_standing_rule TINYINT(1) NOT NULL DEFAULT 0,        -- answer is a general rule: record it in the
                                                         -- pipeline's contract, not just this case
  answered_by      VARCHAR(64) NULL,                     -- app_user.username (no FK: app_user is DO-only)
  answered_at      DATETIME NULL,
  claimed_by       VARCHAR(120) NULL,
  claimed_at       DATETIME NULL,
  resolution       TEXT NULL,
  resolved_at      DATETIME NULL,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (question_id),
  KEY ix_intake_status (status, priority, raised_at),
  KEY ix_intake_topic (topic_key, status),
  CONSTRAINT fk_intake_parent FOREIGN KEY (parent_id) REFERENCES intake_queue (question_id)
    ON DELETE SET NULL,
  CONSTRAINT ck_intake_arrays CHECK (
    JSON_TYPE(pages) = 'ARRAY' AND JSON_TYPE(chants) = 'ARRAY' AND JSON_TYPE(options) = 'ARRAY'
    AND JSON_TYPE(refs) = 'ARRAY' AND JSON_TYPE(context) = 'ARRAY' AND JSON_TYPE(blocks) = 'ARRAY'),
  CONSTRAINT ck_intake_answered CHECK (status IN ('open','withdrawn') OR answer IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
