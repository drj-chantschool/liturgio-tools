#!/usr/bin/env python3
"""
Roadmap step 7a: add authority/translation FKs to lit_part_sources.

1. Narrows translation_source_code from VARCHAR(50) to VARCHAR(40) to match
   the PK p_translation_source(translation_source_code) VARCHAR(40).
   (Only runs if the current length is not already 40.)

2. Adds FK fk_lps_assignment_authority:
   lit_part_sources.assignment_authority_code -> p_assignment_authority(authority_code)
   ON UPDATE CASCADE ON DELETE RESTRICT

3. Adds FK fk_lps_translation_source:
   lit_part_sources.translation_source_code -> p_translation_source(translation_source_code)
   ON UPDATE CASCADE ON DELETE RESTRICT

Both are idempotent: already-applied steps print [skip].
Orphan check is run before each FK add; script aborts if any orphan exists.

Usage:
    python scripts/migrate_step7a_lit_part_sources_authority_fks.py [--dry-run]
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


def get_varchar_length(conn, table, column):
    """Return CHARACTER_MAXIMUM_LENGTH for a column, or None if not found."""
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': column}).scalar()


def narrow_translation_source_code(conn, dry_run):
    """Modify translation_source_code from VARCHAR(50) to VARCHAR(40) if needed."""
    from sqlalchemy import text
    current_len = get_varchar_length(conn, 'lit_part_sources', 'translation_source_code')
    if current_len == 40:
        print('[skip] translation_source_code is already VARCHAR(40)')
        return
    print(f'[run]  MODIFY translation_source_code VARCHAR({current_len}) -> VARCHAR(40)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "MODIFY translation_source_code VARCHAR(40) NULL"
        ))
        print('[done] translation_source_code narrowed to VARCHAR(40)')


def check_orphans_assignment_authority(conn):
    """Abort if any non-NULL assignment_authority_code is not in p_assignment_authority."""
    from sqlalchemy import text
    orphan_count = conn.execute(text("""
        SELECT COUNT(*) FROM lit_part_sources
        WHERE assignment_authority_code IS NOT NULL
          AND assignment_authority_code NOT IN (
              SELECT authority_code FROM p_assignment_authority
          )
    """)).scalar()
    if orphan_count > 0:
        samples = conn.execute(text("""
            SELECT DISTINCT assignment_authority_code FROM lit_part_sources
            WHERE assignment_authority_code IS NOT NULL
              AND assignment_authority_code NOT IN (
                  SELECT authority_code FROM p_assignment_authority
              )
            LIMIT 5
        """)).fetchall()
        sys.exit(
            f'ERROR: {orphan_count} orphan assignment_authority_code value(s) '
            f'not in p_assignment_authority: {[r[0] for r in samples]}. '
            f'Populate the lookup table before adding FK.'
        )
    print(f'[ok]   orphan check passed for assignment_authority_code (0 orphans)')


def check_orphans_translation_source(conn):
    """Abort if any non-NULL translation_source_code is not in p_translation_source."""
    from sqlalchemy import text
    orphan_count = conn.execute(text("""
        SELECT COUNT(*) FROM lit_part_sources
        WHERE translation_source_code IS NOT NULL
          AND translation_source_code NOT IN (
              SELECT translation_source_code FROM p_translation_source
          )
    """)).scalar()
    if orphan_count > 0:
        samples = conn.execute(text("""
            SELECT DISTINCT translation_source_code FROM lit_part_sources
            WHERE translation_source_code IS NOT NULL
              AND translation_source_code NOT IN (
                  SELECT translation_source_code FROM p_translation_source
              )
            LIMIT 5
        """)).fetchall()
        sys.exit(
            f'ERROR: {orphan_count} orphan translation_source_code value(s) '
            f'not in p_translation_source: {[r[0] for r in samples]}. '
            f'Populate the lookup table before adding FK.'
        )
    print(f'[ok]   orphan check passed for translation_source_code (0 orphans)')


def add_fk_assignment_authority(conn, dry_run):
    from sqlalchemy import text
    if fk_exists(conn, 'fk_lps_assignment_authority'):
        print('[skip] FK fk_lps_assignment_authority already exists')
        return
    check_orphans_assignment_authority(conn)
    print('[run]  ADD FOREIGN KEY fk_lps_assignment_authority -> p_assignment_authority(authority_code)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD CONSTRAINT fk_lps_assignment_authority "
            "FOREIGN KEY (assignment_authority_code) "
            "REFERENCES p_assignment_authority(authority_code) "
            "ON UPDATE CASCADE ON DELETE RESTRICT"
        ))
        print('[done] FK fk_lps_assignment_authority added')


def add_fk_translation_source(conn, dry_run):
    from sqlalchemy import text
    if fk_exists(conn, 'fk_lps_translation_source'):
        print('[skip] FK fk_lps_translation_source already exists')
        return
    check_orphans_translation_source(conn)
    print('[run]  ADD FOREIGN KEY fk_lps_translation_source -> p_translation_source(translation_source_code)')
    if not dry_run:
        conn.execute(text(
            "ALTER TABLE lit_part_sources "
            "ADD CONSTRAINT fk_lps_translation_source "
            "FOREIGN KEY (translation_source_code) "
            "REFERENCES p_translation_source(translation_source_code) "
            "ON UPDATE CASCADE ON DELETE RESTRICT"
        ))
        print('[done] FK fk_lps_translation_source added')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine()

    print('=== migrate_step7a: add authority/translation FKs to lit_part_sources ===\n')

    if args.dry_run:
        print('DRY RUN — no changes will be made.\n')
        with engine.connect() as conn:
            narrow_translation_source_code(conn, dry_run=True)
            add_fk_assignment_authority(conn, dry_run=True)
            add_fk_translation_source(conn, dry_run=True)
    else:
        # Step 1: narrow the column first (must commit before adding FK)
        with engine.begin() as conn:
            narrow_translation_source_code(conn, dry_run=False)
        # Step 2: add FKs (separate transactions so each commits cleanly)
        with engine.begin() as conn:
            add_fk_assignment_authority(conn, dry_run=False)
        with engine.begin() as conn:
            add_fk_translation_source(conn, dry_run=False)

    print('\n=== migrate_step7a complete ===')


if __name__ == '__main__':
    main()
