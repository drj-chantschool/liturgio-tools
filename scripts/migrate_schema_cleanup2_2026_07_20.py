#!/usr/bin/env python3
"""
Schema cleanup migration, part 2 — 2026-07-20.

Depends on migrate_schema_cleanup_2026_07_20.py having run first (dedupe must be
complete — 0 duplicate slots — or the unique index add will fail).

Steps:

  A2e Add a NULL-safe UNIQUE constraint on the lit_part_sources "slot".
      MySQL UNIQUE indexes treat NULL as distinct, so a plain unique index over
      the nullable slot columns would not actually prevent duplicates. We add
      STORED generated sentinel columns that coalesce each nullable slot key to
      a non-NULL sentinel, then a unique index over them.

      Slot identity = (jurisdiction, lit_epoch_slug, part_id, book,
                       assignment_authority_code, cycle_sun, cycle_wkday, wkday,
                       wknum_mod_4, wknum_mod_2, translation_source_code,
                       option_num, chant_uuid)

  A3  Repair the books page-provenance FK: change
      lit_part_sources.printed_page_num from INT to VARCHAR(16) to match
      books.printed_page_num, then add the composite FK
      (book, printed_page_num) -> books(book, printed_page_num).

  A5  Attribute the psalms table: it is Jerome's Psalterium iuxta Hebraeos
      (signatures: Ps 22[23] "Dominus pascit me" not Gallican "regit me";
      Ps 2 "quare turbabuntur gentes" not "fremuerunt"). Add a table comment
      and a `version` column defaulting to 'iuxta_hebraeos' so a second psalter
      edition can coexist later.

Usage:
    python scripts/migrate_schema_cleanup2_2026_07_20.py [--dry-run]
"""

import argparse
import sys


def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine, text
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    engine = create_engine(
        f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}',
        future=True,
    )
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


def col_exists(conn, table, col):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': col}).scalar() > 0


def index_exists(conn, table, index):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND INDEX_NAME=:i"
    ), {'t': table, 'i': index}).scalar() > 0


def fk_exists(conn, table, constraint):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND CONSTRAINT_NAME=:c "
        "AND CONSTRAINT_TYPE='FOREIGN KEY'"
    ), {'t': table, 'c': constraint}).scalar() > 0


def col_type(conn, table, col):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': col}).scalar()


def log(msg):
    print(msg)


