#!/usr/bin/env python3
"""
Pre-migration prep for step 8: add and populate chant_uuid on lit_part_assignment.

Run this BEFORE migrate_step8_lps_absorbs_lpa.py so that Phase A and Phase B
can copy lpa.chant_uuid directly rather than falling back to a coarse
MIN(gregobase_id) lookup.

Phases:
  0. ADD COLUMN chant_uuid VARCHAR(255) NULL to lit_part_assignment (idempotent).
  1. GRADUALE rows: populate via gr_index_entry.gregobase_id → gregobase_chant_group_map.
     Uses the resolved best-edition gregobase chant from the index resolution pass,
     which is more accurate than a bare MIN(gregobase_id) across the whole group map.
  2. MISSAL rows (still NULL after phase 1): populate via local_chants.
  3. Report remaining NULLs grouped by assignment_authority_code.

Usage:
    python scripts/migrate_step8a_prep_lpa_chant_uuid.py [--dry-run]
"""

import argparse
import sys

KEYRING_SERVICE = 'liturgio-mysql'
KEYRING_USER    = 'jcost'
DB_HOST, DB_PORT, DB_NAME = 'localhost', 3306, 'liturgio'


def get_engine():
    import keyring
    from sqlalchemy import create_engine
    pw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if not pw:
        sys.exit(f'No keyring password for ({KEYRING_SERVICE!r}, {KEYRING_USER!r})')
    url = f'mysql+mysqlconnector://{KEYRING_USER}:{pw}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    return create_engine(url)


def column_exists(conn, table, column):
    from sqlalchemy import text
    row = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {'db': DB_NAME, 't': table, 'c': column}).scalar()
    return row > 0


def do(conn, label, sql, dry_run):
    print(f'  {"[dry-run] " if dry_run else ""}  {label}')
    if not dry_run:
        from sqlalchemy import text
        conn.execute(text(sql))


def phase0_add_column(conn, dry_run):
    """Add chant_uuid column to lit_part_assignment if absent."""
    if column_exists(conn, 'lit_part_assignment', 'chant_uuid'):
        print('  [skip] chant_uuid column already exists on lit_part_assignment')
        return
    do(conn, 'ADD COLUMN chant_uuid',
       "ALTER TABLE lit_part_assignment "
       "ADD COLUMN chant_uuid VARCHAR(255) NULL AFTER chant_group_id",
       dry_run)


def phase1_graduale(conn, dry_run):
    """
    Populate chant_uuid for GRADUALE rows via gr_index_entry.gregobase_id.

    The gr_index_entry.gregobase_id column is the resolved best-edition chant
    for each index entry (populated by resolve_gr_index.py).  Joining through
    gregobase_chant_group_map gives us the confirmed chant_uuid for each group,
    which is more accurate than a bare MIN across the whole map.

    When a group maps to multiple resolved gregobase_ids (edge case), we take
    the minimum — consistent with the rest of the codebase's tiebreak policy.
    """
    from sqlalchemy import text

    sql_update = """
        UPDATE lit_part_assignment lpa
        JOIN (
            SELECT cgm.chant_group_id,
                   CONCAT('gregobase:', MIN(cgm.gregobase_id)) AS chant_uuid
            FROM gregobase_chants gc
            JOIN gregobase_chant_group_map cgm ON gc.id = cgm.gregobase_id
            INNER JOIN gr_index_entry gidx     ON gidx.gregobase_id = gc.id
            GROUP BY cgm.chant_group_id
        ) src ON src.chant_group_id = lpa.chant_group_id
        SET lpa.chant_uuid = src.chant_uuid
        WHERE lpa.assignment_authority_code = 'GRADUALE'
          AND lpa.chant_uuid IS NULL
    """

    if dry_run:
        # Count how many rows would be updated
        count = conn.execute(text("""
            SELECT COUNT(*)
            FROM lit_part_assignment lpa
            JOIN (
                SELECT cgm.chant_group_id
                FROM gregobase_chants gc
                JOIN gregobase_chant_group_map cgm ON gc.id = cgm.gregobase_id
                INNER JOIN gr_index_entry gidx     ON gidx.gregobase_id = gc.id
                GROUP BY cgm.chant_group_id
            ) src ON src.chant_group_id = lpa.chant_group_id
            WHERE lpa.assignment_authority_code = 'GRADUALE'
              AND lpa.chant_uuid IS NULL
        """)).scalar()
        print(f'  [dry-run] Phase 1 GRADUALE: would populate chant_uuid on {count} rows')
        return

    result = conn.execute(text(sql_update))
    conn.commit()
    print(f'  [done]    Phase 1 GRADUALE: populated chant_uuid on {result.rowcount} rows')


