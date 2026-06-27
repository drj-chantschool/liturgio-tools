#!/usr/bin/env python3
"""
Roadmap step 8: lit_part_sources absorbs lit_part_assignment.

Merges every assignment row from lit_part_assignment into lit_part_sources,
adding the missing assignment columns to lit_part_sources and then dropping
lit_part_assignment.

DDL changes to lit_part_sources:
  1. RENAME status      → review_status  (same VARCHAR(20) draft/reviewed/published)
  2. ADD jurisdiction   VARCHAR(64) NOT NULL DEFAULT 'UNIVERSAL'
  3. ADD option_num     TINYINT UNSIGNED NOT NULL DEFAULT 1
  4. ADD wknum_mod_4    TINYINT UNSIGNED NULL
  5. ADD wknum_mod_2    TINYINT UNSIGNED NULL
  6. ADD notes          VARCHAR(500) NULL

Data migration:
  Phase A — rows with text_id IS NOT NULL (lpa points to an existing lps row):
    UPDATE lps rows with jurisdiction, option_num, wknum_mod_4, wknum_mod_2,
    cycle_wkday (copied from lpa.cycle_wk), notes.
    Promote review_status to 'reviewed' if needs_review=0.
    If chant_uuid is NULL, try to populate it from gregobase_chant_group_map /
    local_chants (any representative chant item for the group).
  Phase B — rows with text_id IS NULL (pure assignment, no source text):
    INSERT new lps rows carrying the assignment columns; chant_uuid populated
    the same way.

Drop:
  lit_part_assignment (with safety checks).

Usage:
    python scripts/migrate_step8_lps_absorbs_lpa.py [--dry-run] [--skip-drop]
"""

import sys
import argparse

SCHEMA = 'liturgio'


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


def column_exists(conn, table, col):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'s': SCHEMA, 't': table, 'c': col}).scalar() > 0


def table_exists(conn, table):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t"
    ), {'s': SCHEMA, 't': table}).scalar() > 0


def index_exists(conn, table, index_name):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t AND INDEX_NAME=:i"
    ), {'s': SCHEMA, 't': table, 'i': index_name}).scalar() > 0


def fk_exists(conn, fk_name):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA=:s AND CONSTRAINT_NAME=:n"
    ), {'s': SCHEMA, 'n': fk_name}).scalar() > 0


def do(conn, label, sql, dry_run, params=None):
    from sqlalchemy import text
    print(f'  [run]  {label}')
    if dry_run:
        first = sql.strip().splitlines()[0]
        print(f'         DRY RUN: {first} ...')
    else:
        conn.execute(text(sql), params or {})


# ── Phase 0: DDL changes on lit_part_sources ─────────────────────────────────

def ddl_rename_status(conn, dry_run):
    if column_exists(conn, 'lit_part_sources', 'review_status'):
        print('  [skip] review_status column already exists')
        return
    if not column_exists(conn, 'lit_part_sources', 'status'):
        print('  [warn] neither status nor review_status found — skipping rename')
        return
    do(conn, 'RENAME COLUMN status → review_status',
       "ALTER TABLE lit_part_sources "
       "RENAME COLUMN status TO review_status", dry_run)



def ddl_add_jurisdiction(conn, dry_run):
    if column_exists(conn, 'lit_part_sources', 'jurisdiction'):
        print('  [skip] jurisdiction column already exists')
        return
    do(conn, "ADD COLUMN jurisdiction",
       "ALTER TABLE lit_part_sources "
       "ADD COLUMN jurisdiction VARCHAR(64) NOT NULL DEFAULT 'UNIVERSAL' "
       "COMMENT 'jurisdiction code (UNIVERSAL, US, etc.); DEFAULT covers all existing rows'",
       dry_run)


