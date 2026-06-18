-- tocs: table-of-contents / heading entries extracted from source books.
-- Each row is one header found at a page in a book, with an optional nesting level.
CREATE TABLE IF NOT EXISTS tocs (
    toc_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    book         VARCHAR(40) NOT NULL,
    pdf_page_num INT NULL,
    hdr_txt      VARCHAR(500) NOT NULL,
    lvl          TINYINT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_tocs_book (book)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
