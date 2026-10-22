-- chant_indices: the printed indices of multiple chant books, each entry
-- (eventually) resolved to its gregobase chant / chant_uuid.
--
-- Generalizes gr_index_entry (1974 Graduale Romanum only) to any book in
-- `books`. Drops gr_index_entry's index-page OCR geometry (page_idx, col,
-- bbox_*), which locates the entry on the INDEX page and is not needed here.
-- gr_index_entry.gregobase_id is superseded by chant_uuid ('gregobase:<id>'),
-- which can also hold non-gregobase chants ('local:<uuid>').
--
-- `page` is VARCHAR to match books.printed_page_num, so (book, page) can be a
-- real FK to the page image. Beware string comparison: BETWEEN '619' AND '626'
-- matches '62' -- CAST(page AS UNSIGNED) for numeric ranges.
--
-- day_code / hour_code / place_code (2026-10-20): indices that key a chant to a
-- liturgical day + hour + place (OCO) keep those sigla as printed; NULL elsewhere.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Uses backticks only, so it runs
-- unchanged under sql_mode=ANSI (DigitalOcean).

CREATE TABLE IF NOT EXISTS `chant_indices` (
  `row_id`        INT NOT NULL AUTO_INCREMENT,
  `book`          VARCHAR(40) NOT NULL COMMENT 'Book whose index this entry comes from (books.book / p_sources.source_code)',
  `id`            INT NOT NULL COMMENT 'Entry ordinal within this book''s index',
  `section_type`  VARCHAR(64) DEFAULT NULL COMMENT 'Index section heading, e.g. introitus, graduale, alleluia',
  `mode`          TINYINT DEFAULT NULL,
  `incipit`       TEXT,
  `page`          VARCHAR(16) DEFAULT NULL COMMENT 'Printed page in `book` where the chant appears (books.printed_page_num)',
  `day_code`      VARCHAR(40) DEFAULT NULL COMMENT 'Day/feast siglum as printed in the index, e.g. OCO 1A1, 21/2, UnM',
  `hour_code`     VARCHAR(16) DEFAULT NULL COMMENT 'Hour siglum as printed, e.g. OCO 1V, L, Ol, Hm, V, 2V',
  `place_code`    VARCHAR(40) DEFAULT NULL COMMENT 'Place in the hour as printed, e.g. OCO 1, 2, 3, B, Ma, M ad lib',
  `chant_uuid`    VARCHAR(80) DEFAULT NULL COMMENT 'v_chant_item.chant_item_uid, e.g. gregobase:123 or local:<uuid>; no FK (view)',
  `match_status`  ENUM('resolved','ambiguous','unmatched','reviewed','resolved_with_difficulty') DEFAULT NULL,
  PRIMARY KEY (`row_id`),
  UNIQUE KEY `uq_ci_book_id_page` (`book`, `id`, `page`),
  KEY `idx_ci_book_page` (`book`, `page`),
  KEY `idx_ci_chant_uuid` (`chant_uuid`),
  CONSTRAINT `fk_ci_source` FOREIGN KEY (`book`)
    REFERENCES `p_sources` (`source_code`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ci_book_page` FOREIGN KEY (`book`, `page`)
    REFERENCES `books` (`book`, `printed_page_num`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Printed indices of chant books (GR, GM, ...), resolved to gregobase / chant_uuid';