def ddl_add_option_num(conn, dry_run):
    if column_exists(conn, 'lit_part_sources', 'option_num'):
        print('  [skip] option_num column already exists')
        return
    do(conn, "ADD COLUMN option_num",
       "ALTER TABLE lit_part_sources "
       "ADD COLUMN option_num TINYINT UNSIGNED NOT NULL DEFAULT 1 "
       "COMMENT '1=primary/default, 2+=alternate options for the same slot'",
       dry_run)


def ddl_add_wknum_mods(conn, dry_run):
    if column_exists(conn, 'lit_part_sources', 'wknum_mod_4'):
        print('  [skip] wknum_mod_4 already exists')
    else:
        do(conn, "ADD COLUMN wknum_mod_4",
           "ALTER TABLE lit_part_sources "
           "ADD COLUMN wknum_mod_4 TINYINT UNSIGNED NULL "
           "COMMENT 'psalter fallback: matches when wknum % 4 equals this value'",
           dry_run)
    if column_exists(conn, 'lit_part_sources', 'wknum_mod_2'):
        print('  [skip] wknum_mod_2 already exists')
    else:
        do(conn, "ADD COLUMN wknum_mod_2",
           "ALTER TABLE lit_part_sources "
           "ADD COLUMN wknum_mod_2 TINYINT UNSIGNED NULL "
           "COMMENT 'psalter fallback: matches when wknum % 2 equals this value'",
           dry_run)


def ddl_add_notes(conn, dry_run):
    if column_exists(conn, 'lit_part_sources', 'notes'):
        print('  [skip] notes column already exists')
        return
    do(conn, "ADD COLUMN notes",
       "ALTER TABLE lit_part_sources "
       "ADD COLUMN notes VARCHAR(500) NULL",
       dry_run)


def ddl_add_indexes(conn, dry_run):
    for idx, col in [
        ('idx_lps_jurisdiction', 'jurisdiction'),
        ('idx_lps_option_num',   'option_num'),
        ('idx_lps_wknum_mod_4',  'wknum_mod_4'),
    ]:
        if index_exists(conn, 'lit_part_sources', idx):
            print(f'  [skip] index {idx} already exists')
        else:
            do(conn, f'ADD INDEX {idx}',
               f'ALTER TABLE lit_part_sources ADD INDEX {idx} ({col})',
               dry_run)


# ── Phase A: UPDATE existing lps rows referenced by lpa ─────────────────────

def migrate_phase_a(conn, dry_run):
    """
    For each lpa row where text_id IS NOT NULL, update the linked lps row
    with assignment columns.  Also populate chant_uuid if still NULL.
    """
    from sqlalchemy import text

    rows = conn.execute(text("""
        SELECT lpa.assignment_id, lpa.text_id, lpa.jurisdiction, lpa.option_num,
               lpa.wknum_mod_4, lpa.wknum_mod_2, lpa.cycle_wk, lpa.notes,
               lpa.needs_review, lpa.chant_group_id,
               lps.chant_uuid AS existing_uuid
        FROM lit_part_assignment lpa
        JOIN lit_part_sources lps ON lps.text_id = lpa.text_id
    """)).mappings().fetchall()

    print(f'  [info] Phase A: {len(rows)} lpa rows with text_id → updating lps rows')
    updated = 0
    uuid_populated = 0

    for r in rows:
        chant_uuid = r['existing_uuid']
        if chant_uuid is None and r['chant_group_id']:
            chant_uuid = _find_chant_uuid(conn, r['chant_group_id'])
            if chant_uuid:
                uuid_populated += 1

        review_status_override = None
        if r['needs_review'] == 0:
            review_status_override = 'reviewed'

        set_parts = [
            'jurisdiction = :jurisdiction',
            'option_num   = :option_num',
            'wknum_mod_4  = :wknum_mod_4',
            'wknum_mod_2  = :wknum_mod_2',
            'notes        = COALESCE(:notes, notes)',
        ]
        params = {
            'jurisdiction': r['jurisdiction'],
            'option_num':   r['option_num'],
            'wknum_mod_4':  r['wknum_mod_4'],
            'wknum_mod_2':  r['wknum_mod_2'],
            'notes':        r['notes'],
            'tid':          r['text_id'],
        }

        if r['cycle_wk'] is not None:
            set_parts.append('cycle_wkday = :cycle_wkday')
            params['cycle_wkday'] = r['cycle_wk']

        if chant_uuid is not None and r['existing_uuid'] is None:
            set_parts.append('chant_uuid = :chant_uuid')
            params['chant_uuid'] = chant_uuid

        if review_status_override:
            set_parts.append('review_status = :review_status')
            params['review_status'] = review_status_override

        sql = f"UPDATE lit_part_sources SET {', '.join(set_parts)} WHERE text_id = :tid"
        if dry_run:
            pass  # skip individual row prints for brevity
        else:
            conn.execute(text(sql), params)
        updated += 1

    print(f'  [done] Phase A: updated {updated} lps rows; '
          f'populated chant_uuid on {uuid_populated}')


