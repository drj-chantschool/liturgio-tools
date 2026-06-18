#!/usr/bin/env python3
"""
Migrate schema and load saints' feast day Mass propers into lit_part_sources.

Schema changes (run once):
  1. ALTER TABLE lit_part_sources MODIFY original_text TEXT NULL
  2. ALTER TABLE lit_part_sources ADD COLUMN month TINYINT UNSIGNED NULL
  3. ALTER TABLE lit_part_sources ADD COLUMN day_of_month TINYINT UNSIGNED NULL
  4. ALTER TABLE lit_part_sources ADD COLUMN feast_title VARCHAR(150) NULL
  5. ALTER TABLE lit_part_sources ADD COLUMN common_of VARCHAR(200) NULL

Then inserts rows from translations/saints_propers.json (produced by
translations/tools/parse_saints.py).

Usage:
    python translations/load_saints.py [--dry-run] [--migrate-only]
    --dry-run      Print rows without touching the database.
    --migrate-only Run schema migration only, no data load.
"""

import json
import sys
import argparse
from pathlib import Path

HERE = Path(__file__).parent


# ── DB connection ─────────────────────────────────────────────────────────

def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine, text

    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
    engine = create_engine(conn_str, future=True)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


# ── Schema migration ─────────────────────────────────────────────────────

MIGRATIONS = [
    # Make original_text nullable so common-only marker rows can omit it
    (
        "MODIFY original_text nullable",
        "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='original_text'",
        lambda row: row[0] == 'YES',
        "ALTER TABLE lit_part_sources MODIFY COLUMN original_text TEXT NULL",
    ),
    # Add month column
    (
        "ADD COLUMN month",
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='month'",
        lambda row: row[0] > 0,
        "ALTER TABLE lit_part_sources ADD COLUMN month TINYINT UNSIGNED NULL",
    ),
    # Add day_of_month column
    (
        "ADD COLUMN day_of_month",
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='day_of_month'",
        lambda row: row[0] > 0,
        "ALTER TABLE lit_part_sources ADD COLUMN day_of_month TINYINT UNSIGNED NULL",
    ),
    # Add feast_title column (for disambiguation when multiple feasts share a date)
    (
        "ADD COLUMN feast_title",
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='feast_title'",
        lambda row: row[0] > 0,
        "ALTER TABLE lit_part_sources ADD COLUMN feast_title VARCHAR(150) NULL",
    ),
    # Add common_of column (reference to the Common used when no proper antiphons exist)
    (
        "ADD COLUMN common_of",
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='common_of'",
        lambda row: row[0] > 0,
        "ALTER TABLE lit_part_sources ADD COLUMN common_of VARCHAR(200) NULL",
    ),
]


def run_migrations(conn, dry_run=False):
    from sqlalchemy import text
    print('Running schema migrations:')
    for (label, check_sql, already_done, alter_sql) in MIGRATIONS:
        row = conn.execute(text(check_sql)).fetchone()
        if already_done(row):
            print(f'  [skip] {label} — already applied')
        else:
            print(f'  [run]  {label}')
            if not dry_run:
                conn.execute(text(alter_sql))
            else:
                print(f'         DRY RUN: {alter_sql}')


# ── Row builder ───────────────────────────────────────────────────────────

YEAR_CYCLE = {'Year A': 1, 'Year B': 2, 'Year C': 0}

SERVICE_PARTS = {
    'entrance_antiphon': 'in',
    'communion_antiphon': 'co',
}


def _base_row(entry):
    return {
        'lit_epoch_slug':  entry.get('slug'),   # proper_of_saints.slug = lit_epoch saint node
        'month':           entry['month'],
        'day_of_month':    entry['day_of_month'],
        'feast_title':     entry.get('title'),
        'cycle_sun':       None, 'cycle_wkday': None,
        'common_of':       None,
        'assignment_authority_code': 'MISSAL',
        'translation_source_code':   'ROMAN_MISSAL_2010_ICEL',
    }


