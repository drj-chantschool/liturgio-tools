#!/usr/bin/env python3
"""
Roadmap step 6b: add option_num to lit_part_assignment unique key.

Problem:
  14 groups of rows share the same (jurisdiction, part_id, lit_epoch_slug, …)
  but point to different chant_group_ids — intentional alternate-chant options
  labeled 'or' / 'traditional option' / 'additional option' in notes.
  The MySQL NULL-in-unique-key loophole previously allowed these, but the
  schema expressed no intent and new inserts of the same kind would silently
  succeed or not based on which columns happen to be NULL.

Fix:
  Add option_num TINYINT UNSIGNED NOT NULL DEFAULT 1.
  Include it in the unique key.
  option_num=1 is the primary / GR default; option_num=2 is the first alternate;
  etc.  The assign CLI exposes --option-num (default 1) so composers can record
  alternatives explicitly.

Steps:
  1. Add column option_num DEFAULT 1 (existing rows become option_num=1)
  2. Set option_num=2 for the 14 secondary rows (higher assignment_id per group)
  3. Drop old unique key uq_lit_part_assignment
  4. Add new unique key including option_num

Idempotent: checks column and key existence before acting.

Usage:
    python scripts/migrate_step6b_option_num.py [--dry-run]
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
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': column}).scalar() > 0


def key_exists(conn, key_name):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_assignment' "
        "AND INDEX_NAME=:k"
    ), {'k': key_name}).scalar() > 0


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine()
    from sqlalchemy import text

    print('=== migrate_step6b: add option_num to lit_part_assignment ===\n')

    # ── Step 1: identify secondary rows ─────────────────────────────────────
    with engine.connect() as conn:
        secondary_rows = conn.execute(text("""
            SELECT a.assignment_id
            FROM lit_part_assignment a
            WHERE EXISTS (
                SELECT 1 FROM lit_part_assignment b
                WHERE b.jurisdiction   = a.jurisdiction
                  AND b.part_id        = a.part_id
                  AND b.lit_epoch_slug <=> a.lit_epoch_slug
                  AND b.wkday          <=> a.wkday
                  AND b.cycle_wk       <=> a.cycle_wk
                  AND b.cycle_sun      <=> a.cycle_sun
                  AND b.wknum_mod_4    <=> a.wknum_mod_4
                  AND b.wknum_mod_2    <=> a.wknum_mod_2
                  AND b.chant_group_id != a.chant_group_id
                  AND b.assignment_id  <  a.assignment_id
            )
        """)).fetchall()
        secondary_ids = [r[0] for r in secondary_rows]

    print(f'Secondary rows to set option_num=2: {len(secondary_ids)} '
          f'(ids: {secondary_ids})')

    if args.dry_run:
        print('\nDRY RUN — no changes made.')
        return

    # ── Step 2: add column ───────────────────────────────────────────────────
    with engine.begin() as conn:
        if column_exists(conn, 'lit_part_assignment', 'option_num'):
            print('[skip] option_num column already exists')
        else:
            print('[run]  ADD COLUMN option_num TINYINT UNSIGNED NOT NULL DEFAULT 1')
            conn.execute(text(
                "ALTER TABLE lit_part_assignment "
                "ADD COLUMN option_num TINYINT UNSIGNED NOT NULL DEFAULT 1 "
                "COMMENT '1=primary/GR-default, 2=first alternate, etc. Part of unique key.'"
            ))

    # ── Step 3: stamp secondary rows ────────────────────────────────────────
    with engine.begin() as conn:
        if secondary_ids:
            ids_str = ','.join(str(i) for i in secondary_ids)
            conn.execute(text(
                f"UPDATE lit_part_assignment SET option_num=2 "
                f"WHERE assignment_id IN ({ids_str}) AND option_num != 2"
            ))
            updated = conn.execute(text(
                f"SELECT COUNT(*) FROM lit_part_assignment WHERE option_num=2"
            )).scalar()
            print(f'[done] option_num=2 rows: {updated}')

    # ── Step 4: rebuild unique key ───────────────────────────────────────────
    with engine.begin() as conn:
        if key_exists(conn, 'uq_lit_part_assignment'):
            print('[run]  DROP KEY uq_lit_part_assignment')
            conn.execute(text(
                "ALTER TABLE lit_part_assignment DROP INDEX uq_lit_part_assignment"
            ))
        else:
            print('[skip] old unique key already absent')

        if key_exists(conn, 'uq_lit_part_assignment_v2'):
            print('[skip] uq_lit_part_assignment_v2 already exists')
        else:
            print('[run]  ADD UNIQUE KEY uq_lit_part_assignment_v2 (… option_num)')
            conn.execute(text("""
                ALTER TABLE lit_part_assignment
                ADD UNIQUE KEY uq_lit_part_assignment_v2 (
                    jurisdiction,
                    part_id,
                    lit_epoch_slug,
                    wkday,
                    cycle_wk,
                    cycle_sun,
                    wknum_mod_4,
                    wknum_mod_2,
                    option_num
                )
            """))

    # ── Verification ─────────────────────────────────────────────────────────
    with engine.connect() as conn:
        total      = conn.execute(text("SELECT COUNT(*) FROM lit_part_assignment")).scalar()
        primaries  = conn.execute(text("SELECT COUNT(*) FROM lit_part_assignment WHERE option_num=1")).scalar()
        alts       = conn.execute(text("SELECT COUNT(*) FROM lit_part_assignment WHERE option_num=2")).scalar()
        print(f'\nlit_part_assignment: {total} rows  '
              f'(option_num=1: {primaries}, option_num=2: {alts})')
    print('\nDone.')


if __name__ == '__main__':
    main()