# ── Phase B: INSERT new lps rows for orphan lpa rows (text_id IS NULL) ───────

def migrate_phase_b(conn, dry_run):
    from sqlalchemy import text

    rows = conn.execute(text("""
        SELECT lpa.assignment_id, lpa.jurisdiction, lpa.part_id,
               lpa.lit_epoch_slug, lpa.wkday, lpa.cycle_wk, lpa.cycle_sun,
               lpa.wknum_mod_4, lpa.wknum_mod_2, lpa.option_num,
               lpa.assignment_authority_code, lpa.notes, lpa.needs_review,
               lpa.chant_group_id
        FROM lit_part_assignment lpa
        WHERE lpa.text_id IS NULL
    """)).mappings().fetchall()

    print(f'  [info] Phase B: {len(rows)} orphan lpa rows (no text_id) → inserting new lps rows')
    inserted = 0
    uuid_missing = 0

    for r in rows:
        chant_uuid = _find_chant_uuid(conn, r['chant_group_id'])
        if chant_uuid is None:
            uuid_missing += 1

        review_status = 'draft' if r['needs_review'] else 'reviewed'

        params = {
            'lit_epoch_slug':            r['lit_epoch_slug'],
            'part_id':                   r['part_id'],
            'jurisdiction':              r['jurisdiction'],
            'option_num':                r['option_num'],
            'wkday':                     r['wkday'],
            'cycle_wkday':               r['cycle_wk'],
            'cycle_sun':                 r['cycle_sun'],
            'wknum_mod_4':               r['wknum_mod_4'],
            'wknum_mod_2':               r['wknum_mod_2'],
            'assignment_authority_code': r['assignment_authority_code'],
            'notes':                     r['notes'],
            'review_status':             review_status,
            'chant_uuid':                chant_uuid,
        }

        if not dry_run:
            conn.execute(text("""
                INSERT INTO lit_part_sources
                    (lit_epoch_slug, part_id, jurisdiction, option_num,
                     wkday, cycle_wkday, cycle_sun, wknum_mod_4, wknum_mod_2,
                     assignment_authority_code, notes, review_status, chant_uuid)
                VALUES
                    (:lit_epoch_slug, :part_id, :jurisdiction, :option_num,
                     :wkday, :cycle_wkday, :cycle_sun, :wknum_mod_4, :wknum_mod_2,
                     :assignment_authority_code, :notes, :review_status, :chant_uuid)
            """), params)
        inserted += 1

    msg = f'  [done] Phase B: inserted {inserted} new lps rows'
    if uuid_missing:
        msg += f'; WARNING: {uuid_missing} had no resolvable chant_uuid (chant_group with no items)'
    print(msg)


