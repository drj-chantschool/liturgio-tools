#!/usr/bin/env python3
"""
Roadmap step 7b: add season/subseason FKs to lit_epoch.

Mirrors the FKs that liturgical_day already carries:
  - liturgical_day_p_liturgical_seasons_FK
  - liturgical_day_p_liturgical_subseasons_FK

Steps applied here:
1. Add a composite KEY on lit_epoch(season, subseason) if not already present.
   MySQL requires an index on the referencing columns for the composite FK.
   The single-column season FK is covered by that same composite index.

2. Add FK fk_epoch_season:
   lit_epoch.season -> p_liturgical_seasons(season)
   ON UPDATE CASCADE ON DELETE RESTRICT

3. Add FK fk_epoch_subseason (composite):
   lit_epoch(season, subseason) -> p_liturgical_subseasons(season, subseason)
   ON UPDATE CASCADE ON DELETE RESTRICT

NULL season/subseason rows (17 saint/mass nodes + season-level rows with NULL
subseason) pass under MySQL MATCH SIMPLE semantics — no action taken for NULLs.
Orphan checks count only rows where the value IS NOT NULL.

All steps are idempotent: already-applied steps print [skip].
Script aborts if any orphan is found.

Usage:
    python scripts/migrate_step7b_lit_epoch_season_fks.py [--dry-run]
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


def fk_exists(conn, fk_name):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA='liturgio' AND CONSTRAINT_NAME=:n"
    ), {'n': fk_name}).scalar()
    return row > 0


def composite_index_exists(conn, table, col1, col2):
    """Return True if any index on `table` starts with (col1, col2) as the
    first two columns (in order), which satisfies MySQL's prefix rule."""
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME "
        "FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
    ), {'t': table}).fetchall()

    # Build {index_name: [col_name_in_order]}
    indexes = {}
    for r in rows:
        indexes.setdefault(r[0], []).append(r[2])

    for idx_name, cols in indexes.items():
        if len(cols) >= 2 and cols[0] == col1 and cols[1] == col2:
            return True
    return False


def add_composite_index_if_needed(conn, dry_run):
    """Add KEY idx_epoch_season_subseason (season, subseason) on lit_epoch."""
    from sqlalchemy import text
    if composite_index_exists(conn, 'lit_epoch', 'season', 'subseason'):
        print('[skip] composite index on lit_epoch(season, subseason) already exists')
        return
    print('[run]  ADD KEY idx_epoch_season_subseason (season, subseason) on lit_epoch')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_epoch "
            "ADD KEY idx_epoch_season_subseason (season, subseason)"
        ))
        print('[done] index idx_epoch_season_subseason added')


def check_orphans_season(conn):
    """Abort if any non-NULL lit_epoch.season is absent from p_liturgical_seasons."""
    from sqlalchemy import text
    orphan_count = conn.execute(text("""
        SELECT COUNT(*) FROM lit_epoch
        WHERE season IS NOT NULL
          AND season NOT IN (SELECT season FROM p_liturgical_seasons)
    """)).scalar()
    if orphan_count > 0:
        samples = conn.execute(text("""
            SELECT DISTINCT season FROM lit_epoch
            WHERE season IS NOT NULL
              AND season NOT IN (SELECT season FROM p_liturgical_seasons)
            LIMIT 5
        """)).fetchall()
        sys.exit(
            f'ERROR: {orphan_count} orphan season value(s) in lit_epoch '
            f'not in p_liturgical_seasons: {[r[0] for r in samples]}. '
            f'Populate the lookup table before adding FK.'
        )
    print('[ok]   orphan check passed for lit_epoch.season (0 orphans)')


def check_orphans_subseason(conn):
    """Abort if any non-NULL (season, subseason) in lit_epoch is absent from
    p_liturgical_subseasons. Rows where either column is NULL are skipped
    (MySQL MATCH SIMPLE semantics)."""
    from sqlalchemy import text
    orphan_count = conn.execute(text("""
        SELECT COUNT(*) FROM lit_epoch
        WHERE season IS NOT NULL AND subseason IS NOT NULL
          AND (season, subseason) NOT IN (
              SELECT season, subseason FROM p_liturgical_subseasons
          )
    """)).scalar()
    if orphan_count > 0:
        samples = conn.execute(text("""
            SELECT DISTINCT season, subseason FROM lit_epoch
            WHERE season IS NOT NULL AND subseason IS NOT NULL
              AND (season, subseason) NOT IN (
                  SELECT season, subseason FROM p_liturgical_subseasons
              )
            LIMIT 5
        """)).fetchall()
        sys.exit(
            f'ERROR: {orphan_count} orphan (season, subseason) combo(s) in lit_epoch '
            f'not in p_liturgical_subseasons: {[(r[0], r[1]) for r in samples]}. '
            f'Populate the lookup table before adding FK.'
        )
    print('[ok]   orphan check passed for lit_epoch(season, subseason) (0 orphans)')


def add_fk_epoch_season(conn, dry_run):
    from sqlalchemy import text
    if fk_exists(conn, 'fk_epoch_season'):
        print('[skip] FK fk_epoch_season already exists')
        return
    check_orphans_season(conn)
    print('[run]  ADD FOREIGN KEY fk_epoch_season -> p_liturgical_seasons(season)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_epoch "
            "ADD CONSTRAINT fk_epoch_season "
            "FOREIGN KEY (season) "
            "REFERENCES p_liturgical_seasons(season) "
            "ON UPDATE CASCADE ON DELETE RESTRICT"
        ))
        print('[done] FK fk_epoch_season added')


def add_fk_epoch_subseason(conn, dry_run):
    from sqlalchemy import text
    if fk_exists(conn, 'fk_epoch_subseason'):
        print('[skip] FK fk_epoch_subseason already exists')
        return
    check_orphans_subseason(conn)
    print('[run]  ADD FOREIGN KEY fk_epoch_subseason -> p_liturgical_subseasons(season, subseason)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_epoch "
            "ADD CONSTRAINT fk_epoch_subseason "
            "FOREIGN KEY (season, subseason) "
            "REFERENCES p_liturgical_subseasons(season, subseason) "
            "ON UPDATE CASCADE ON DELETE RESTRICT"
        ))
        print('[done] FK fk_epoch_subseason added')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine()

    print('=== migrate_step7b: add season/subseason FKs to lit_epoch ===\n')

    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')
        with engine.connect() as conn:
            add_composite_index_if_needed(conn, dry_run=True)
            add_fk_epoch_season(conn, dry_run=True)
            add_fk_epoch_subseason(conn, dry_run=True)
    else:
        # Add index first (FK depends on it)
        with engine.begin() as conn:
            add_composite_index_if_needed(conn, dry_run=False)
        # Add FKs in separate transactions
        with engine.begin() as conn:
            add_fk_epoch_season(conn, dry_run=False)
        with engine.begin() as conn:
            add_fk_epoch_subseason(conn, dry_run=False)

    print('\n=== migrate_step7b complete ===')


if __name__ == '__main__':
    main()
