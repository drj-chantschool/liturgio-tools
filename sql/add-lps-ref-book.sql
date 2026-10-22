-- 2026-10-18: lit_part_sources.ref_book (intake_queue #3/#4, user ruling).
--
-- A cross-reference row can cite a page in a DIFFERENT book from the one that assigns it. Example: the Liber
-- Hymnarius p494 prints "Vel R. Emendemus, in Graduale." for the Lenten Office of Readings responsory. The row's
-- `book` (and assignment authority) stays LIBER_HYMNARIUS, and the cited engraving is GRADUALE p66.
--
--   ref_book NULL  -> ref_page_num is a page of `book` (all rows before this change)
--   ref_book set   -> ref_page_num is a page of `ref_book`
--
-- Tools that show the cited page image should join books on COALESCE(ref_book, book).
-- Idempotent: run it with a guard, or check INFORMATION_SCHEMA first.

ALTER TABLE lit_part_sources
  ADD COLUMN ref_book VARCHAR(40) NULL
    COMMENT 'Book of ref_page_num when it differs from book (cross-book cue); NULL = same book'
    AFTER ref_page_num;
