#!/usr/bin/env python3
"""
2026-10-26: chants.origin_source says where every chant comes from, and the chant_uuid prefix must agree.

Before, local chants (Dr. J transcriptions, placeholders, translations) had origin_source NULL, so
"made here" was only implied by NULL and by the 'local:' prefix of chant_uuid.

1. origin_source = 'LOCAL' on every 'local:' row; the column becomes NOT NULL DEFAULT 'LOCAL', so
   every existing insert path (API, CLI, upload_english_chants, agents' placeholder SQL) needs no change.
2. CHECK chk_chants_origin: a GREGOBASE row is exactly 'gregobase:<origin_id>' (origin_id NOT NULL); a
   LOCAL row is 'local:<uuid>' with origin_id NULL.

Idempotent.

Usage:
    python scripts/migrate_chants_origin_local_2026_10_26.py [--dry-run]
"""
import argparse

from sqlalchemy import text

from liturgio_tools.cli import get_rw_engine

# origin_id IS NOT NULL matters: a CHECK that evaluates to NULL passes, and CONCAT(..., NULL) is NULL.
CHECK = ("(origin_source = 'GREGOBASE' AND origin_id IS NOT NULL AND chant_uuid = CONCAT('gregobase:', origin_id)) "
         "OR (origin_source = 'LOCAL' AND chant_uuid LIKE 'local:%' AND origin_id IS NULL)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    with get_rw_engine().connect() as c:
        bad = c.execute(text(
            "SELECT chant_uuid, origin_source, origin_id FROM chants WHERE NOT ("
            "(origin_source = 'GREGOBASE' AND chant_uuid = CONCAT('gregobase:', origin_id)) "
            "OR (origin_source IS NULL AND chant_uuid LIKE 'local:%' AND origin_id IS NULL) "
            "OR (origin_source = 'LOCAL' AND chant_uuid LIKE 'local:%' AND origin_id IS NULL))")).all()
        if bad:
            raise SystemExit(f'{len(bad)} rows fit neither shape, fix them first: {bad[:10]}')
        n = c.execute(text("SELECT COUNT(*) FROM chants WHERE origin_source IS NULL")).scalar()
        has_check = c.execute(text(
            "SELECT 1 FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
            "AND table_name = 'chants' AND constraint_name = 'chk_chants_origin'")).first()
        print(f'rows to mark LOCAL: {n}; check constraint present: {bool(has_check)}')
        if a.dry_run:
            return
        c.execute(text("UPDATE chants SET origin_source = 'LOCAL' WHERE origin_source IS NULL"))
        c.commit()
        c.execute(text(
            "ALTER TABLE chants MODIFY origin_source varchar(20) NOT NULL DEFAULT 'LOCAL' "
            "COMMENT 'GREGOBASE = imported by chants_import; LOCAL = made here (chant_uuid local:<uuid>)'"))
        if has_check:   # re-create, so a re-run applies the current definition
            c.execute(text("ALTER TABLE chants DROP CHECK chk_chants_origin"))
        c.execute(text(f"ALTER TABLE chants ADD CONSTRAINT chk_chants_origin CHECK ({CHECK})"))
        print('done')


if __name__ == '__main__':
    main()
