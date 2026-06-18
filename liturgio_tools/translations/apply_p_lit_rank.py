#!/usr/bin/env python3
"""
Step 1 migration: create p_lit_rank lookup table, seed it, set all
liturgical_day.lit_rank to 'UNDETERMINED', and add the FK + column default.

All operations are idempotent — safe to re-run.

Usage:
    python apply_p_lit_rank.py [--dry-run]
    --dry-run   Print all SQL that would run without touching the database.
"""

import sys
import argparse

# ---------------------------------------------------------------------------
# Seed data: 13-level Roman Rite Table of Liturgical Days (GNLYC 1969) + sentinel
# (rank_code, display_name, sort_order, can_be_transferred, can_be_impeded)
# can_* flags are best-effort and themselves subject to review; NULL = undetermined.
# ---------------------------------------------------------------------------
SEED_ROWS = [
    ('TRIDUUM',            'Easter Triduum',                                                                                                                                                                                      10,   0,    0),
    ('PRINCIPAL_TEMPORAL', 'Principal temporal day / solemnity of the Lord (Nativity, Epiphany, Ascension, Pentecost; Sundays of Advent, Lent, Easter; Ash Wednesday; weekdays of Holy Week; days within the Octave of Easter)',  20,   0,    0),
    ('SOLEMNITY',          'Solemnity (General Calendar) / All Souls',                                                                                                                                                             30,   1,    1),
    ('PROPER_SOLEMNITY',   'Proper solemnity (patron, dedication, title, founder)',                                                                                                                                                 40,   1,    1),
    ('FEAST_OF_THE_LORD',  'Feast of the Lord (General Calendar)',                                                                                                                                                                  50,   0,    1),
    ('SUNDAY',             'Sunday of Christmas season or Ordinary Time',                                                                                                                                                           60,   0,    1),
    ('FEAST',              'Feast (General Calendar)',                                                                                                                                                                               70,   0,    1),
    ('PROPER_FEAST',       'Proper feast',                                                                                                                                                                                           80,   0,    1),
    ('PRIVILEGED_WEEKDAY', 'Privileged weekday (Advent Dec 17-24; Octave of the Nativity; weekdays of Lent)',                                                                                                                       90,   0,    1),
    ('MEMORIAL',           'Obligatory memorial (General Calendar)',                                                                                                                                                               100,   0,    1),
    ('PROPER_MEMORIAL',    'Proper obligatory memorial',                                                                                                                                                                           110,   0,    1),
    ('OPTIONAL_MEMORIAL',  'Optional memorial',                                                                                                                                                                                    120,   0,    1),
    ('WEEKDAY',            'Weekday / feria',                                                                                                                                                                                      130,   0,    1),
    ('UNDETERMINED',       'Undetermined — pending review',                                                                                                                                                                  9000, None, None),
]

CREATE_P_LIT_RANK = """
CREATE TABLE IF NOT EXISTS p_lit_rank (
    rank_code           VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name        VARCHAR(255) NOT NULL,
    sort_order          SMALLINT UNSIGNED NOT NULL,
    can_be_transferred  TINYINT NULL,
    can_be_impeded      TINYINT NULL,
    is_active           TINYINT NOT NULL DEFAULT 1,
    notes               VARCHAR(500) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

INSERT_RANK = """
INSERT INTO p_lit_rank (rank_code, display_name, sort_order, can_be_transferred, can_be_impeded)
VALUES (:rank_code, :display_name, :sort_order, :can_be_transferred, :can_be_impeded)
ON DUPLICATE KEY UPDATE
    display_name        = VALUES(display_name),
    sort_order          = VALUES(sort_order),
    can_be_transferred  = VALUES(can_be_transferred),
    can_be_impeded      = VALUES(can_be_impeded)
