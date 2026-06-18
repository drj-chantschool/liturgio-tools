#!/usr/bin/env python3
"""
Roadmap step 6a: add lit_epoch_slug to lit_part_sources.

Adds a lit_epoch_slug column (FK -> lit_epoch.slug) and populates it for
all rows that can be mapped to the existing lit_epoch tree.

Three row types handled:
  1. Seasonal day rows (wkday IS NOT NULL, month IS NULL):
       Primary join: lit_epoch.kind='day', seq = wkday
       Fallback:     seq = wkday - 1  (NAT season uses 0-indexed seq)
  2. Seasonal week rows (wkday IS NULL, wknum IS NOT NULL, month IS NULL):
       Join: lit_epoch.kind='week', (season, subseason, wknum)
  3. Feast rows (month IS NOT NULL):
       Join: proper_of_saints.month_nominal = month
             AND proper_of_saints.day_nominal = day_of_month
       → lit_epoch.slug = proper_of_saints.slug  (saint node)

Rows not resolvable:
  - NAT/DAY wkday=2,3: Christmas Masses 2&3 have no separate lit_epoch node
  - ADV/I/4: 4th Sunday of Advent missing from liturgical_day
  - Feast rows without proper_of_saints coverage (most saints awaiting GR OCR)

Idempotent: re-running the UPDATE steps is safe (overwrites with same values).

Usage:
    python scripts/migrate_step6a_lit_part_sources_epoch_slug.py [--dry-run]
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


def add_column_if_needed(conn, dry_run):
    from sqlalchemy import text
    if column_exists(conn, 'lit_part_sources', 'lit_epoch_slug'):
        print('[skip] lit_epoch_slug column already exists')
        return
    print('[run]  ADD COLUMN lit_epoch_slug')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD COLUMN lit_epoch_slug VARCHAR(64) NULL "
            "COMMENT 'FK to lit_epoch; replaces season/subseason/wknum/wkday and month/day_of_month for epoch lookup'"
        ))


def add_fk_if_needed(conn, dry_run):
    from sqlalchemy import text
    if fk_exists(conn, 'fk_lps_epoch'):
        print('[skip] FK fk_lps_epoch already exists')
        return
    print('[run]  ADD FOREIGN KEY fk_lps_epoch -> lit_epoch(slug)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD CONSTRAINT fk_lps_epoch "
            "FOREIGN KEY (lit_epoch_slug) REFERENCES lit_epoch(slug) "
            "ON UPDATE CASCADE ON DELETE RESTRICT"
        ))


def update_day_rows(conn, dry_run):
    """Primary pass: seasonal day rows where seq = wkday."""
    from sqlalchemy import text
    sql = """
        UPDATE lit_part_sources lps
        JOIN lit_epoch le ON le.kind = 'day'
            AND le.season    = lps.season
            AND le.subseason = lps.subseason
            AND le.wknum     <=> lps.wknum
            AND le.seq       = lps.wkday
        SET lps.lit_epoch_slug = le.slug
        WHERE lps.month IS NULL
          AND lps.wkday IS NOT NULL
          AND lps.lit_epoch_slug IS NULL
    """
    if dry_run:
        count = conn.execute(text(sql.replace('UPDATE', 'SELECT COUNT(*) FROM').replace(
            'JOIN lit_epoch le', ', lit_epoch le').replace(
            'SET lps.lit_epoch_slug = le.slug\n        WHERE', 'WHERE')
        )).scalar() if False else None
        # Just describe
        matched = conn.execute(text("""
            SELECT COUNT(DISTINCT lps.text_id)
            FROM lit_part_sources lps
            JOIN lit_epoch le ON le.kind = 'day'
                AND le.season = lps.season AND le.subseason = lps.subseason
                AND le.wknum <=> lps.wknum AND le.seq = lps.wkday
            WHERE lps.month IS NULL AND lps.wkday IS NOT NULL
        """)).scalar()
        print(f'[dry]  day rows (primary, seq=wkday): {matched} would be updated')
        return matched
    else:
        conn.execute(text(sql))
        matched = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources "
            "WHERE month IS NULL AND wkday IS NOT NULL AND lit_epoch_slug IS NOT NULL"
        )).scalar()
        print(f'[done] day rows (primary): {matched} rows have lit_epoch_slug')
        return matched


def update_day_rows_offset(conn, dry_run):
    """Fallback: NAT season day rows where seq = wkday - 1."""
    from sqlalchemy import text
    sql = """
        UPDATE lit_part_sources lps
        JOIN lit_epoch le ON le.kind = 'day'
            AND le.season    = lps.season
            AND le.subseason = lps.subseason
            AND le.wknum     <=> lps.wknum
            AND le.seq       = CAST(lps.wkday AS SIGNED) - 1
        SET lps.lit_epoch_slug = le.slug
        WHERE lps.month IS NULL
          AND lps.wkday IS NOT NULL
          AND lps.lit_epoch_slug IS NULL
    """
    if dry_run:
        matched = conn.execute(text("""
            SELECT COUNT(DISTINCT lps.text_id)
            FROM lit_part_sources lps
            JOIN lit_epoch le ON le.kind = 'day'
                AND le.season = lps.season AND le.subseason = lps.subseason
                AND le.wknum <=> lps.wknum AND le.seq = CAST(lps.wkday AS SIGNED) - 1
            WHERE lps.month IS NULL AND lps.wkday IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM lit_epoch le2
                WHERE le2.kind='day' AND le2.season=lps.season
                  AND le2.subseason=lps.subseason AND le2.wknum<=>lps.wknum
                  AND le2.seq=lps.wkday
              )
        """)).scalar()
        print(f'[dry]  day rows (fallback, seq=wkday-1): {matched} would be updated')
        return matched
    else:
        conn.execute(text(sql))
        fallback_count = conn.execute(text("""
            SELECT COUNT(*) FROM lit_part_sources
            WHERE month IS NULL AND wkday IS NOT NULL AND lit_epoch_slug IS NOT NULL
        """)).scalar()
        print(f'[done] day rows (after fallback): {fallback_count} total have lit_epoch_slug')
        return fallback_count


def update_week_rows(conn, dry_run):
    """Seasonal week rows (wkday IS NULL, wknum IS NOT NULL)."""
    from sqlalchemy import text
    sql = """
        UPDATE lit_part_sources lps
        JOIN lit_epoch le ON le.kind = 'week'
            AND le.season    = lps.season
            AND le.subseason = lps.subseason
            AND le.wknum     = lps.wknum
        SET lps.lit_epoch_slug = le.slug
        WHERE lps.month IS NULL
          AND lps.wkday IS NULL
          AND lps.wknum IS NOT NULL
          AND lps.lit_epoch_slug IS NULL
    """
    if dry_run:
        matched = conn.execute(text("""
            SELECT COUNT(DISTINCT lps.text_id)
            FROM lit_part_sources lps
            JOIN lit_epoch le ON le.kind = 'week'
                AND le.season = lps.season AND le.subseason = lps.subseason
                AND le.wknum = lps.wknum
            WHERE lps.month IS NULL AND lps.wkday IS NULL AND lps.wknum IS NOT NULL
        """)).scalar()
        print(f'[dry]  week rows: {matched} would be updated')
        return matched
    else:
        conn.execute(text(sql))
        matched = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources "
            "WHERE month IS NULL AND wkday IS NULL AND wknum IS NOT NULL AND lit_epoch_slug IS NOT NULL"
        )).scalar()
        print(f'[done] week rows: {matched} rows have lit_epoch_slug')
        return matched


def update_feast_rows(conn, dry_run):
    """Feast rows: match via proper_of_saints (month+day → slug → lit_epoch)."""
    from sqlalchemy import text
    sql = """
        UPDATE lit_part_sources lps
        JOIN proper_of_saints ps
          ON ps.month_nominal = lps.month
         AND ps.day_nominal   = lps.day_of_month
        JOIN lit_epoch le ON le.slug = ps.slug
        SET lps.lit_epoch_slug = ps.slug
        WHERE lps.month IS NOT NULL
          AND lps.lit_epoch_slug IS NULL
    """
    if dry_run:
        matched = conn.execute(text("""
            SELECT COUNT(DISTINCT lps.text_id)
            FROM lit_part_sources lps
            JOIN proper_of_saints ps ON ps.month_nominal = lps.month
                AND ps.day_nominal = lps.day_of_month
            JOIN lit_epoch le ON le.slug = ps.slug
            WHERE lps.month IS NOT NULL
        """)).scalar()
        total_feast = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources WHERE month IS NOT NULL"
        )).scalar()
        print(f'[dry]  feast rows: {matched}/{total_feast} would be updated '
              f'({total_feast - matched} await proper_of_saints population)')
        return matched
    else:
        conn.execute(text(sql))
        matched = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources WHERE month IS NOT NULL AND lit_epoch_slug IS NOT NULL"
        )).scalar()
        total_feast = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources WHERE month IS NOT NULL"
        )).scalar()
        print(f'[done] feast rows: {matched}/{total_feast} have lit_epoch_slug '
              f'({total_feast - matched} await proper_of_saints population)')
        return matched


def report_unmatched(conn):
    """Report seasonal rows still without a slug after both passes."""
    from sqlalchemy import text
    unmatched = conn.execute(text("""
        SELECT DISTINCT season, subseason, wknum, wkday,
               COUNT(*) OVER (PARTITION BY season, subseason, wknum, wkday) as row_cnt
        FROM lit_part_sources
        WHERE month IS NULL AND lit_epoch_slug IS NULL
        ORDER BY season, subseason, wknum, wkday
    """)).fetchall()
    if unmatched:
        seen = set()
        print(f'\n[warn] {len(unmatched)} seasonal rows still unresolved (no lit_epoch node):')
        for r in unmatched:
            key = (r[0], r[1], r[2], r[3])
            if key not in seen:
                seen.add(key)
                print(f'       {r[0]}/{r[1]}/wk{r[2]}/wkday{r[3]}')
        print('       → ADV/I/4: 4th Advent Sunday missing from liturgical_day')
        print('       → NAT/DAY/2,3: Christmas Masses 2&3 have no separate epoch node')
    else:
        print('\n[ok]   All seasonal rows have lit_epoch_slug.')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine()
    from sqlalchemy import text

    print('=== migrate_step6a: add lit_epoch_slug to lit_part_sources ===\n')

    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')
        with engine.connect() as conn:
            add_column_if_needed(conn, dry_run=True)
            add_fk_if_needed(conn, dry_run=True)
            update_day_rows(conn, dry_run=True)
            update_day_rows_offset(conn, dry_run=True)
            update_week_rows(conn, dry_run=True)
            update_feast_rows(conn, dry_run=True)
    else:
        with engine.begin() as conn:
            add_column_if_needed(conn, dry_run=False)
        # FK can only be added after column exists and data is valid
        with engine.begin() as conn:
            update_day_rows(conn, dry_run=False)
            update_day_rows_offset(conn, dry_run=False)
            update_week_rows(conn, dry_run=False)
            update_feast_rows(conn, dry_run=False)
        with engine.begin() as conn:
            add_fk_if_needed(conn, dry_run=False)
        with engine.connect() as conn:
            report_unmatched(conn)

        with engine.connect() as conn:
            total = conn.execute(text('SELECT COUNT(*) FROM lit_part_sources')).scalar()
            have_slug = conn.execute(text(
                'SELECT COUNT(*) FROM lit_part_sources WHERE lit_epoch_slug IS NOT NULL'
            )).scalar()
            print(f'\n=== Summary: {have_slug}/{total} lit_part_sources rows have lit_epoch_slug ===')


if __name__ == '__main__':
    main()