def _find_chant_uuid(conn, chant_group_id):
    """Return a representative chant_uuid for a chant_group_id, or None."""
    from sqlalchemy import text
    if chant_group_id is None:
        return None

    # Prefer gregobase (lowest id = most likely canonical)
    row = conn.execute(text("""
        SELECT MIN(gregobase_id) AS gid
        FROM gregobase_chant_group_map
        WHERE chant_group_id = :gid
    """), {'gid': chant_group_id}).fetchone()
    if row and row[0] is not None:
        return f'gregobase:{row[0]}'

    # Fall back to local_chants (any version)
    row = conn.execute(text("""
        SELECT local_chant_id
        FROM local_chants
        WHERE chant_group_id = :gid
        ORDER BY local_chant_id
        LIMIT 1
    """), {'gid': chant_group_id}).fetchone()
    if row:
        return f'local:{row[0]}'

    return None


# ── Phase C: drop lit_part_assignment ────────────────────────────────────────

def drop_lpa(conn, dry_run):
    from sqlalchemy import text

    if not table_exists(conn, 'lit_part_assignment'):
        print('  [skip] lit_part_assignment does not exist (already dropped?)')
        return

    # Safety: confirm all lpa rows have been migrated (either via Phase A or B)
    unmigrated = conn.execute(text("""
        SELECT COUNT(*) FROM lit_part_assignment lpa
        WHERE lpa.text_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM lit_part_sources lps
              WHERE lps.text_id = lpa.text_id
                AND lps.jurisdiction IS NOT NULL
          )
    """)).scalar()
    if unmigrated:
        sys.exit(
            f'ABORT: {unmigrated} lpa rows with text_id point to lps rows that '
            'were not updated — migration incomplete, refusing to drop.'
        )

    # Backup before drop
    if not table_exists(conn, 'lit_part_assignment_backup_step8'):
        do(conn, 'snapshot lit_part_assignment → lit_part_assignment_backup_step8',
           'CREATE TABLE lit_part_assignment_backup_step8 '
           'AS SELECT * FROM lit_part_assignment',
           dry_run)
    else:
        print('  [skip] backup table lit_part_assignment_backup_step8 already exists')

    do(conn, 'DROP TABLE lit_part_assignment',
       'DROP TABLE lit_part_assignment', dry_run)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(
        description='Step 8: lit_part_sources absorbs lit_part_assignment.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would happen without making changes.')
    parser.add_argument('--skip-drop', action='store_true',
                        help='Skip the DROP TABLE lit_part_assignment step.')
    args = parser.parse_args()

    print('=== migrate_step8: lit_part_sources absorbs lit_part_assignment ===\n')
    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')

    engine = get_engine()

    # Each DDL step in its own transaction so partial progress is visible.
    print('── Phase 0: DDL changes to lit_part_sources ──')
    steps = [
        ddl_rename_status,
        ddl_add_jurisdiction,
        ddl_add_option_num,
        ddl_add_wknum_mods,
        ddl_add_notes,
        ddl_add_indexes,
    ]
    for step in steps:
        if args.dry_run:
            with engine.connect() as conn:
                step(conn, dry_run=True)
        else:
            with engine.begin() as conn:
                step(conn, dry_run=False)

    print('\n── Phase A: update lps rows referenced by lpa ──')
    if args.dry_run:
        with engine.connect() as conn:
            migrate_phase_a(conn, dry_run=True)
    else:
        with engine.begin() as conn:
            migrate_phase_a(conn, dry_run=False)

    print('\n── Phase B: insert orphan lpa rows (no text_id) ──')
    if args.dry_run:
        with engine.connect() as conn:
            migrate_phase_b(conn, dry_run=True)
    else:
        with engine.begin() as conn:
            migrate_phase_b(conn, dry_run=False)

    if not args.skip_drop:
        print('\n── Phase C: drop lit_part_assignment ──')
        if args.dry_run:
            with engine.connect() as conn:
                drop_lpa(conn, dry_run=True)
        else:
            with engine.begin() as conn:
                drop_lpa(conn, dry_run=False)
    else:
        print('\n── Phase C: skipped (--skip-drop) ──')

    print('\nDone.')


if __name__ == '__main__':
    main()
