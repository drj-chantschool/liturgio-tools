#!/usr/bin/env python
"""Office-of-the-Hours scaffolding for the Liber Hymnarius (LH) page intake (2026-10-13).

1. lit_part_sources.rank_code (FK p_lit_rank) — some Office assignments differ by the
   celebration's rank ("in memoriis", "in sollemnitatibus et festis"). Added to the
   uq_lps_slot key through a VIRTUAL generated slot_rank column (STORED + FK → MySQL 1215).
2. service_part rows for the Office hours the LH fills: INVITATORY, OFFREAD, LAUDS,
   TERCE, SEXT, NONE, VESPERS1 (I Vespers, hung off the feast's own epoch), COMPLINE.
   VESPERS (already present) = II Vespers.
3. p_assignment_authority 'LIBER_HYMNARIUS'.
4. LH Commons that have no GR counterpart epoch: COM-BMV-SAB, COM-APOST, COM-MART-PLUR,
   COM-MART-UNO, COM-SANCT-VIR, COM-MON, plus ritual 'officium-defunctorum'.

Idempotent. Dry unless --commit.
    .venv\\Scripts\\python.exe liturgio-tools\\scripts\\migrate_lh_office_2026_10_13.py --commit
"""
import argparse
from pathlib import Path

import keyring
from sqlalchemy import create_engine, text

PARTS = [
    # service_code, part_code, display_name, display_order, part_class
    ("INVITATORY", "invan",  "Invitatory antiphon",               10, "PROPER"),
    ("INVITATORY", "invps",  "Invitatory psalm (Ps 94 tone)",     20, "COMMON"),
    ("OFFREAD",    "hymn",   "Hymn",                              20, "PROPER"),
    ("OFFREAD",    "resp",   "Responsory after the (first) reading", 100, "PROPER"),
    ("OFFREAD",    "resp2",  "Responsory after the second reading", 110, "PROPER"),
    ("OFFREAD",    "tedeum", "Te Deum",                          150, "COMMON"),
    ("LAUDS",      "hymn",   "Hymn",                              20, "PROPER"),
    ("TERCE",      "hymn",   "Hymn",                              20, "PROPER"),
    ("SEXT",       "hymn",   "Hymn",                              20, "PROPER"),
    ("NONE",       "hymn",   "Hymn",                              20, "PROPER"),
    ("VESPERS1",   "hymn",   "Hymn",                              20, "PROPER"),
    ("VESPERS1",   "resp",   "Responsory",                       100, "PROPER"),
    ("COMPLINE",   "hymn",   "Hymn",                              20, "PROPER"),
]

