#!/usr/bin/env python3
"""
Roadmap step 5 migration: evolve lit_part_texts into lit_part_sources.

Idempotent. Applies the live-DB changes that correspond to the SQL source
files under liturgio-tools/sql/:
    - snapshot lit_part_texts -> lit_part_texts_backup_step5 (once)
    - RENAME lit_part_texts -> lit_part_sources
    - ADD status flag (draft/reviewed/published vocabulary, matching local_chants)
    - ADD provenance columns (book, pdf_page_num, bbox) + composite FK to books
    - CREATE books, lit_text_chant_link, tocs

Usage:
    python scripts/migrate_step5_lit_part_sources.py [--dry-run]
"""

import sys
import argparse

import keyring
from sqlalchemy import create_engine, text


def get_engine(user='jcost', db_name='liturgio'):
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
    engine = create_engine(conn_str, future=True)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


SCHEMA = 'liturgio'


def table_exists(conn, name):
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:n"
    ), {'s': SCHEMA, 'n': name}).scalar() > 0


def column_exists(conn, table, col):
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'s': SCHEMA, 't': table, 'c': col}).scalar() > 0


def constraint_exists(conn, table, name):
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t AND CONSTRAINT_NAME=:c"
    ), {'s': SCHEMA, 't': table, 'c': name}).scalar() > 0


CREATE_BOOKS = """
CREATE TABLE IF NOT EXISTS books (
    book             VARCHAR(40)  NOT NULL,
    pdf_page_num     INT          NOT NULL,
    printed_page_num VARCHAR(16)  NULL,
    image_path       VARCHAR(512) NULL,
    image_blob       LONGBLOB     NULL,
    notes            VARCHAR(500) NULL,
    created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (book, pdf_page_num)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_LINK = """
CREATE TABLE IF NOT EXISTS lit_text_chant_link (
    text_id        BIGINT UNSIGNED NOT NULL,
    chant_item_uid VARCHAR(80)     NOT NULL,
    notes          VARCHAR(500)    NULL,
    created_at     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (text_id, chant_item_uid),
    KEY idx_ltcl_chant (chant_item_uid),
    CONSTRAINT fk_ltcl_text FOREIGN KEY (text_id)
        REFERENCES lit_part_sources(text_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_TOCS = """
CREATE TABLE IF NOT EXISTS tocs (
    toc_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    book         VARCHAR(40) NOT NULL,
    pdf_page_num INT NULL,
    hdr_txt      VARCHAR(500) NOT NULL,
    lvl          TINYINT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_tocs_book (book)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def run(conn, dry_run):
    def do(label, sql, params=None):
        print(f'  [run]  {label}')
        if dry_run:
            print(f'         DRY RUN: {sql.strip().splitlines()[0]} ...')
        else:
            conn.execute(text(sql), params or {})

    # 1. Snapshot (once)
    if table_exists(conn, 'lit_part_texts_backup_step5'):
        print('  [skip] backup lit_part_texts_backup_step5 — already exists')
    elif table_exists(conn, 'lit_part_texts'):
        do('snapshot lit_part_texts -> lit_part_texts_backup_step5',
           'CREATE TABLE lit_part_texts_backup_step5 AS SELECT * FROM lit_part_texts')
    else:
        print('  [skip] backup — source lit_part_texts not present (already renamed?)')

    # 2. Rename
    if table_exists(conn, 'lit_part_sources'):
        print('  [skip] rename — lit_part_sources already exists')
    elif table_exists(conn, 'lit_part_texts'):
        do('RENAME lit_part_texts -> lit_part_sources',
           'RENAME TABLE lit_part_texts TO lit_part_sources')
    else:
        sys.exit('ERROR: neither lit_part_texts nor lit_part_sources exists.')

    # 3. status flag
    if column_exists(conn, 'lit_part_sources', 'status'):
        print('  [skip] add status — already present')
    else:
        do('ADD COLUMN status (draft/reviewed/published)',
           "ALTER TABLE lit_part_sources "
           "ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft' "
           "COMMENT 'review status: draft/reviewed/published (matches local_chants.status)'")

    # 4. provenance columns + composite FK
    if column_exists(conn, 'lit_part_sources', 'book'):
        print('  [skip] add book — already present')
    else:
        do('ADD COLUMN book (provenance; reference_assets folded in here)',
           "ALTER TABLE lit_part_sources "
           "ADD COLUMN book VARCHAR(40) NULL "
           "COMMENT 'source page provenance; extracted text+translation in "
           "original_text/vernacular_text replace the dropped reference_assets table'")

    if column_exists(conn, 'lit_part_sources', 'pdf_page_num'):
        print('  [skip] add pdf_page_num — already present')
    else:
        do('ADD COLUMN pdf_page_num',
           'ALTER TABLE lit_part_sources ADD COLUMN pdf_page_num INT NULL')

    if column_exists(conn, 'lit_part_sources', 'bbox'):
        print('  [skip] add bbox — already present')
    else:
        do('ADD COLUMN bbox',
           "ALTER TABLE lit_part_sources ADD COLUMN bbox VARCHAR(64) NULL "
           "COMMENT 'bounding box or start-y of content on page, e.g. x,y,w,h'")

    # books must exist before the FK can be created
    do('CREATE TABLE books', CREATE_BOOKS)

    if constraint_exists(conn, 'lit_part_sources', 'fk_lps_book'):
        print('  [skip] add fk_lps_book — already present')
    else:
        do('ADD CONSTRAINT fk_lps_book (composite FK -> books)',
           'ALTER TABLE lit_part_sources '
           'ADD CONSTRAINT fk_lps_book FOREIGN KEY (book, pdf_page_num) '
           'REFERENCES books(book, pdf_page_num)')

    # 5. link table (note: chant_item_uid -> v_chant_item view, no real FK possible)
    do('CREATE TABLE lit_text_chant_link', CREATE_LINK)

    # 6. tocs
    do('CREATE TABLE tocs', CREATE_TOCS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine('jcost')
    print(f'Step 5 migration ({"DRY RUN" if args.dry_run else "APPLY"}):')
    with engine.begin() as conn:
        run(conn, args.dry_run)
    print('Done.')


if __name__ == '__main__':
    main()
