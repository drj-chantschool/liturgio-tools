#!/usr/bin/env python3
"""
Copy gr_index_entry -> chant_indices (book='GRADUALE'), deriving chant_uuid from
gregobase_id, checked against lit_part_sources.

chant_uuid supersedes gr_index_entry.gregobase_id (not copied). It is
'gregobase:<gregobase_id>', which agrees with every GRADUALE lit_part_sources row
citing the same page and chant (793 rows), except for the hand-checked
MANUAL_LINKS below. Those are index rows with no gregobase_id, or a different
one, whose page carries exactly one lps row of the same part and incipit. There
the loaded lps chant_uuid wins. Every MANUAL_LINKS / DIRECT_OVERRIDES row is
stamped match_status='resolved_with_difficulty' (the review-queue status).

Idempotent: skips any gr_index_entry row already copied (book, id, page).

Usage:
    python scripts/migrate_chant_indices_from_gr_index_2026_10_08.py [--dry-run]
"""

import sys
import argparse

# gr_index_entry.row_id -> lit_part_sources.text_id (checked 2026-10-08)
MANUAL_LINKS = {
    1044: 2347,  # p69  in  Exaudi nos, Domine     (index unmatched)
    1049: 2411,  # p106 in  Exsurge, Domine        (index unmatched)
    1521: 2465,  # p129 of  Eripe me Deus          (index unmatched)
    1522: 2899,  # p151 of  Eripe me Domine        (index unmatched)
    1754: 2965,  # p195 an  Alleluia (ter)         (index says 3004, lps holds 2308)
    1805: 4439,  # p691 an  Qui exspectant Dominum (index unmatched; GS offertory 15499, mode I)
    1820: 2912,  # p159 hy  O Redemptor            (index unmatched)
}

# gr_index_entry.row_id -> chant_uuid, for index rows with no lps row to borrow from
# (checked against the page 2026-10-08)
DIRECT_OVERRIDES = {
    1755: 'gregobase:2308',  # p852 Alleluia (ter): same melody as p195, not 3004
}

INSERT_SQL = """
INSERT INTO chant_indices
    (book, id, section_type, mode, incipit, page, chant_uuid, match_status)
SELECT 'GRADUALE', g.id, g.section_type, g.mode, g.incipit, CAST(g.page AS CHAR),
       CONCAT('gregobase:', g.gregobase_id), g.match_status
FROM gr_index_entry g
WHERE NOT EXISTS (SELECT 1 FROM chant_indices c
                   WHERE c.book = 'GRADUALE' AND c.id = g.id
                     AND c.page <=> CAST(g.page AS CHAR))
ORDER BY g.row_id
"""

MANUAL_SQL = """
UPDATE chant_indices c
JOIN gr_index_entry g ON g.id = c.id AND CAST(g.page AS CHAR) <=> c.page
JOIN lit_part_sources p ON p.text_id = :text_id
SET c.chant_uuid = p.chant_uuid, c.match_status = 'resolved_with_difficulty'
WHERE c.book = 'GRADUALE' AND g.row_id = :row_id
  AND CAST(g.page AS CHAR) IN (p.printed_page_num, p.ref_page_num)
"""

OVERRIDE_SQL = """
UPDATE chant_indices c
JOIN gr_index_entry g ON g.id = c.id AND CAST(g.page AS CHAR) <=> c.page
SET c.chant_uuid = :uuid, c.match_status = 'resolved_with_difficulty'
WHERE c.book = 'GRADUALE' AND g.row_id = :row_id
"""


def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    return create_engine(
        f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}',
        future=True,
    )


def main():
    from sqlalchemy import text
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    with get_engine().connect() as conn:
        n = conn.execute(text(INSERT_SQL)).rowcount
        print(f'[run]  inserted {n} rows')
        for row_id, text_id in MANUAL_LINKS.items():
            m = conn.execute(text(MANUAL_SQL), {'row_id': row_id, 'text_id': text_id}).rowcount
            print(f'[run]  manual link gie {row_id} -> lps {text_id}: {m} row(s)')
        for row_id, uuid in DIRECT_OVERRIDES.items():
            m = conn.execute(text(OVERRIDE_SQL), {'row_id': row_id, 'uuid': uuid}).rowcount
            print(f'[run]  override gie {row_id} -> {uuid}: {m} row(s)')
        total, linked = conn.execute(text(
            "SELECT COUNT(*), COUNT(chant_uuid) FROM chant_indices WHERE book='GRADUALE'"
        )).one()
        src = conn.execute(text('SELECT COUNT(*) FROM gr_index_entry')).scalar()
        print(f'[sum]  chant_indices GRADUALE: {total} rows (gr_index_entry: {src}), '
              f'{linked} with chant_uuid')
        if total != src:
            conn.rollback()
            sys.exit('ERROR: row count mismatch, rolled back')
        if args.dry_run:
            conn.rollback()
            print('[dry-run] rolled back')
        else:
            conn.commit()


if __name__ == '__main__':
    main()