EPOCHS = [
    # slug, kind, title, parent, notes
    ("COM-BMV-SAB",   "common", "Memorial of the BVM on Saturday", None, "LH printed p. 263"),
    ("COM-APOST",     "common", "Common of Apostles", None,
     "LH printed p. 266 (the LH does not split Apostles by Paschaltide as the GR does)"),
    ("COM-MART-PLUR", "common", "Common of Several Martyrs", None, "LH printed p. 273"),
    ("COM-MART-UNO",  "common", "Common of One Martyr", None, "LH printed p. 280"),
    ("COM-SANCT-VIR", "common", "Pro sanctis viris", "COM-SANCT", "LH printed p. 311"),
    ("COM-MON",       "common", "Common of Holy Monks and Nuns (O.S.B.)", None,
     "LH Proprium monasticum printed p. 537; rows carry jurisdiction OSB"),
    ("officium-defunctorum", "ritual", "Office for the Dead (Officium defunctorum)", None,
     "LH printed p. 328"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--do", action="store_true", help="target the DigitalOcean copy as doadmin")
    a = ap.parse_args()
    if a.do:
        pw = keyring.get_password("liturgio-mysql-do", "doadmin")
        ca = Path(__file__).resolve().parents[2] / "ca-certificate.crt"
        eng = create_engine(
            f"mysql+mysqlconnector://doadmin:{pw}@db-mysql-nyc3-73323-do-user-34645648-0.d.db."
            "ondigitalocean.com:25060/liturgio", future=True,
            connect_args={"ssl_ca": str(ca), "ssl_verify_cert": True,
                          # MySQL 8.4 rejects FKs to non-unique keys by default
                          "init_command": "SET SESSION restrict_fk_on_non_standard_key=OFF"})
    else:
        pw = keyring.get_password("liturgio-mysql", "jcost")
        eng = create_engine(f"mysql+mysqlconnector://jcost:{pw}@localhost:3306/liturgio", future=True)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text("SHOW COLUMNS FROM lit_part_sources"))}
        uq = [r for r in c.execute(text("SHOW INDEX FROM lit_part_sources WHERE Key_name='uq_lps_slot'"))]
        uq_cols = [r[4] for r in uq]
        stmts = []
        if "rank_code" not in cols:
            stmts += [
                "ALTER TABLE lit_part_sources ADD COLUMN rank_code VARCHAR(40) NULL "
                "COMMENT 'celebration rank this assignment applies to (NULL = any rank)' AFTER wknum_mod_2",
                "ALTER TABLE lit_part_sources ADD CONSTRAINT fk_lps_rank FOREIGN KEY (rank_code) "
                "REFERENCES p_lit_rank (rank_code) ON DELETE RESTRICT ON UPDATE CASCADE",
            ]
        if "slot_rank" not in cols:
            stmts.append("ALTER TABLE lit_part_sources ADD COLUMN slot_rank VARCHAR(40) "
                         "GENERATED ALWAYS AS (coalesce(rank_code,_utf8mb4'')) VIRTUAL")
        elif "slot_rank" not in uq_cols:
            stmts.append("ALTER TABLE lit_part_sources MODIFY COLUMN slot_rank VARCHAR(40) "
                         "GENERATED ALWAYS AS (coalesce(rank_code,_utf8mb4'')) VIRTUAL")
        if "slot_rank" not in uq_cols:
            stmts.append(
                "ALTER TABLE lit_part_sources DROP INDEX uq_lps_slot, ADD UNIQUE KEY uq_lps_slot "
                "(jurisdiction, lit_epoch_slug, part_id, slot_book, slot_auth, slot_csun, slot_cwkday, "
                "slot_wkday, slot_wm4, slot_wm2, slot_rank, slot_tsrc, option_num, slot_chant)")
        for s in stmts:
            print("DDL:", s[:140])
            if a.commit:
                c.execute(text(s))
        if a.commit:
            c.commit()

    with eng.begin() as c:
        n = 0
        for svc, code, name, order, cls in PARTS:
            exists = c.execute(text("SELECT 1 FROM service_part WHERE service_code=:s AND part_code=:p"),
                               {"s": svc, "p": code}).first()
            if not exists:
                n += 1
                print(f"service_part + {svc}.{code}")
                c.execute(text("INSERT INTO service_part (service_code, part_code, display_name, "
                               "display_order, is_required, part_class) VALUES (:s,:p,:n,:o,0,:c)"),
                          {"s": svc, "p": code, "n": name, "o": order, "c": cls})
        if not c.execute(text("SELECT 1 FROM p_assignment_authority WHERE authority_code='LIBER_HYMNARIUS'")).first():
            print("p_assignment_authority + LIBER_HYMNARIUS")
            c.execute(text("INSERT INTO p_assignment_authority (authority_code, display_name, sort_order, is_active, notes) "
                           "VALUES ('LIBER_HYMNARIUS','Liber Hymnarius (1983)',60,1,"
                           "'Office hymns, invitatories and responsories as assigned by the Liber Hymnarius')"))
        base = c.execute(text("SELECT COALESCE(MAX(sort_order),0) FROM lit_epoch WHERE kind IN ('common','ritual')")).scalar()
        for i, (slug, kind, title, parent, notes) in enumerate(EPOCHS, start=1):
            if not c.execute(text("SELECT 1 FROM lit_epoch WHERE slug=:s"), {"s": slug}).first():
                print(f"lit_epoch + {slug}")
                c.execute(text("INSERT INTO lit_epoch (slug, kind, title, sort_order, notes) "
                               "VALUES (:s,:k,:t,:o,:n)"),
                          {"s": slug, "k": kind, "t": title, "o": base + i, "n": notes})
            if parent:
                c.execute(text("INSERT IGNORE INTO lit_epoch_tree (parent_slug, child_slug) VALUES (:p,:c)"),
                          {"p": parent, "c": slug})
        if not a.commit:
            print("(dry run — rolling back; pass --commit)")
            c.rollback()


if __name__ == "__main__":
    main()
