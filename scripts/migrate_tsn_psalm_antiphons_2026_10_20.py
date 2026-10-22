#!/usr/bin/env python3
"""
2026-10-20: TERCE / SEXT / NONE get three psalm-antiphon parts (ant1-3) beside the single 'ant'.

OCO (Ordo Cantus Officii) gives Terce, Sext and None each three antiphons for the complementary
psalmody (OCO pp699-701, index day code 'H' = every day), e.g. Terce: Clamavi / Auxilium meum /
Laetatus sum. User ruling intake_queue #14. The single 'ant' (Sundays, feasts, seasons) stays.

Idempotent.

Usage:
    python scripts/migrate_tsn_psalm_antiphons_2026_10_20.py [--dry-run]
"""
import argparse
import sys

NOTE = 'Antiphon to psalm {n} of the psalmody (e.g. the complementary psalmody, every day).'
PARTS = [(svc, f'ant{n}', ['First', 'Second', 'Third'][n - 1] + ' Antiphon', 30 + 10 * n, NOTE.format(n=n))
         for svc in ('TERCE', 'SEXT', 'NONE') for n in (1, 2, 3)]


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
    with eng.connect() as c:
        have = {(s, p) for s, p in c.execute(text("SELECT service_code, part_code FROM service_part"))}
    new_parts = [p for p in PARTS if (p[0], p[1]) not in have]
    for p in new_parts:
        print(("would add part: " if a.dry_run else "add part: ") + f"{p[0]}.{p[1]} (order {p[3]})")
    if a.dry_run:
        return
    with eng.begin() as c:
        for s, p, name, order, notes in new_parts:
            c.execute(text("INSERT INTO service_part (service_code, part_code, display_name, display_order, "
                           "is_required, notes, part_class) VALUES (:s, :p, :n, :o, 0, :notes, 'PROPER')"),
                      {"s": s, "p": p, "n": name, "o": order, "notes": notes})
    print("done")


if __name__ == '__main__':
    main()
