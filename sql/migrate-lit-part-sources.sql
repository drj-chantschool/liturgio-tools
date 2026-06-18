-- ───────────────────────────────────────────────────────────────────────────
-- Roadmap step 5: evolve lit_part_texts into lit_part_sources.
--
-- lit_part_sources is the repository for ALL liturgical texts from ALL sources,
-- with a review-status flag and source-page provenance.
--
-- This file documents the canonical DDL for the migration. The live migration is
-- applied idempotently by scripts/migrate_step5_lit_part_sources.py (which guards
-- every step against INFORMATION_SCHEMA, mirroring load_saints.py). The statements
-- below are the plain-SQL equivalent; run the Python script for repeat-safe execution.
--
-- Prerequisites: make-books.sql (books must exist before fk_lps_book is added).
-- Related new tables: make-lit-text-chant-link.sql, make-tocs.sql.
-- ───────────────────────────────────────────────────────────────────────────

-- 0. Snapshot before the rename (run once).
CREATE TABLE lit_part_texts_backup_step5 AS SELECT * FROM lit_part_texts;

-- 1. Rename the table.
RENAME TABLE lit_part_texts TO lit_part_sources;

-- 2. Review-status flag — same vocabulary as local_chants.status
--    (draft / reviewed / published). Existing rows default to 'draft'.
ALTER TABLE lit_part_sources
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft'
    COMMENT 'review status: draft/reviewed/published (matches local_chants.status)';

-- 3. Source-page provenance columns.
--    NOTE: the dropped 'reference_assets' idea is folded in here: the extracted
--    text + translation already live in original_text / vernacular_text, and the
--    page they came from is identified by (book, pdf_page_num) + bbox.
ALTER TABLE lit_part_sources
    ADD COLUMN book VARCHAR(40) NULL
        COMMENT 'source page provenance; reference_assets folded in here (text in original_text/vernacular_text)',
    ADD COLUMN pdf_page_num INT NULL,
    ADD COLUMN bbox VARCHAR(64) NULL
        COMMENT 'bounding box or start-y of content on page, e.g. x,y,w,h';

-- 4. Composite FK to books. Nullable, so rows without page provenance
--    (e.g. ROMAN_MISSAL text-only entries) remain valid.
ALTER TABLE lit_part_sources
    ADD CONSTRAINT fk_lps_book FOREIGN KEY (book, pdf_page_num)
        REFERENCES books(book, pdf_page_num);

-- ── Roadmap step 6a (2026-06-17): add lit_epoch_slug ─────────────────────
-- Adds the canonical epoch foreign key so texts can be looked up by
-- lit_epoch tree position (matching how lit_part_assignment is keyed).
-- Populated by scripts/migrate_step6a_lit_part_sources_epoch_slug.py.
--
-- Coverage after initial migration:
--   • All PASC + standard OT/ADV/LENT seasonal rows (day + week nodes)
--   • NAT rows with 0-indexed seq matched via wkday-1 fallback (most)
--   • Feast rows with a proper_of_saints entry (~34 of 461 initially)
--   • Unresolved: NAT/DAY Christmas Masses 2&3, ADV/I/4 Sunday (gaps in
--     liturgical_day), and feast rows awaiting GR OCR saint population
--
-- Loaders updated to write lit_epoch_slug on new inserts:
--   load_propers.py, load_lit_part_texts.py, load_saints.py
ALTER TABLE lit_part_sources
    ADD COLUMN lit_epoch_slug VARCHAR(64) NULL
    COMMENT 'FK to lit_epoch; replaces season/subseason/wknum/wkday and month/day_of_month for epoch lookup';

ALTER TABLE lit_part_sources
    ADD CONSTRAINT fk_lps_epoch FOREIGN KEY (lit_epoch_slug)
        REFERENCES lit_epoch(slug)
        ON UPDATE CASCADE ON DELETE RESTRICT;
