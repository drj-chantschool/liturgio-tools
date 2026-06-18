#!/usr/bin/env python3
"""
Roadmap step 6c: Christmas Mass sub-epoch nodes + sunset old columns.

Three phases:
  1. Insert four Christmas Mass nodes (kind='mass') under NAT-DAY-00-0 in
     lit_epoch / lit_epoch_tree, then update all 8 lit_part_sources rows
     (4 masses × introit + communion) to point to the correct mass slug.
  2. Fix the 4th Sunday of Advent: set lit_epoch_slug='ADV-II', wkday=1
     (no dedicated epoch node — it's a movable Sunday over the O-antiphon days).
  3. Drop season, subseason, wknum from lit_part_sources (replaced by
     lit_epoch_slug). wkday is kept — it encodes day-of-week within the
     epoch period, needed when the epoch is a week or subseason.

Idempotent: INSERT IGNORE on new rows, column checks before ALTER.

Usage:
    python scripts/migrate_step6c_christmas_masses_and_drop_cols.py [--dry-run]
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


CHRISTMAS_MASSES = [
    {
        'slug': 'NAT-DAY-00-0-vigil',
        'kind': 'mass',
        'title': 'Christmas — Vigil Mass',
        'rank_code': 'PRINCIPAL_TEMPORAL',
        'season': 'NAT', 'subseason': 'DAY',
        'wknum': 0, 'seq': 0,
        'sort_order': 29,
        'wkday_in_lps': 0,
    },
    {
        'slug': 'NAT-DAY-00-0-night',
        'kind': 'mass',
        'title': 'Christmas — Mass at Night',
        'rank_code': 'PRINCIPAL_TEMPORAL',
        'season': 'NAT', 'subseason': 'DAY',
        'wknum': 0, 'seq': 1,
        'sort_order': 29,
        'wkday_in_lps': 1,
    },
    {
        'slug': 'NAT-DAY-00-0-dawn',
        'kind': 'mass',
        'title': 'Christmas — Mass at Dawn',
        'rank_code': 'PRINCIPAL_TEMPORAL',
        'season': 'NAT', 'subseason': 'DAY',
        'wknum': 0, 'seq': 2,
        'sort_order': 29,
        'wkday_in_lps': 2,
    },
    {
        'slug': 'NAT-DAY-00-0-day',
        'kind': 'mass',
        'title': 'Christmas — Mass During the Day',
        'rank_code': 'PRINCIPAL_TEMPORAL',
        'season': 'NAT', 'subseason': 'DAY',
        'wknum': 0, 'seq': 3,
        'sort_order': 29,
        'wkday_in_lps': 3,
    },
]


def phase1_christmas_masses(conn, dry_run):
    """Insert Christmas Mass nodes and fix lit_part_sources slugs."""
    from sqlalchemy import text

    print('\n--- Phase 1: Christmas Mass sub-epoch nodes ---')

    for m in CHRISTMAS_MASSES:
        existing = conn.execute(text(
            "SELECT slug FROM lit_epoch WHERE slug = :s"
        ), {'s': m['slug']}).fetchone()

        if existing:
            print(f"[skip] lit_epoch {m['slug']} already exists")
        else:
            print(f"[{'dry' if dry_run else 'run'}]  INSERT lit_epoch {m['slug']}")
            if not dry_run:
                conn.execute(text("""
                    INSERT INTO lit_epoch (slug, kind, title, rank_code,
                                          season, subseason, wknum, seq,
                                          sort_order)
                    VALUES (:slug, :kind, :title, :rank_code,
                            :season, :subseason, :wknum, :seq,
                            :sort_order)
                """), {k: m[k] for k in (
                    'slug', 'kind', 'title', 'rank_code',
                    'season', 'subseason', 'wknum', 'seq', 'sort_order',
                )})

        edge_exists = conn.execute(text(
            "SELECT 1 FROM lit_epoch_tree "
            "WHERE parent_slug = 'NAT-DAY-00-0' AND child_slug = :c"
        ), {'c': m['slug']}).fetchone()
        if edge_exists:
            print(f"[skip] tree edge NAT-DAY-00-0 → {m['slug']} already exists")
        else:
            print(f"[{'dry' if dry_run else 'run'}]  INSERT tree edge NAT-DAY-00-0 → {m['slug']}")
            if not dry_run:
                conn.execute(text(
                    "INSERT INTO lit_epoch_tree (parent_slug, child_slug) "
                    "VALUES ('NAT-DAY-00-0', :c)"
                ), {'c': m['slug']})

    for m in CHRISTMAS_MASSES:
        affected = conn.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources "
            "WHERE season='NAT' AND subseason='DAY' AND wknum=0 AND wkday=:w"
        ), {'w': m['wkday_in_lps']}).scalar()
        print(f"[{'dry' if dry_run else 'run'}]  UPDATE {affected} lit_part_sources rows "
              f"(wkday={m['wkday_in_lps']}) → {m['slug']}")
        if not dry_run:
            conn.execute(text(
                "UPDATE lit_part_sources SET lit_epoch_slug = :slug "
                "WHERE season='NAT' AND subseason='DAY' AND wknum=0 AND wkday=:w"
            ), {'slug': m['slug'], 'w': m['wkday_in_lps']})


def phase2_adv_sunday(conn, dry_run):
    """Fix the 4th Sunday of Advent: epoch=ADV-II, wkday=1."""
    from sqlalchemy import text

    print('\n--- Phase 2: 4th Sunday of Advent (ADV-II + wkday=1) ---')

    # The 4th Sunday is a movable observance over the Dec 17-24 days.
    # It has no dedicated epoch node — the correct encoding is
    # lit_epoch_slug='ADV-II' (the subseason) + wkday=1 (Sunday).
    affected = conn.execute(text(
        "SELECT COUNT(*) FROM lit_part_sources "
        "WHERE text_id IN (150, 151) AND lit_epoch_slug IS NULL"
    )).scalar()
    print(f"[{'dry' if dry_run else 'run'}]  UPDATE {affected} lit_part_sources rows "
          "→ lit_epoch_slug='ADV-II', wkday=1")
    if not dry_run:
        conn.execute(text(
            "UPDATE lit_part_sources "
            "SET lit_epoch_slug = 'ADV-II', wkday = 1 "
            "WHERE text_id IN (150, 151) AND lit_epoch_slug IS NULL"
        ))


def phase3_drop_columns(conn, dry_run):
    """Drop season, subseason, wknum from lit_part_sources (wkday kept)."""
    from sqlalchemy import text

    print('\n--- Phase 3: drop season/subseason/wknum from lit_part_sources ---')

    # Safety check: any seasonal rows still without a slug?
    orphans = conn.execute(text("""
        SELECT COUNT(*) FROM lit_part_sources
        WHERE (season IS NOT NULL OR subseason IS NOT NULL
               OR wknum IS NOT NULL)
          AND lit_epoch_slug IS NULL
    """)).scalar()
    if orphans:
        print(f"[ABORT] {orphans} row(s) still have season/subseason/wknum "
              "but no lit_epoch_slug — cannot safely drop columns.")
        return False

    for col in ('season', 'subseason', 'wknum'):
        if not column_exists(conn, 'lit_part_sources', col):
            print(f"[skip] column {col} already dropped")
            continue
        print(f"[{'dry' if dry_run else 'run'}]  DROP COLUMN {col}")
        if not dry_run:
            conn.execute(text(
                f"ALTER TABLE lit_part_sources DROP COLUMN {col}"
            ))

    return True


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    engine = get_engine()
    from sqlalchemy import text

    print('=== migrate_step6c: Christmas Masses + sunset old columns ===')
    if args.dry_run:
        print('DRY RUN — no changes will be made.')

    if args.dry_run:
        with engine.connect() as conn:
            phase1_christmas_masses(conn, dry_run=True)
            phase2_adv_sunday(conn, dry_run=True)
            phase3_drop_columns(conn, dry_run=True)
    else:
        with engine.begin() as conn:
            phase1_christmas_masses(conn, dry_run=False)
        with engine.begin() as conn:
            phase2_adv_sunday(conn, dry_run=False)
        with engine.begin() as conn:
            phase3_drop_columns(conn, dry_run=False)

    with engine.connect() as conn:
        # Verify
        unresolved = conn.execute(text("""
            SELECT COUNT(*) FROM lit_part_sources WHERE lit_epoch_slug IS NULL
        """)).scalar()
        total = conn.execute(text("SELECT COUNT(*) FROM lit_part_sources")).scalar()
        have_slug = total - unresolved
        print(f'\n=== Summary: {have_slug}/{total} lit_part_sources rows have lit_epoch_slug '
              f'({unresolved} NULL — feast rows awaiting proper_of_saints) ===')

        masses = conn.execute(text(
            "SELECT slug, title FROM lit_epoch "
            "WHERE slug LIKE 'NAT-DAY-00-0-%' ORDER BY seq"
        )).fetchall()
        if masses:
            print('\nChristmas Mass nodes:')
            for r in masses:
                print(f'  {r[0]}: {r[1]}')


if __name__ == '__main__':
    main()
