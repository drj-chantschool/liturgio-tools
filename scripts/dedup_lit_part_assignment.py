#!/usr/bin/env python3
"""
Dedup lit_part_assignment: remove exact-duplicate rows (same chant_group_id)
while preserving intentional alternative assignments (different chant_group_ids).

Background:
  The unique key uq_lit_part_assignment covers (jurisdiction, part_id,
  lit_epoch_slug, wkday, cycle_wk, cycle_sun, wknum_mod_4, wknum_mod_2).
  MySQL allows multiple rows to share a unique key when any key column is NULL,
  so pre-existing duplicates slipped in before constraint enforcement was tightened.

Two categories found:
  A. True duplicates (same chant_group_id, different authority_code): keep
     the row with MIN(assignment_id), delete the rest. 27 groups.
  B. Intentional alternatives (different chant_group_id, notes like 'or'):
     left in place — these need a schema change (add option_num column) to
     represent properly. 14 groups are reported but not deleted.

Usage:
    python scripts/dedup_lit_part_assignment.py [--dry-run]
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


DUP_GROUPS_SQL = """
    SELECT
        jurisdiction, part_id, lit_epoch_slug, wkday,
        cycle_wk, cycle_sun, wknum_mod_4, wknum_mod_2,
        COUNT(*)                                              AS cnt,
        COUNT(DISTINCT chant_group_id)                       AS distinct_groups,
        MIN(assignment_id)                                    AS keep_id,
        GROUP_CONCAT(assignment_id   ORDER BY assignment_id) AS all_ids,
        GROUP_CONCAT(chant_group_id  ORDER BY assignment_id) AS all_group_ids,
        GROUP_CONCAT(assignment_authority_code ORDER BY assignment_id) AS all_authorities,
        GROUP_CONCAT(IFNULL(notes, '') ORDER BY assignment_id SEPARATOR '|') AS all_notes
    FROM lit_part_assignment
    GROUP BY jurisdiction, part_id, lit_epoch_slug, wkday,
             cycle_wk, cycle_sun, wknum_mod_4, wknum_mod_2
    HAVING COUNT(*) > 1
    ORDER BY distinct_groups, cnt DESC, lit_epoch_slug
"""


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Report without deleting anything')
    args = parser.parse_args()

    engine = get_engine()
    from sqlalchemy import text

    with engine.connect() as conn:
        groups = conn.execute(text(DUP_GROUPS_SQL)).fetchall()

    true_dups = [g for g in groups if g[9] == 1]     # distinct_groups == 1
    alternatives = [g for g in groups if g[9] > 1]   # distinct_groups > 1

    print(f'Found {len(groups)} duplicate groups total:')
    print(f'  {len(true_dups)} true duplicates (same chant_group_id) → will delete extras')
    print(f'  {len(alternatives)} alternative assignments (different chant_group_ids) → left in place')

    # ── True duplicates ──────────────────────────────────────────────────────
    ids_to_delete = []
    for g in true_dups:
        keep_id = g[10]     # MIN(assignment_id)
        all_ids = [int(x) for x in g[11].split(',')]
        delete_ids = [i for i in all_ids if i != keep_id]
        ids_to_delete.extend(delete_ids)
        print(f'  dup: epoch={g[2]} part={g[1]} → keep {keep_id}, delete {delete_ids}'
              f' (authorities: {g[13]})')

    print(f'\n→ {len(ids_to_delete)} rows to delete')

    if not ids_to_delete:
        print('Nothing to delete.')
    elif args.dry_run:
        print('DRY RUN — no changes made.')
    else:
        from sqlalchemy import text
        # Build the IN list directly into the SQL (safe: IDs are integers from the DB)
        placeholders = ','.join(str(i) for i in ids_to_delete)
        with engine.begin() as conn:
            conn.execute(text(
                f'DELETE FROM lit_part_assignment WHERE assignment_id IN ({placeholders})'
            ))
            remaining = conn.execute(
                text('SELECT COUNT(*) FROM lit_part_assignment')
            ).scalar()
        print(f'Deleted {len(ids_to_delete)} rows. Remaining: {remaining}')

    # ── Alternatives (informational) ─────────────────────────────────────────
    if alternatives:
        print(f'\n── {len(alternatives)} intentional alternative groups (NOT deleted) ──')
        print('  These represent multiple valid chant options for the same slot.')
        print('  Schema fix needed: add option_num column to uq_lit_part_assignment.\n')
        for g in alternatives:
            print(f'  epoch={g[2]} part={g[1]} '
                  f'group_ids={g[12]} notes="{g[14]}"')


if __name__ == '__main__':
    main()
