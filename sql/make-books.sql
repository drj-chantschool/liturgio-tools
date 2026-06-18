-- books: page-image provenance for liturgical source texts.
-- A row identifies one page within a source PDF (by short book code + page index)
-- and optionally the printed page number, a page image path/blob, and notes.
-- Referenced by lit_part_sources(book, pdf_page_num) via composite FK fk_lps_book.
CREATE TABLE IF NOT EXISTS books (
    book             VARCHAR(40)  NOT NULL,   -- short code, e.g. 'GR', 'GREGORIAN_MISSAL', 'OCO'
    pdf_page_num     INT          NOT NULL,   -- page index within the source PDF
    printed_page_num VARCHAR(16)  NULL,       -- page number printed on the page (may differ)
    image_path       VARCHAR(512) NULL,       -- filesystem/URL reference to the page image
    image_blob       LONGBLOB     NULL,       -- optional inline image
    notes            VARCHAR(500) NULL,
    created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (book, pdf_page_num)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
