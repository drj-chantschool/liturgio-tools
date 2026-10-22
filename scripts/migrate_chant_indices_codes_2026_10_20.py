#!/usr/bin/env python3
"""
2026-10-20: chant_indices gets the liturgical codes some indices print instead of (or besides) a page.

The Ordo Cantus Officii indices key each chant to a day siglum, an hour and a place in the hour
(OCO pp1210-1213, "Sigla in indicibus adhibita"): e.g. 'Super muros tuos | 21/2 | V | M'.
Three nullable columns keep them as printed:
    day_code   VARCHAR(40)  'Tempus / Dies / Festum' column, e.g. 1A1, 21/2, UnM, D-I, Mul TP
    hour_code  VARCHAR(16)  'Officium' column, e.g. 1V, L, Ol, Hm, V, 2V, C
    place_code VARCHAR(40)  'Locus' column, e.g. 1, 2, 3, B, Ma, M ad lib, CV
GR / LH rows keep NULL. sql/make-chant-indices.sql carries the same columns for new installs.

Idempotent (skips columns that exist).

Usage:
    python scripts/migrate_chant_indices_codes_2026_10_20.py [--dry-run]
"""
import argparse
import sys

COLUMNS = [
    ("day_code", "VARCHAR(40) DEFAULT NULL COMMENT 'Day/feast siglum as printed in the index, e.g. OCO 1A1, 21/2, UnM' AFTER `page`"),
    ("hour_code", "VARCHAR(16) DEFAULT NULL COMMENT 'Hour siglum as printed, e.g. OCO 1V, L, Ol, Hm, V, 2V' AFTER `day_code`"),
    ("place_code", "VARCHAR(40) DEFAULT NULL COMMENT 'Place in the hour as printed, e.g. OCO 1, 2, 3, B, Ma, M ad lib' AFTER `hour_code`"),
]


def get_engine(user='jcost', db_name='liturgio'):
    import keyring
    from sqlalchemy import create_engine
    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')
    return create_engine(f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}', future=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    from sqlalchemy import text
    eng = get_engine()
    with eng.begin() as c:
        have = {r[0] for r in c.execute(text("SHOW COLUMNS FROM chant_indices"))}
        for name, ddl in COLUMNS:
            if name in have:
                print(f"= {name} exists")
                continue
            sql = f"ALTER TABLE `chant_indices` ADD COLUMN `{name}` {ddl}"
            print(("would run: " if a.dry_run else "run: ") + sql)
            if not a.dry_run:
                c.execute(text(sql))


if __name__ == '__main__':
    main()