def phase2_missal(conn, dry_run):
    """
    Populate chant_uuid for MISSAL rows (still NULL) via local_chants.

    MISSAL antiphon recordings are stored as local chants, not in gregobase.
    Takes the minimum local_chant_id per group as a deterministic tiebreak.
    """
    from sqlalchemy import text

    sql_update = """
        UPDATE lit_part_assignment lpa
        JOIN (
            SELECT chant_group_id,
                   CONCAT('local:', MIN(local_chant_id)) AS chant_uuid
            FROM local_chants
            GROUP BY chant_group_id
        ) src ON src.chant_group_id = lpa.chant_group_id
        SET lpa.chant_uuid = src.chant_uuid
        WHERE lpa.assignment_authority_code = 'MISSAL'
          AND lpa.chant_uuid IS NULL
    """

    if dry_run:
        count = conn.execute(text("""
            SELECT COUNT(*)
            FROM lit_part_assignment lpa
            JOIN (
                SELECT chant_group_id FROM local_chants GROUP BY chant_group_id
            ) src ON src.chant_group_id = lpa.chant_group_id
            WHERE lpa.assignment_authority_code = 'MISSAL'
              AND lpa.chant_uuid IS NULL
        """)).scalar()
        print(f'  [dry-run] Phase 2 MISSAL:   would populate chant_uuid on {count} rows')
        return

    result = conn.execute(text(sql_update))
    conn.commit()
    print(f'  [done]    Phase 2 MISSAL:   populated chant_uuid on {result.rowcount} rows')


def phase3_report(conn):
    """Report rows still missing chant_uuid, grouped by authority_code."""
    from sqlalchemy import text

    rows = conn.execute(text("""
        SELECT COALESCE(assignment_authority_code, '(none)') AS authority,
               COUNT(*) AS cnt
        FROM lit_part_assignment
        WHERE chant_uuid IS NULL
        GROUP BY assignment_authority_code
        ORDER BY cnt DESC
    """)).mappings().fetchall()

    total_null = sum(r['cnt'] for r in rows)
    total_all  = conn.execute(text(
        "SELECT COUNT(*) FROM lit_part_assignment"
    )).scalar()
    populated  = total_all - total_null

    print(f'\n  Summary: {populated}/{total_all} lpa rows now have chant_uuid')
    if rows:
        print(f'  Still NULL ({total_null} rows):')
        for r in rows:
            print(f'    authority={r["authority"]:20s}  count={r["cnt"]}')
    else:
        print('  All rows have chant_uuid.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true',
                    help='Print what would happen without writing anything')
    args = ap.parse_args()

    engine = get_engine()

    print('── Phase 0: ADD COLUMN chant_uuid ──')
    with engine.begin() as conn:
        phase0_add_column(conn, args.dry_run)

    print('\n── Phase 1: populate GRADUALE rows (via gr_index_entry) ──')
    with engine.connect() as conn:
        phase1_graduale(conn, args.dry_run)

    print('\n── Phase 2: populate MISSAL rows (via local_chants) ──')
    with engine.connect() as conn:
        phase2_missal(conn, args.dry_run)

    print('\n── Phase 3: remaining NULLs ──')
    with engine.connect() as conn:
        phase3_report(conn)

    engine.dispose()


if __name__ == '__main__':
    main()