def build_rows(entry):
    """
    Expand one JSON feast entry into lit_part_sources row dicts.

    Three cases:
    - Latin + English: original_text=Latin, vernacular_text=English, original_lang='la'
    - English-only:   original_text=English, vernacular_text=NULL, original_lang='en'
    - Common-only:    original_text=NULL, common_of='From the Common of...', one row per part
    """
    rows = []
    has_antiphons = any(entry.get(f) for f in SERVICE_PARTS)
    common_refs = entry.get('common_of') or []
    common_str = '; '.join(common_refs) if common_refs else None

    if has_antiphons:
        for field, part_code in SERVICE_PARTS.items():
            for antiphon in entry.get(field, []):
                english_only = antiphon.get('english_only', False)
                latin = antiphon.get('latin', '').strip()
                english = antiphon.get('english', '').strip()

                if english_only:
                    if not english:
                        continue
                    original_text, original_lang = english, 'en'
                    vernacular_text, vernacular_lang = None, None
                else:
                    if not latin:
                        continue
                    original_text, original_lang = latin, 'la'
                    vernacular_text = english or None
                    vernacular_lang = 'en' if vernacular_text else None

                year_label = antiphon.get('year')
                row = _base_row(entry)
                row.update({
                    'service_part':    part_code,
                    'original_text':   original_text,
                    'original_lang':   original_lang,
                    'vernacular_text': vernacular_text,
                    'vernacular_lang': vernacular_lang,
                    'text_src':        antiphon.get('citation') or None,
                    'cycle_sun':       YEAR_CYCLE.get(year_label) if year_label else None,
                })
                rows.append(row)

    elif common_str:
        # No proper antiphons; insert one marker row per service part
        for part_code in SERVICE_PARTS.values():
            row = _base_row(entry)
            row.update({
                'service_part':    part_code,
                'original_text':   None,
                'original_lang':   'la',
                'vernacular_text': None,
                'vernacular_lang': None,
                'text_src':        None,
                'common_of':       common_str,
            })
            rows.append(row)

    return rows


# ── Main ─────────────────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO lit_part_sources
        (lit_epoch_slug,
         month, day_of_month, feast_title,
         cycle_sun, cycle_wkday, common_of,
         service_part, original_text, vernacular_text, text_src,
         original_lang, vernacular_lang,
         assignment_authority_code, translation_source_code)
    VALUES
        (:lit_epoch_slug,
         :month, :day_of_month, :feast_title,
         :cycle_sun, :cycle_wkday, :common_of,
         :service_part, :original_text, :vernacular_text, :text_src,
         :original_lang, :vernacular_lang,
         :assignment_authority_code, :translation_source_code)
"""


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Print rows without touching the database')
    parser.add_argument('--migrate-only', action='store_true',
                        help='Run schema migration only, do not insert data')
    args = parser.parse_args()

    json_path = HERE / 'saints_propers.json'
    if not json_path.exists() and not args.migrate_only:
        sys.exit(f'ERROR: {json_path} not found. Run parse_saints.py first.')

    engine = get_engine('jcost') if not args.dry_run else None

    # Schema migration
    if args.dry_run:
        print('DRY RUN — schema migration (not applied):')
        for (label, _, _, alter_sql) in MIGRATIONS:
            print(f'  {alter_sql}')
    else:
        from sqlalchemy import text
        with engine.begin() as conn:
            run_migrations(conn)

    if args.migrate_only:
        print('Migration-only mode; skipping data load.')
        return

    # Build rows
    with open(json_path, encoding='utf-8') as f:
        entries = json.load(f)

    all_rows = []
    skipped = 0
    for entry in entries:
        rows = build_rows(entry)
        if rows:
            all_rows.extend(rows)
        else:
            skipped += 1
            print(f'  WARNING: no antiphons or common reference for '
                  f'{entry["month"]:02d}-{entry["day_of_month"]:02d} {entry["title"]}')

    common_rows = sum(1 for r in all_rows if r.get('common_of'))
    antiphon_rows = len(all_rows) - common_rows
    print(f'\n{json_path.name}: {len(entries)} entries -> {len(all_rows)} rows '
          f'({antiphon_rows} antiphon, {common_rows} common-ref, {skipped} skipped)')

    if args.dry_run:
        print('\n--- DRY RUN: first 5 rows ---')
        for r in all_rows[:5]:
            print(' ', r)
        return

    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(INSERT_SQL), all_rows)
        count = conn.execute(text('SELECT COUNT(*) FROM lit_part_sources')).scalar()
        feast_count = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources WHERE month IS NOT NULL"
        )).scalar()
        print(f'Inserted {len(all_rows)} rows. Total in lit_part_sources: {count} '
              f'(of which feast-day rows: {feast_count})')

        # Quick verification
        sample = conn.execute(text(
            "SELECT month, day_of_month, feast_title, service_part, text_src "
            "FROM lit_part_sources "
            "WHERE month IS NOT NULL "
            "ORDER BY month, day_of_month, feast_title, service_part LIMIT 10"
        )).fetchall()
        print('\nSample feast-day rows:')
        for r in sample:
            print(' ', dict(r._mapping))


if __name__ == '__main__':
    main()