def run(dry_run):
    from sqlalchemy import text
    engine = get_engine()

    with engine.begin() as conn:

        # ── A2e: NULL-safe unique slot index ──────────────────────────────────

        log('\n[A2e] Add NULL-safe unique slot index on lit_part_sources')

        # Guard: refuse if duplicates still exist under the intended key.
        dup = conn.execute(text("""
            SELECT COUNT(*) FROM (
              SELECT 1 FROM lit_part_sources
              GROUP BY jurisdiction, lit_epoch_slug, part_id, COALESCE(book,''),
                       COALESCE(assignment_authority_code,''), COALESCE(cycle_sun,255),
                       COALESCE(cycle_wkday,255), COALESCE(wkday,255),
                       COALESCE(wknum_mod_4,255), COALESCE(wknum_mod_2,255),
                       COALESCE(translation_source_code,''), option_num,
                       COALESCE(chant_uuid,'')
              HAVING COUNT(*) > 1
            ) x
        """)).scalar()
        if dup > 0:
            raise RuntimeError(f'{dup} duplicate slots still present — run part-1 dedupe first')
        log(f'  Duplicate-slot check passed (0 remaining)')

        # Generated sentinel columns. VIRTUAL (not STORED): MySQL 8.0 can index
        # virtual columns, and adding a STORED generated column to a table that
        # already has foreign keys fails with error 1215 on 8.0.x.
        gen_cols = [
            ('slot_book',    "COALESCE(`book`, '')"),
            ('slot_auth',    "COALESCE(`assignment_authority_code`, '')"),
            ('slot_csun',    "COALESCE(`cycle_sun`, 255)"),
            ('slot_cwkday',  "COALESCE(`cycle_wkday`, 255)"),
            ('slot_wkday',   "COALESCE(`wkday`, 255)"),
            ('slot_wm4',     "COALESCE(`wknum_mod_4`, 255)"),
            ('slot_wm2',     "COALESCE(`wknum_mod_2`, 255)"),
            ('slot_tsrc',    "COALESCE(`translation_source_code`, '')"),
            ('slot_chant',   "COALESCE(`chant_uuid`, '')"),
        ]
        for name, expr in gen_cols:
            if col_exists(conn, 'lit_part_sources', name):
                log(f'  [skip] generated column {name} exists')
                continue
            if not dry_run:
                conn.execute(text(
                    f"ALTER TABLE lit_part_sources "
                    f"ADD COLUMN {name} VARCHAR(64) "
                    f"GENERATED ALWAYS AS ({expr}) VIRTUAL"
                ))
            log(f'  Added generated column {name}' + (' [dry-run]' if dry_run else ''))

        if index_exists(conn, 'lit_part_sources', 'uq_lps_slot'):
            log('  [skip] uq_lps_slot already present')
        else:
            if not dry_run:
                conn.execute(text("""
                    ALTER TABLE lit_part_sources
                    ADD UNIQUE KEY uq_lps_slot (
                        jurisdiction, lit_epoch_slug, part_id, slot_book, slot_auth,
                        slot_csun, slot_cwkday, slot_wkday, slot_wm4, slot_wm2,
                        slot_tsrc, option_num, slot_chant
                    )
                """))
            log('  Added unique key uq_lps_slot' + (' [dry-run]' if dry_run else ''))

        # ── A3: books page-provenance FK ──────────────────────────────────────

        log('\n[A3] Repair books page-provenance FK on lit_part_sources')
        ctype = col_type(conn, 'lit_part_sources', 'printed_page_num')
        log(f'  Current lit_part_sources.printed_page_num type: {ctype}')
        if ctype and ctype.lower().startswith('int'):
            # Orphan check under varchar semantics
            orphans = conn.execute(text("""
                SELECT COUNT(*) FROM lit_part_sources lps
                LEFT JOIN books b
                  ON b.book = lps.book
                 AND b.printed_page_num = CAST(lps.printed_page_num AS CHAR)
                WHERE lps.book IS NOT NULL AND lps.printed_page_num IS NOT NULL
                  AND b.book IS NULL
            """)).scalar()
            if orphans > 0:
                raise RuntimeError(f'{orphans} lps rows have book+page not in books — aborting')
            if not dry_run:
                conn.execute(text(
                    "ALTER TABLE lit_part_sources "
                    "MODIFY COLUMN printed_page_num VARCHAR(16) "
                    "COMMENT 'printed page number on the physical page (matches books.printed_page_num, VARCHAR for roman-numeral pages)'"
                ))
            log('  Changed printed_page_num INT -> VARCHAR(16)' + (' [dry-run]' if dry_run else ''))
        else:
            log('  [skip] printed_page_num already non-INT')

        if fk_exists(conn, 'lit_part_sources', 'fk_lps_book'):
            log('  [skip] fk_lps_book FK constraint already present')
        else:
            # fk_lps_book currently exists only as an INDEX; adding the constraint
            # of the same name reuses/covers it.
            if not dry_run:
                conn.execute(text(
                    "ALTER TABLE lit_part_sources "
                    "ADD CONSTRAINT fk_lps_book "
                    "FOREIGN KEY (book, printed_page_num) "
                    "REFERENCES books(book, printed_page_num) "
                    "ON UPDATE CASCADE ON DELETE RESTRICT"
                ))
            log('  Added composite FK fk_lps_book -> books(book, printed_page_num)' + (' [dry-run]' if dry_run else ''))

        # ── A5: psalms attribution ────────────────────────────────────────────

        log('\n[A5] Attribute psalms table (Psalterium iuxta Hebraeos)')
        if not dry_run:
            conn.execute(text(
                "ALTER TABLE psalms COMMENT = "
                "'Latin Psalter: Jerome Psalterium iuxta Hebraeos (Hebrew Psalter). "
                "Distinct from vulgclementine_verses (Gallican/Clementine).'"
            ))
        log('  Set table comment' + (' [dry-run]' if dry_run else ''))
        if col_exists(conn, 'psalms', 'version'):
            log('  [skip] psalms.version already exists')
        else:
            if not dry_run:
                conn.execute(text(
                    "ALTER TABLE psalms "
                    "ADD COLUMN version VARCHAR(40) NOT NULL DEFAULT 'iuxta_hebraeos' "
                    "COMMENT 'psalter edition; iuxta_hebraeos = Jerome Hebrew Psalter'"
                ))
            log("  Added version column (default 'iuxta_hebraeos')" + (' [dry-run]' if dry_run else ''))

        if dry_run:
            log('\n[dry-run] Rolling back — no changes committed.')
            raise _DryRunRollback()

    log('\n✓ Migration part 2 committed successfully.')


class _DryRunRollback(Exception):
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except _DryRunRollback:
        print('\n[dry-run complete — transaction rolled back]')
    except Exception as e:
        print(f'\nERROR: {e}', file=sys.stderr)
        raise
