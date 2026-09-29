#!/usr/bin/env python3
"""
Roadmap step 7d: add chant_uuid to lit_part_sources, drop lit_text_chant_link.

Replaces the empty lit_text_chant_link table with a single nullable column on
lit_part_sources that holds v_chant_item.chant_item_uid values (e.g.
'gregobase:123' or 'local:<uuid>'). Semantics: one chant per source text;
no FK (v_chant_item is a view, not FK-able).

Steps (each idempotent / guarded):
  1. ADD COLUMN chant_uuid VARCHAR(80) NULL on lit_part_sources (if missing).
  2. ADD INDEX idx_lps_chant_uuid on lit_part_sources(chant_uuid) (if missing).
  3. Safety check lit_text_chant_link: abort if non-empty or if any FK
     references it.
  4. DROP TABLE lit_text_chant_link (if it still exists and passed safety).
  5. Print final summary.

Usage:
    python scripts/migrate_step7d_chant_uuid_on_lit_part_sources.py [--dry-run]
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


def table_exists(conn, table):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t"
    ), {'t': table}).scalar()
    return row > 0


def index_exists(conn, table, index_name):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND INDEX_NAME=:i"
    ), {'t': table, 'i': index_name}).scalar()
    return row > 0


# ---------------------------------------------------------------------------
# Step 1: ADD COLUMN chant_uuid
# ---------------------------------------------------------------------------

def add_column_if_needed(conn, dry_run):
    from sqlalchemy import text
    if column_exists(conn, 'lit_part_sources', 'chant_uuid'):
        print('[skip] chant_uuid column already exists on lit_part_sources')
        return
    print('[run]  ADD COLUMN chant_uuid VARCHAR(80) NULL on lit_part_sources')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD COLUMN chant_uuid VARCHAR(80) NULL "
            "COMMENT 'v_chant_item.chant_item_uid (e.g. gregobase:N or local:<uuid>); "
            "optional single chant rendering of this text. Replaces lit_text_chant_link.'"
        ))
        print('[done] chant_uuid column added')


# ---------------------------------------------------------------------------
# Step 2: ADD INDEX idx_lps_chant_uuid
# ---------------------------------------------------------------------------

def add_index_if_needed(conn, dry_run):
    from sqlalchemy import text
    if index_exists(conn, 'lit_part_sources', 'idx_lps_chant_uuid'):
        print('[skip] index idx_lps_chant_uuid already exists on lit_part_sources')
        return
    print('[run]  ADD INDEX idx_lps_chant_uuid (chant_uuid) on lit_part_sources')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD INDEX idx_lps_chant_uuid (chant_uuid)"
        ))
        print('[done] index idx_lps_chant_uuid added')


# ---------------------------------------------------------------------------
# Step 3 + 4: Safety-check then drop lit_text_chant_link
# ---------------------------------------------------------------------------

def drop_link_table_if_safe(conn, dry_run):
    from sqlalchemy import text

    if not table_exists(conn, 'lit_text_chant_link'):
        print('[skip] lit_text_chant_link does not exist (already dropped)')
        return

    # Safety check 1: row count must be 0
    row_count = conn.execute(
        text('SELECT COUNT(*) FROM lit_text_chant_link')
    ).scalar()
    if row_count != 0:
        sys.exit(
            f'ABORT: lit_text_chant_link has {row_count} rows — refusing to drop a '
            'populated table. Migrate the data to lit_part_sources.chant_uuid first.'
        )
    print(f'[ok]   lit_text_chant_link row count = {row_count} (safe to drop)')

    # Safety check 2: no inbound FKs referencing lit_text_chant_link
    fk_refs = conn.execute(text(
        "SELECT TABLE_NAME, CONSTRAINT_NAME "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = 'liturgio' "
        "  AND REFERENCED_TABLE_NAME = 'lit_text_chant_link'"
    )).fetchall()
    if fk_refs:
        details = ', '.join(f'{r[0]}.{r[1]}' for r in fk_refs)
        sys.exit(
            f'ABORT: inbound FK references to lit_text_chant_link found: {details}. '
            'Remove those FKs before dropping the table.'
        )
    print('[ok]   no inbound FK references to lit_text_chant_link')

    # Drop
    print('[run]  DROP TABLE lit_text_chant_link')
    if not dry_run:
        conn.execute(text('DROP TABLE lit_text_chant_link'))
        print('[done] lit_text_chant_link dropped')
    else:
        print('[dry]  (skipped — dry run)')


# ---------------------------------------------------------------------------
# Step 5: Final summary
# ---------------------------------------------------------------------------

def print_summary(conn):
    from sqlalchemy import text
    has_col   = column_exists(conn, 'lit_part_sources', 'chant_uuid')
    has_idx   = index_exists(conn, 'lit_part_sources', 'idx_lps_chant_uuid')
    link_gone = not table_exists(conn, 'lit_text_chant_link')

    print('\n=== Summary ===')
    print(f'  lit_part_sources.chant_uuid column : {"EXISTS" if has_col else "MISSING"}')
    print(f'  idx_lps_chant_uuid index           : {"EXISTS" if has_idx else "MISSING"}')
    print(f'  lit_text_chant_link table          : {"GONE (dropped)" if link_gone else "STILL EXISTS"}')

    if has_col and has_idx and link_gone:
        print('\n[ok]   Migration step 7d complete.')
    else:
        print('\n[warn] One or more steps did not finish — re-run without --dry-run.')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(
        description='Step 7d: add chant_uuid to lit_part_sources, drop lit_text_chant_link.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making any changes.')
    args = parser.parse_args()

    print('=== migrate_step7d: chant_uuid on lit_part_sources / drop lit_text_chant_link ===\n')
    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')

    engine = get_engine()

    if args.dry_run:
        with engine.connect() as conn:
            add_column_if_needed(conn, dry_run=True)
            add_index_if_needed(conn, dry_run=True)
            drop_link_table_if_safe(conn, dry_run=True)
            print_summary(conn)
    else:
        # Each DDL step in its own transaction so partial progress is visible
        with engine.begin() as conn:
            add_column_if_needed(conn, dry_run=False)
        with engine.begin() as conn:
            add_index_if_needed(conn, dry_run=False)
        with engine.begin() as conn:
            drop_link_table_if_safe(conn, dry_run=False)
        with engine.connect() as conn:
            print_summary(conn)


if __name__ == '__main__':
    main()
