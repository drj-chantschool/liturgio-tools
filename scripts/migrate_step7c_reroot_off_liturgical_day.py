#!/usr/bin/env python3
"""
Migration step 7c: re-root the build off liturgical_day (drop the table).

Phase 1 already rewrote make-lit-epoch.sql and make-proper-of-seasons.sql
so that neither references liturgical_day.  This script performs the
destructive DB changes:

  1. SAFETY BACKUP: CREATE TABLE liturgical_day_backup_step7c AS SELECT * FROM liturgical_day
  2. REPOINT FK on proper_of_seasons: drop fk_pos_lit_day, add fk_pos_lit_epoch
     (references lit_epoch.slug)
  3. DROP back-ref column lit_day_id from lit_epoch (+ index idx_epoch_litday if any)
  4. DROP TABLE liturgical_day (only if no remaining inbound FKs)
  5. FINAL SUMMARY

Idempotent: every step is guarded so re-running is safe.

Usage:
    python scripts/migrate_step7c_reroot_off_liturgical_day.py [--dry-run]
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


def table_exists(conn, table):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t"
    ), {'t': table}).scalar()
    return row > 0


def column_exists(conn, table, column):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': column}).scalar()
    return row > 0


def fk_exists(conn, fk_name):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA='liturgio' AND CONSTRAINT_NAME=:n"
    ), {'n': fk_name}).scalar()
    return row > 0


def index_exists(conn, table, index_name):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND INDEX_NAME=:i"
    ), {'t': table, 'i': index_name}).scalar()
    return row > 0


# ---------------------------------------------------------------------------
# Step 1: Safety backup
# ---------------------------------------------------------------------------

def step1_backup(conn, dry_run):
    """CREATE TABLE liturgical_day_backup_step7c AS SELECT * FROM liturgical_day."""
    from sqlalchemy import text

    print('\n--- Step 1: Safety backup ---')

    if not table_exists(conn, 'liturgical_day'):
        print('[skip] liturgical_day does not exist — backup not needed')
        return

    if table_exists(conn, 'liturgical_day_backup_step7c'):
        row_count = conn.execute(text(
            'SELECT COUNT(*) FROM liturgical_day_backup_step7c'
        )).scalar()
        print(f'[skip] liturgical_day_backup_step7c already exists ({row_count} rows)')
        return

    src_count = conn.execute(text('SELECT COUNT(*) FROM liturgical_day')).scalar()
    print(f'[run]  CREATE TABLE liturgical_day_backup_step7c AS SELECT * FROM liturgical_day'
          f' ({src_count} rows)')
    if not dry_run:
        conn.execute(text(
            'CREATE TABLE liturgical_day_backup_step7c AS SELECT * FROM liturgical_day'
        ))
        backed_up = conn.execute(text(
            'SELECT COUNT(*) FROM liturgical_day_backup_step7c'
        )).scalar()
        print(f'[done] backup created: {backed_up} rows')


# ---------------------------------------------------------------------------
# Step 2: Repoint FK on proper_of_seasons
# ---------------------------------------------------------------------------

def step2_repoint_fk(conn, dry_run):
    """
    Drop fk_pos_lit_day (if exists), then add fk_pos_lit_epoch pointing to
    lit_epoch(slug) (if not exists).  Orphan check before adding.
    """
    from sqlalchemy import text

    print('\n--- Step 2: Repoint FK on proper_of_seasons ---')

    # 2a. Drop old FK
    if fk_exists(conn, 'fk_pos_lit_day'):
        print('[run]  DROP FOREIGN KEY fk_pos_lit_day on proper_of_seasons')
        if not dry_run:
            conn.execute(text(
                'ALTER TABLE proper_of_seasons DROP FOREIGN KEY fk_pos_lit_day'
            ))
            print('[done] fk_pos_lit_day dropped')
    else:
        print('[skip] fk_pos_lit_day does not exist (already dropped)')

    # 2b. Add new FK (fk_pos_lit_epoch)
    if fk_exists(conn, 'fk_pos_lit_epoch'):
        print('[skip] fk_pos_lit_epoch already exists')
        return

    # Orphan check
    orphan_count = conn.execute(text("""
        SELECT COUNT(*) FROM proper_of_seasons pos
        WHERE pos.lit_day_id NOT IN (SELECT slug FROM lit_epoch)
    """)).scalar()
    if orphan_count > 0:
        print(f'[ABORT] {orphan_count} proper_of_seasons rows have lit_day_id not in '
              f'lit_epoch.slug — fix orphans before adding FK')
        sys.exit(1)
    print(f'[ok]   Orphan check: 0 orphaned rows in proper_of_seasons')

    print('[run]  ADD CONSTRAINT fk_pos_lit_epoch FOREIGN KEY (lit_day_id) '
          'REFERENCES lit_epoch(slug) ON UPDATE CASCADE ON DELETE RESTRICT')
    if not dry_run:
        conn.execute(text(
            'ALTER TABLE proper_of_seasons '
            'ADD CONSTRAINT fk_pos_lit_epoch '
            'FOREIGN KEY (lit_day_id) REFERENCES lit_epoch(slug) '
            'ON UPDATE CASCADE ON DELETE RESTRICT'
        ))
        print('[done] fk_pos_lit_epoch added')


# ---------------------------------------------------------------------------
# Step 3: Drop lit_epoch.lit_day_id column (and its index, if any)
# ---------------------------------------------------------------------------

def step3_drop_lit_day_id_column(conn, dry_run):
    """Drop idx_epoch_litday index (if any) then lit_epoch.lit_day_id column."""
    from sqlalchemy import text

    print('\n--- Step 3: Drop lit_epoch.lit_day_id column ---')

    if not column_exists(conn, 'lit_epoch', 'lit_day_id'):
        print('[skip] lit_epoch.lit_day_id does not exist')
        return

    # Drop index first if it exists (MySQL will error if you drop a column that
    # is the sole column of an index — drop explicitly to be safe).
    if index_exists(conn, 'lit_epoch', 'idx_epoch_litday'):
        print('[run]  DROP INDEX idx_epoch_litday ON lit_epoch')
        if not dry_run:
            conn.execute(text('ALTER TABLE lit_epoch DROP INDEX idx_epoch_litday'))
            print('[done] idx_epoch_litday dropped')
    else:
        print('[skip] idx_epoch_litday does not exist (already gone or never created)')

    print('[run]  ALTER TABLE lit_epoch DROP COLUMN lit_day_id')
    if not dry_run:
        conn.execute(text('ALTER TABLE lit_epoch DROP COLUMN lit_day_id'))
        print('[done] lit_day_id column dropped from lit_epoch')


# ---------------------------------------------------------------------------
# Step 4: Drop TABLE liturgical_day
# ---------------------------------------------------------------------------

def step4_drop_liturgical_day(conn, dry_run):
    """
    Confirm no inbound FKs remain (excluding fk_pos_lit_day, which Step 2
    will have already dropped), then DROP TABLE liturgical_day.
    """
    from sqlalchemy import text

    print('\n--- Step 4: DROP TABLE liturgical_day ---')

    if not table_exists(conn, 'liturgical_day'):
        print('[skip] liturgical_day does not exist (already dropped)')
        return

    # Check for remaining inbound FKs, ignoring fk_pos_lit_day which
    # Step 2 drops (in dry-run mode it hasn't been executed yet, so we
    # explicitly exclude it from the abort check — it will be gone by the
    # time we reach this step in a real run).
    inbound = conn.execute(text("""
        SELECT TABLE_NAME, CONSTRAINT_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = 'liturgio'
          AND REFERENCED_TABLE_NAME = 'liturgical_day'
          AND CONSTRAINT_NAME != 'fk_pos_lit_day'
    """)).fetchall()

    if inbound:
        details = ', '.join(f'{r[0]}.{r[1]}' for r in inbound)
        print(f'[ABORT] Remaining inbound FK references to liturgical_day '
              f'(besides fk_pos_lit_day which Step 2 handles): {details}')
        print('        Drop those FKs first, then re-run.')
        sys.exit(1)

    # In dry-run, also report the fk_pos_lit_day situation for transparency
    if dry_run and fk_exists(conn, 'fk_pos_lit_day'):
        print('[dry]  fk_pos_lit_day still references liturgical_day '
              '(Step 2 will drop it before we reach here in a real run)')

    print('[run]  DROP TABLE liturgical_day')
    if not dry_run:
        conn.execute(text('DROP TABLE liturgical_day'))
        print('[done] liturgical_day dropped')


# ---------------------------------------------------------------------------
# Step 5: Final summary
# ---------------------------------------------------------------------------

def step5_summary(conn):
    """Print summary of final state."""
    from sqlalchemy import text

    print('\n--- Step 5: Final summary ---')

    ld_exists = table_exists(conn, 'liturgical_day')
    print(f'  liturgical_day exists:          {ld_exists}  (expected: False)')

    # proper_of_seasons FK target
    pos_fk = conn.execute(text("""
        SELECT REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = 'liturgio'
          AND TABLE_NAME = 'proper_of_seasons'
          AND CONSTRAINT_NAME = 'fk_pos_lit_epoch'
    """)).fetchone()
    if pos_fk:
        print(f'  proper_of_seasons FK target:    {pos_fk[0]}({pos_fk[1]})  '
              f'(expected: lit_epoch(slug))')
    else:
        print(f'  proper_of_seasons fk_pos_lit_epoch: NOT FOUND')

    lit_day_id_col = column_exists(conn, 'lit_epoch', 'lit_day_id')
    print(f'  lit_epoch.lit_day_id exists:    {lit_day_id_col}  (expected: False)')

    epoch_rows = conn.execute(text('SELECT COUNT(*) FROM lit_epoch')).scalar()
    pos_rows = conn.execute(text('SELECT COUNT(*) FROM proper_of_seasons')).scalar()
    print(f'  lit_epoch row count:            {epoch_rows}  (expected: 493)')
    print(f'  proper_of_seasons row count:    {pos_rows}  (expected: 1507)')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(
        description='Step 7c: drop liturgical_day and repoint FKs to lit_epoch.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would happen without making any changes.')
    args = parser.parse_args()

    print('=== migrate_step7c: re-root off liturgical_day ===')
    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')

    engine = get_engine()

    if args.dry_run:
        with engine.connect() as conn:
            step1_backup(conn, dry_run=True)
            step2_repoint_fk(conn, dry_run=True)
            step3_drop_lit_day_id_column(conn, dry_run=True)
            step4_drop_liturgical_day(conn, dry_run=True)
            step5_summary(conn)
    else:
        # Step 1: backup (autocommit-friendly: CREATE TABLE ... AS SELECT)
        with engine.begin() as conn:
            step1_backup(conn, dry_run=False)

        # Step 2: drop old FK, add new FK (separate transactions because
        # MySQL DDL is non-transactional anyway, but we isolate for clarity)
        with engine.begin() as conn:
            step2_repoint_fk(conn, dry_run=False)

        # Step 3: drop lit_epoch.lit_day_id
        with engine.begin() as conn:
            step3_drop_lit_day_id_column(conn, dry_run=False)

        # Step 4: drop liturgical_day
        with engine.begin() as conn:
            step4_drop_liturgical_day(conn, dry_run=False)

        # Step 5: summary
        with engine.connect() as conn:
            step5_summary(conn)

    print('\n=== Done ===')


if __name__ == '__main__':
    main()