"""

UPDATE_LIT_DAY = "UPDATE liturgical_day SET lit_rank = 'UNDETERMINED'"

# INFORMATION_SCHEMA checks (idempotency guards)
CHECK_COLUMN_DEFAULT = (
    "SELECT COLUMN_DEFAULT, IS_NULLABLE "
    "FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = 'liturgio' "
    "  AND TABLE_NAME   = 'liturgical_day' "
    "  AND COLUMN_NAME  = 'lit_rank'"
)

CHECK_FK = (
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
    "WHERE CONSTRAINT_SCHEMA = 'liturgio' "
    "  AND TABLE_NAME         = 'liturgical_day' "
    "  AND CONSTRAINT_NAME    = 'fk_litday_rank' "
    "  AND CONSTRAINT_TYPE    = 'FOREIGN KEY'"
)

ALTER_MODIFY_COLUMN = (
    "ALTER TABLE liturgical_day "
    "MODIFY COLUMN lit_rank VARCHAR(40) NOT NULL DEFAULT 'UNDETERMINED'"
)

ALTER_ADD_FK = (
    "ALTER TABLE liturgical_day "
    "ADD CONSTRAINT fk_litday_rank "
    "FOREIGN KEY (lit_rank) REFERENCES p_lit_rank(rank_code) "
    "ON UPDATE CASCADE ON DELETE RESTRICT"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine, text
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}. '
                 'Store it with: keyring set liturgio-mysql jcost')
    conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
    engine = create_engine(conn_str, future=True)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true',
                        help='Print SQL without touching the database')
    args = parser.parse_args()

    if args.dry_run:
        print('=== DRY RUN — no changes will be made ===\n')
        print('[1] CREATE TABLE IF NOT EXISTS p_lit_rank ...')
        print('[2] INSERT INTO p_lit_rank ... ON DUPLICATE KEY UPDATE  '
              f'({len(SEED_ROWS)} rows)')
        print(f'[3] {UPDATE_LIT_DAY}')
        print(f'[4] Check INFORMATION_SCHEMA; if needed: {ALTER_MODIFY_COLUMN}')
        print(f'[5] Check INFORMATION_SCHEMA; if needed: {ALTER_ADD_FK}')
        return

    from sqlalchemy import text
    engine = get_engine('jcost')

    with engine.begin() as conn:
        # 1. Create p_lit_rank (idempotent via IF NOT EXISTS)
        conn.execute(text(CREATE_P_LIT_RANK))
        print('[1] p_lit_rank table ensured.')

        # 1b. Widen display_name if it was previously created as VARCHAR(120)
        dn_len = conn.execute(text(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='p_lit_rank' "
            "  AND COLUMN_NAME='display_name'"
        )).scalar()
        if dn_len is not None and int(dn_len) < 255:
            conn.execute(text(
                'ALTER TABLE p_lit_rank MODIFY COLUMN display_name VARCHAR(255) NOT NULL'
            ))
            print(f'[1b] p_lit_rank.display_name widened from VARCHAR({dn_len}) to VARCHAR(255).')
        else:
            print('[1b] p_lit_rank.display_name is already VARCHAR(255) — skipped.')

        # 2. Seed / upsert rows
        seed_dicts = [
            {
                'rank_code':          r[0],
                'display_name':       r[1],
                'sort_order':         r[2],
                'can_be_transferred': r[3],
                'can_be_impeded':     r[4],
            }
            for r in SEED_ROWS
        ]
        conn.execute(text(INSERT_RANK), seed_dicts)
        count = conn.execute(text('SELECT COUNT(*) FROM p_lit_rank')).scalar()
        print(f'[2] p_lit_rank seeded. Row count: {count}')

        # 3. Set all liturgical_day.lit_rank to UNDETERMINED
        result = conn.execute(text(UPDATE_LIT_DAY))
        print(f'[3] liturgical_day rows updated: {result.rowcount}')

        # 4. MODIFY column to add NOT NULL DEFAULT (idempotent check)
        col_info = conn.execute(text(CHECK_COLUMN_DEFAULT)).fetchone()
        needs_modify = True
        if col_info is not None:
            current_default = col_info[0]
            current_nullable = col_info[1]
            if current_default == 'UNDETERMINED' and current_nullable == 'NO':
                needs_modify = False
        if needs_modify:
            conn.execute(text(ALTER_MODIFY_COLUMN))
            print('[4] lit_rank column modified: NOT NULL DEFAULT UNDETERMINED.')
        else:
            print('[4] lit_rank column already NOT NULL DEFAULT UNDETERMINED — skipped.')

        # 5. Add FK constraint (idempotent check)
        fk_count = conn.execute(text(CHECK_FK)).scalar()
        if fk_count == 0:
            conn.execute(text(ALTER_ADD_FK))
            print('[5] FK fk_litday_rank added.')
        else:
            print('[5] FK fk_litday_rank already exists — skipped.')

    # ---------------------------------------------------------------------------
    # Verification (read-only, separate connection so no accidental transaction)
    # ---------------------------------------------------------------------------
    print('\n=== Verification ===')
    from sqlalchemy import text as t
    with engine.connect() as conn:
        n = conn.execute(t('SELECT COUNT(*) FROM p_lit_rank')).scalar()
        print(f'\nSELECT COUNT(*) FROM p_lit_rank;  -> {n}')

        rows = conn.execute(t(
            'SELECT rank_code, sort_order FROM p_lit_rank ORDER BY sort_order'
        )).fetchall()
        print('\nSELECT rank_code, sort_order FROM p_lit_rank ORDER BY sort_order;')
        for r in rows:
            print(f'  {r[0]:<22}  {r[1]}')

        dist = conn.execute(t(
            'SELECT lit_rank, COUNT(*) AS cnt FROM liturgical_day GROUP BY lit_rank'
        )).fetchall()
        print('\nSELECT lit_rank, COUNT(*) FROM liturgical_day GROUP BY lit_rank;')
        for r in dist:
            print(f'  {r[0]}  ->  {r[1]}')

        fk_rows = conn.execute(t(
            "SELECT CONSTRAINT_NAME, TABLE_NAME, CONSTRAINT_TYPE "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = 'liturgio' "
            "  AND CONSTRAINT_NAME   = 'fk_litday_rank'"
        )).fetchall()
        print('\nINFORMATION_SCHEMA check for fk_litday_rank:')
        if fk_rows:
            for r in fk_rows:
                print(f'  CONSTRAINT_NAME={r[0]}  TABLE_NAME={r[1]}  TYPE={r[2]}')
        else:
            print('  *** NOT FOUND — FK was not created ***')

    print('\nDone.')


if __name__ == '__main__':
    main()
