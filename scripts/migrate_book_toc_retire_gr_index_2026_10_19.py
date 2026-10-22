#!/usr/bin/env python3
"""
2026-10-19: one-book tables -> multi-book tables.

1. graduale_toc -> book_toc (sql/make-book-toc.sql): PK (book, idx), lowercase columns,
   printed_page VARCHAR(16) with a real FK to books(book, printed_page_num). All 156 GRADUALE
   rows are copied verbatim; graduale_toc is then renamed graduale_toc_bak_2026_10_19.
2. gr_index_entry is retired in favour of chant_indices (book='GRADUALE'), which has held the same
   842 entries since 2026-10-08 and is the curated copy (the API edits it). The script refuses to
   proceed unless every gr_index_entry row has its chant_indices twin with the same section_type /
   mode / incipit / page. Link differences are expected (chant_indices wins; they are listed).
   gr_index_entry is renamed gr_index_entry_bak_2026_10_19: its index-page geometry (page_idx,
   col, bbox_*) is kept locally, and scripts/sync_to_do.py skips *_bak_* tables.

Idempotent: each step checks whether it has already been done.

Usage:
    python scripts/migrate_book_toc_retire_gr_index_2026_10_19.py [--dry-run]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DDL = os.path.join(HERE, '..', 'sql', 'make-book-toc.sql')
TOC_BAK = 'graduale_toc_bak_2026_10_19'
GIE_BAK = 'gr_index_entry_bak_2026_10_19'

COPY_TOC = """
INSERT INTO book_toc (book, idx, level, text, printed_page, y)
SELECT BOOK, IDX, LEVEL, TEXT, CAST(PRINTED_PAGE AS CHAR), Y FROM graduale_toc
WHERE NOT EXISTS (SELECT 1 FROM book_toc b WHERE b.book = graduale_toc.BOOK AND b.idx = graduale_toc.IDX)
"""

TOC_MISMATCH = """
SELECT COUNT(*) FROM graduale_toc g
LEFT JOIN book_toc b ON b.book = g.BOOK AND b.idx = g.IDX AND b.level = g.LEVEL AND b.text = g.TEXT
     AND b.printed_page = CAST(g.PRINTED_PAGE AS CHAR) AND b.y <=> g.Y
WHERE b.book IS NULL
"""

GIE_UNPAIRED = """
SELECT COUNT(*) FROM gr_index_entry g
LEFT JOIN chant_indices c ON c.book = 'GRADUALE' AND c.id = g.id AND c.page = CAST(g.page AS CHAR)
     AND c.section_type <=> g.section_type AND c.mode <=> g.mode AND c.incipit <=> g.incipit
WHERE c.row_id IS NULL
"""

GIE_LINK_DIFFS = """
SELECT g.row_id, c.row_id, g.page, g.incipit, g.gregobase_id, g.match_status, c.chant_uuid, c.match_status
FROM gr_index_entry g
JOIN chant_indices c ON c.book = 'GRADUALE' AND c.id = g.id AND c.page = CAST(g.page AS CHAR)
WHERE NOT (CONCAT('gregobase:', g.gregobase_id) <=> c.chant_uuid AND g.match_status <=> c.match_status)
ORDER BY g.row_id
"""


def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    return create_engine(f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}', future=True)


def exists(conn, table):
    from sqlalchemy import text
    return conn.execute(text("SELECT COUNT(*) FROM information_schema.TABLES "
                             "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"), {'t': table}).scalar() > 0


def main():
    from sqlalchemy import text
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='check and report; change nothing')
    a = ap.parse_args()
    eng = get_engine()

    with eng.connect() as c:
        # ---- 1. book_toc ------------------------------------------------------------------
        if exists(c, 'graduale_toc'):
            n_src = c.execute(text('SELECT COUNT(*) FROM graduale_toc')).scalar()
            if a.dry_run:
                bad = c.execute(text(
                    "SELECT COUNT(*) FROM graduale_toc g LEFT JOIN books b ON b.book = g.BOOK "
                    "AND b.printed_page_num = CAST(g.PRINTED_PAGE AS CHAR) WHERE b.book IS NULL")).scalar()
                print(f'[toc]  graduale_toc: {n_src} rows, {bad} with no books page '
                      f'(must be 0); would create book_toc, copy, rename to {TOC_BAK}')
            else:
                with open(DDL, encoding='utf-8') as f:
                    c.exec_driver_sql(f.read())          # DDL commits implicitly
                n = c.execute(text(COPY_TOC)).rowcount
                c.commit()
                miss = c.execute(text(TOC_MISMATCH)).scalar()
                if miss:
                    sys.exit(f'ERROR: {miss} graduale_toc rows did not copy verbatim; graduale_toc left in place')
                print(f'[toc]  book_toc: copied {n} of {n_src} rows, all verbatim')
                c.exec_driver_sql(f'RENAME TABLE `graduale_toc` TO `{TOC_BAK}`')
                print(f'[toc]  graduale_toc -> {TOC_BAK}')
        else:
            print(f'[toc]  graduale_toc already retired ({TOC_BAK} exists: {exists(c, TOC_BAK)})')

        # ---- 2. gr_index_entry ------------------------------------------------------------
        if exists(c, 'gr_index_entry'):
            unpaired = c.execute(text(GIE_UNPAIRED)).scalar()
            total = c.execute(text('SELECT COUNT(*) FROM gr_index_entry')).scalar()
            ci = c.execute(text("SELECT COUNT(*) FROM chant_indices WHERE book='GRADUALE'")).scalar()
            print(f'[gie]  gr_index_entry {total} rows, chant_indices GRADUALE {ci}, '
                  f'{unpaired} without an identical chant_indices twin (must be 0)')
            diffs = c.execute(text(GIE_LINK_DIFFS)).all()
            print(f'[gie]  {len(diffs)} link differences (chant_indices is the curated copy and wins):')
            for d in diffs:
                print(f'         gie {d[0]} / ci {d[1]} p{d[2]} {d[3]!r}: '
                      f'gregobase:{d[4]} {d[5]}  ->  {d[6]} {d[7]}')
            if unpaired or total != ci:
                sys.exit('ERROR: the tables disagree on entries; gr_index_entry left in place')
            if not a.dry_run:
                c.exec_driver_sql(f'RENAME TABLE `gr_index_entry` TO `{GIE_BAK}`')
                print(f'[gie]  gr_index_entry -> {GIE_BAK}')
        else:
            print(f'[gie]  gr_index_entry already retired ({GIE_BAK} exists: {exists(c, GIE_BAK)})')

    if a.dry_run:
        print('[dry-run] nothing changed')


if __name__ == '__main__':
    main()
