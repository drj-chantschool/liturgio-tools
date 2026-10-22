-- book_toc: the printed table of contents / heading hierarchy of any chant book, one row per heading.
--
-- Replaces graduale_toc (2026-10-19), which could hold only one book: its PK was IDX alone and
-- PRINTED_PAGE was a SMALLINT (no roman front matter, no FK to the page image). Also supersedes the
-- never-created `tocs` table of make-tocs.sql (pdf page numbers, no ordering).
--
--   idx           order of the heading within its book (0-based, gaps allowed); PK is (book, idx)
--   level         nesting depth as the book prints it (0 = the book itself, 1 = chapter, ...)
--   text          heading as printed (may carry OCR noise for GRADUALE; consumers match tolerantly)
--   printed_page  printed page label where the heading sits = books.printed_page_num (VARCHAR:
--                 use CAST(printed_page AS UNSIGNED) for numeric ranges; '62' BETWEEN '619' AND '626')
--   y             heading's vertical position on that page in PDF points, when known (NULL otherwise)
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Backticks only, so it runs unchanged under
-- sql_mode=ANSI (DigitalOcean).

CREATE TABLE IF NOT EXISTS `book_toc` (
  `book`          VARCHAR(40)       NOT NULL COMMENT 'books.book / p_sources.source_code',
  `idx`           SMALLINT UNSIGNED NOT NULL COMMENT 'Heading order within the book',
  `level`         TINYINT UNSIGNED  NOT NULL COMMENT 'Nesting depth (0 = book, 1 = chapter, ...)',
  `text`          VARCHAR(255)      NOT NULL COMMENT 'Heading as printed',
  `printed_page`  VARCHAR(16)       NOT NULL COMMENT 'Printed page label (books.printed_page_num)',
  `y`             FLOAT             DEFAULT NULL COMMENT 'Heading y on the page, PDF points; NULL if unknown',
  PRIMARY KEY (`book`, `idx`),
  KEY `idx_bt_book_page` (`book`, `printed_page`),
  CONSTRAINT `fk_bt_source` FOREIGN KEY (`book`)
    REFERENCES `p_sources` (`source_code`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_bt_book_page` FOREIGN KEY (`book`, `printed_page`)
    REFERENCES `books` (`book`, `printed_page_num`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Printed tables of contents of chant books (GRADUALE, LIBER_HYMNARIUS, ...)';
