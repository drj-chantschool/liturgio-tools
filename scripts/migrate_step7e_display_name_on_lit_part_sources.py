#!/usr/bin/env python3
"""
Roadmap step 7e: add display_name to lit_part_sources.

display_name is a short human-readable label combining:
  - lit_epoch.title (via lit_epoch_slug)
  - service_part.part_code (via part_id)
  - text incipit: v_chant_item.incipit (via chant_uuid), then first 60 chars of
    vernacular_text, then first 60 chars of original_text

Format: "{epoch_title} — {part_code} — {incipit}"
NULL components are omitted via CONCAT_WS.

Steps (each idempotent / guarded):
  1. ADD COLUMN display_name VARCHAR(300) NULL on lit_part_sources.
  2. Populate display_name via UPDATE JOIN across lit_epoch, service_part,
     and v_chant_item.
  3. Print final summary.

Usage:
    python scripts/migrate_step7e_display_name_on_lit_part_sources.py [--dry-run]
"""

import sys
import argparse


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


def column_exists(conn, table, column):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': column}).scalar()
    return row > 0


def add_column_if_needed(conn, dry_run):
    from sqlalchemy import text
    if column_exists(conn, 'lit_part_sources', 'display_name'):
        print('[skip] display_name column already exists on lit_part_sources')
        return
    print('[run]  ADD COLUMN display_name VARCHAR(300) NULL on lit_part_sources')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD COLUMN display_name VARCHAR(300) NULL "
            "COMMENT 'Human-readable label: epoch title, part code, and text incipit'"
        ))
        print('[done] display_name column added')


def populate_display_name(conn, dry_run):
    from sqlalchemy import text

    # Priority for incipit text:
    #   1. v_chant_item.incipit (linked chant's own incipit field)
    #   2. first 60 chars of vernacular_text (English/translated text)
    #   3. first 60 chars of original_text (Latin source text)
    sql = text("""
        UPDATE lit_part_sources lps
        JOIN service_part sp ON sp.part_id = lps.part_id
        LEFT JOIN lit_epoch le ON le.slug = lps.lit_epoch_slug
        LEFT JOIN v_chant_item vci ON vci.chant_item_uid = lps.chant_uuid
        SET lps.display_name = CONCAT_WS(' — ',
            NULLIF(TRIM(COALESCE(le.title, '')), ''),
            sp.part_code,
            NULLIF(TRIM(COALESCE(
                vci.incipit,
                LEFT(lps.vernacular_text, 60),
                LEFT(lps.original_text, 60)
            )), '')
        )
    """)

    if dry_run:
        print('[dry]  would UPDATE lit_part_sources.display_name (skipped)')
        return

    result = conn.execute(sql)
    print(f'[done] display_name populated for {result.rowcount} rows')


def print_summary(conn):
    from sqlalchemy import text
    has_col = column_exists(conn, 'lit_part_sources', 'display_name')
    count = conn.execute(text(
        'SELECT COUNT(*) FROM lit_part_sources WHERE display_name IS NOT NULL'
    )).scalar()
    total = conn.execute(text('SELECT COUNT(*) FROM lit_part_sources')).scalar()

    print('\n=== Summary ===')
    print(f'  lit_part_sources.display_name column : {"EXISTS" if has_col else "MISSING"}')
    print(f'  Rows with display_name populated     : {count} / {total}')

    if has_col and count > 0:
        sample = conn.execute(text(
            'SELECT display_name FROM lit_part_sources '
            'WHERE display_name IS NOT NULL LIMIT 5'
        )).fetchall()
        print('\n  Sample display_name values:')
        for r in sample:
            print(f'    {r[0]}')

    if has_col:
        print('\n[ok]   Migration step 7e complete.')
    else:
        print('\n[warn] Column was not created — re-run without --dry-run.')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(
        description='Step 7e: add display_name to lit_part_sources.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making any changes.')
    args = parser.parse_args()

    print('=== migrate_step7e: display_name on lit_part_sources ===\n')
    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')

    engine = get_engine()

    if args.dry_run:
        with engine.connect() as conn:
            add_column_if_needed(conn, dry_run=True)
            populate_display_name(conn, dry_run=True)
            print_summary(conn)
    else:
        with engine.begin() as conn:
            add_column_if_needed(conn, dry_run=False)
        with engine.begin() as conn:
            populate_display_name(conn, dry_run=False)
        with engine.connect() as conn:
            print_summary(conn)


if __name__ == '__main__':
    main()
