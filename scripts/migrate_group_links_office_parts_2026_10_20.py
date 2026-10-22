#!/usr/bin/env python3
"""
2026-10-20: group-level chant links + Office antiphon parts (OCO intake; user rulings 2026-10-20).

1. chant_group_id BIGINT UNSIGNED NULL (FK chant_group) on chant_indices and lit_part_sources.
   A row may name a chant_group without choosing an edition: chant_uuid NULL, chant_group_id set.
   lit_part_sources.slot_chant (part of uq_lps_slot) becomes
   COALESCE(chant_uuid, CONCAT('group:', chant_group_id), '') so two group-only options in one slot
   don't collide; existing rows keep the same value (their chant_group_id is NULL).
2. service_part: the Office hours get their antiphon / short-responsory parts, mirroring the
   VESPERS codes (ant1-3 at 30/50/70, resp at 100, gospel-canticle antiphon at 110), plus a new
   MIDDAY service for the hora media psalmody (OCO 'Hm'); TERCE/SEXT/NONE get the single
   antiphon used on Sundays and feasts.

Idempotent.

Usage:
    python scripts/migrate_group_links_office_parts_2026_10_20.py [--dry-run]
"""
import argparse
import sys

PARTS = [  # service_code, part_code, display_name, display_order, notes
    ('LAUDS', 'ant1', 'First Antiphon', 30, 'Antiphon to the first psalm.'),
    ('LAUDS', 'ant2', 'Second Antiphon', 50, 'Antiphon to the Old Testament canticle.'),
    ('LAUDS', 'ant3', 'Third Antiphon', 70, 'Antiphon to the third psalm.'),
    ('LAUDS', 'resp', 'Responsory', 100, 'Short responsory (responsorium breve).'),
    ('LAUDS', 'antB', 'Antiphon to the Benedictus', 110, None),
    ('VESPERS1', 'ant1', 'First Antiphon', 30, 'Antiphon to the first psalm.'),
    ('VESPERS1', 'ant2', 'Second Antiphon', 50, 'Antiphon to the second psalm.'),
    ('VESPERS1', 'ant3', 'Third Antiphon', 70, 'Antiphon to the New Testament canticle.'),
    ('VESPERS1', 'antM', 'Antiphon to the Magnificat', 110, None),
    ('OFFREAD', 'ant1', 'First Antiphon', 30, 'Antiphon to the first psalm.'),
    ('OFFREAD', 'ant2', 'Second Antiphon', 50, 'Antiphon to the second psalm.'),
    ('OFFREAD', 'ant3', 'Third Antiphon', 70, 'Antiphon to the third psalm.'),
    ('VIGILIAE', 'antcv', 'Antiphon to the vigil canticles', 60, 'Antiphona ad cantica vigiliarum.'),
    ('TERCE', 'ant', 'Antiphon', 30, 'Single antiphon for the psalmody (Sundays, feasts, seasons).'),
    ('SEXT', 'ant', 'Antiphon', 30, 'Single antiphon for the psalmody (Sundays, feasts, seasons).'),
    ('NONE', 'ant', 'Antiphon', 30, 'Single antiphon for the psalmody (Sundays, feasts, seasons).'),
    ('MIDDAY', 'ant1', 'First Antiphon', 30, 'Hora media psalmody, whichever of Terce/Sext/None is said.'),
    ('MIDDAY', 'ant2', 'Second Antiphon', 50, 'Hora media psalmody, whichever of Terce/Sext/None is said.'),
    ('MIDDAY', 'ant3', 'Third Antiphon', 70, 'Hora media psalmody, whichever of Terce/Sext/None is said.'),
    ('COMPLINE', 'ant1', 'First Antiphon', 30, 'Antiphon to the first psalm.'),
    ('COMPLINE', 'ant2', 'Second Antiphon', 50, 'Antiphon to the second psalm.'),
    ('COMPLINE', 'resp', 'Responsory', 100, 'Short responsory (In manus tuas).'),
    ('COMPLINE', 'antN', 'Antiphon to the Nunc dimittis', 110, None),
    ('COMPLINE', 'antMar', 'Final antiphon of the Blessed Virgin Mary', 130, None),
]

SLOT_NEW = "coalesce(`chant_uuid`,concat(_utf8mb4'group:',`chant_group_id`),_utf8mb4'')"
UQ = ("ALTER TABLE lit_part_sources ADD UNIQUE KEY `uq_lps_slot` (`jurisdiction`,`lit_epoch_slug`,`part_id`,"
      "`slot_book`,`slot_auth`,`slot_csun`,`slot_cwkday`,`slot_wkday`,`slot_wm4`,`slot_wm2`,`slot_rank`,"
      "`slot_tsrc`,`option_num`,`slot_chant`)")


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
    stmts = []
    with eng.connect() as c:
        for t in ('chant_indices', 'lit_part_sources'):
            cols = {r[0] for r in c.execute(text(f"SHOW COLUMNS FROM {t}"))}
            if 'chant_group_id' not in cols:
                after = 'chant_uuid'
                stmts.append(f"ALTER TABLE {t} ADD COLUMN `chant_group_id` BIGINT UNSIGNED DEFAULT NULL "
                             f"COMMENT 'chant_group matched when no single edition is chosen (chant_uuid NULL)' "
                             f"AFTER `{after}`, ADD KEY `idx_{t[:3]}_chant_group` (`chant_group_id`), "
                             f"ADD CONSTRAINT `fk_{'ci' if t == 'chant_indices' else 'lps'}_chant_group` "
                             f"FOREIGN KEY (`chant_group_id`) REFERENCES `chant_group` (`chant_group_id`) "
                             f"ON DELETE RESTRICT ON UPDATE CASCADE")
        expr = c.execute(text("SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS WHERE "
                              "TABLE_SCHEMA=DATABASE() AND TABLE_NAME='lit_part_sources' AND COLUMN_NAME='slot_chant'")).scalar()
        if 'group:' not in (expr or ''):
            stmts += ["ALTER TABLE lit_part_sources DROP INDEX `uq_lps_slot`",
                      f"ALTER TABLE lit_part_sources MODIFY `slot_chant` varchar(64) GENERATED ALWAYS AS ({SLOT_NEW}) VIRTUAL",
                      UQ]
        have = {(s, p) for s, p in c.execute(text("SELECT service_code, part_code FROM service_part"))}
    new_parts = [p for p in PARTS if (p[0], p[1]) not in have]
    for s in stmts:
        print(("would run: " if a.dry_run else "run: ") + s[:160])
    for p in new_parts:
        print(("would add part: " if a.dry_run else "add part: ") + f"{p[0]}.{p[1]}")
    if a.dry_run:
        return
    with eng.begin() as c:
        for s in stmts:
            c.execute(text(s))
        for s, p, name, order, notes in new_parts:
            c.execute(text("INSERT INTO service_part (service_code, part_code, display_name, display_order, "
                           "is_required, notes, part_class) VALUES (:s, :p, :n, :o, 0, :notes, 'PROPER')"),
                      {"s": s, "p": p, "n": name, "o": order, "notes": notes})
    print("done")


if __name__ == '__main__':
    main()
