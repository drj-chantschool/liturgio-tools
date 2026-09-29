#!/usr/bin/env python3
"""
Schema cleanup migration — 2026-07-20.

Applies the repairs identified in the 2026-07-19 schema review (plan file
take-a-look-at-polymorphic-pike.md). Run with --dry-run first; all DML is
wrapped in a single transaction that is rolled back on dry-run.

Steps (executed in this order so later steps can depend on earlier ones):

  A1  Drop the 5 FK constraints on backup_lit_part_assignment; dump the
      three backup tables (backup_lit_part_assignment, gregobase_chants_backup,
      proper_of_seasons_backup) to SQL files under liturgio-tools/sql/archive/
      and then DROP them.

  A2a Delete 14 byte-identical duplicate saint rows in lit_part_sources
      (keeps lowest text_id per slot, confirmed no backreferences).

  A2b Two vigil-Mass node pairs: add kind='mass' lit_epoch entries for
        NAT-EPI-00-0-vigil  and  PASC-ASC-06-5-vigil
      with tree edges, then re-key the vigil lps rows from the day node to the
      new mass node.

  A2c Re-key Christ the King Missal texts (text_ids 369,370) from OT-OT-34 to
      OT-FOL-00-4 so they sit beside the GRADUALE rows for the same feast.

  A2d Remaining proper-alternatives that need option_num=2: for each slot where
      two different antiphons share option_num=1, promote the higher text_id to
      option_num=2 (the Missal lists the second as "Vel:" / alternative).

  A4  Add primary keys: psalms(chapter,verse), dates_for_easter(yr),
      p_liturgical_day_slug_overrides(lit_day_id).

  A8  Backfill gr_index_entry.match_status NULL → 'unmatched'.

  A7  Create p_jurisdiction lookup table and add FKs from the four tables
      that carry a jurisdiction column.

  A6  Create p_review_status lookup table and add FKs from lit_part_sources
      and local_chants.

  B3  Prune p_liturgical_day_slug_overrides: delete the 349 all-NULL rows,
      drop slug_ovr column (confirmed unused in all loaders/SQL).

  B2  Rename local_chants.`office-part` → office_part and rebuild v_chant_item.

Usage:
    python scripts/migrate_schema_cleanup_2026_07_20.py [--dry-run]
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
ARCHIVE = HERE.parent / 'sql' / 'archive'


# ── helpers ──────────────────────────────────────────────────────────────────

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


def fk_exists(conn, table, constraint):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND CONSTRAINT_NAME=:c "
        "AND CONSTRAINT_TYPE='FOREIGN KEY'"
    ), {'t': table, 'c': constraint}).scalar() > 0


def table_exists(conn, table):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t"
    ), {'t': table}).scalar() > 0


def col_exists(conn, table, col):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {'t': table, 'c': col}).scalar() > 0


def index_exists(conn, table, index):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND INDEX_NAME=:i"
    ), {'t': table, 'i': index}).scalar() > 0


def pk_exists(conn, table):
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME=:t AND CONSTRAINT_TYPE='PRIMARY KEY'"
    ), {'t': table}).scalar() > 0


def log(msg):
    print(msg)


# ── dump helpers ──────────────────────────────────────────────────────────────

def dump_table_to_sql(conn, table, out_path):
    """Write a DROP+CREATE+INSERT dump of `table` to `out_path`."""
    from sqlalchemy import text
    # DDL via SHOW CREATE TABLE
    ddl = conn.execute(text(f'SHOW CREATE TABLE `{table}`')).fetchone()[1]
    rows = conn.execute(text(f'SELECT * FROM `{table}`')).fetchall()
    keys = conn.execute(text(f'SELECT * FROM `{table}` LIMIT 0')).keys()
    lines = [
        f'-- Backup dump of {table} taken {date.today()}',
        f'DROP TABLE IF EXISTS `{table}`;',
        ddl + ';',
    ]
    for row in rows:
        vals = ', '.join(
            'NULL' if v is None
            else f"'{str(v).replace(chr(92), chr(92)*2).replace(chr(39), chr(39)*2)}'"
            for v in row
        )
        lines.append(f'INSERT INTO `{table}` VALUES ({vals});')
    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    log(f'  Dumped {len(rows)} rows → {out_path}')


# ── main ──────────────────────────────────────────────────────────────────────

def run(dry_run):
    from sqlalchemy import text

    engine = get_engine()
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    with engine.begin() as conn:

        # ── A1: backup-table FKs and table drops ──────────────────────────────

        log('\n[A1] Drop backup_lit_part_assignment FK constraints')
        backup_fks = [
            ('backup_lit_part_assignment', 'fk_lpa_assignment_authority'),
            ('backup_lit_part_assignment', 'fk_lpa_epoch'),
            ('backup_lit_part_assignment', 'fk_lpa_group'),
            ('backup_lit_part_assignment', 'fk_lpa_part'),
            ('backup_lit_part_assignment', 'fk_lpa_text'),
        ]
        for tbl, fk in backup_fks:
            if fk_exists(conn, tbl, fk):
                log(f'  DROP FK {tbl}.{fk}')
                if not dry_run:
                    conn.execute(text(f'ALTER TABLE `{tbl}` DROP FOREIGN KEY `{fk}`'))
            else:
                log(f'  [skip] {fk} not present')

        log('\n[A1] Dump + drop backup tables')
        backup_tables = [
            'backup_lit_part_assignment',
            'gregobase_chants_backup',
            'proper_of_seasons_backup',
        ]
        for tbl in backup_tables:
            if table_exists(conn, tbl):
                out = ARCHIVE / f'{tbl}_dump_{date.today()}.sql'
                if not dry_run:
                    dump_table_to_sql(conn, tbl, out)
                    conn.execute(text(f'DROP TABLE `{tbl}`'))
                    log(f'  Dropped {tbl}')
                else:
                    rows = conn.execute(text(f'SELECT COUNT(*) FROM `{tbl}`')).scalar()
                    log(f'  [dry-run] Would dump {rows} rows to {out.name} then DROP')
            else:
                log(f'  [skip] {tbl} not present')

        # ── A2a: delete 14 duplicate saint rows ───────────────────────────────

        log('\n[A2a] Delete duplicate saint rows (keep lowest text_id per slot)')
        # Verified identical on all columns; none referenced by backup_lit_part_assignment.
        delete_ids = [1185, 1186, 1349, 1350, 1049, 1050, 1285, 1286,
                      1179, 1180, 1358, 1359, 1444, 1445]
        # Safety: confirm count before deleting
        n = conn.execute(text(
            f"SELECT COUNT(*) FROM lit_part_sources WHERE text_id IN ({','.join(str(i) for i in delete_ids)})"
        )).scalar()
        log(f'  Found {n} duplicate rows to delete (expected 14)')
        if n != 14:
            raise RuntimeError(f'Expected 14 duplicate rows, found {n} — aborting')
        if not dry_run:
            conn.execute(text(
                f"DELETE FROM lit_part_sources WHERE text_id IN ({','.join(str(i) for i in delete_ids)})"
            ))
            log('  Deleted 14 rows')
        else:
            log('  [dry-run] Would DELETE 14 rows')

        # ── A2b: add vigil-mass lit_epoch nodes + re-key lps rows ─────────────

        log('\n[A2b] Add vigil-mass epoch nodes for Epiphany and Ascension')
        vigil_nodes = [
            # (slug, title, rank_code, season, subseason, wknum, seq, sort_order, parent_slug)
            ('NAT-EPI-00-0-vigil', 'Epiphany — Vigil Mass',  'PRINCIPAL_TEMPORAL',
             'NAT', 'EPI', 0, 0, None, 'NAT-EPI-00-0'),
            ('PASC-ASC-06-5-vigil', 'Ascension — Vigil Mass', 'PRINCIPAL_TEMPORAL',
             'PASC', 'ASC', 6, 5, None, 'PASC-ASC-06-5'),
        ]
        for slug, title, rank, season, sub, wk, seq, so, parent in vigil_nodes:
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM lit_epoch WHERE slug=:s"
            ), {'s': slug}).scalar()
            if exists:
                log(f'  [skip] {slug} already in lit_epoch')
            else:
                parent_so = conn.execute(text(
                    "SELECT sort_order FROM lit_epoch WHERE slug=:s"
                ), {'s': parent}).scalar()
                if not dry_run:
                    conn.execute(text("""
                        INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, sort_order)
                        VALUES (:slug, 'mass', :title, :rank, :season, :sub, :wk, :seq, :so)
                    """), dict(slug=slug, title=title, rank=rank, season=season,
                               sub=sub, wk=wk, seq=seq, so=parent_so))
                    conn.execute(text("""
                        INSERT INTO lit_epoch_tree (parent_slug, child_slug)
                        VALUES (:parent, :child)
                    """), {'parent': parent, 'child': slug})
                    log(f'  Inserted {slug} + tree edge')
                else:
                    log(f'  [dry-run] Would insert {slug} (parent sort_order={parent_so})')

        # Re-key vigil lps rows
        # Epiphany vigil: text_ids 194,195 (Surge Ierusalem / Claritas) at NAT-EPI-00-0
        #   → NAT-EPI-00-0-vigil
        # Ascension vigil: text_ids 84,85 (Regna terrae / Christus unam) at PASC-ASC-06-5
        #   → PASC-ASC-06-5-vigil
        vigil_rekeys = [
            ([194, 195], 'NAT-EPI-00-0-vigil', None),   # wkday None for feast node
            ([84, 85],   'PASC-ASC-06-5-vigil', None),
        ]
        for ids, new_slug, new_wkday in vigil_rekeys:
            id_list = ','.join(str(i) for i in ids)
            if not dry_run:
                conn.execute(text(
                    f"UPDATE lit_part_sources SET lit_epoch_slug=:s, wkday=:w "
                    f"WHERE text_id IN ({id_list})"
                ), {'s': new_slug, 'w': new_wkday})
                log(f'  Re-keyed text_ids {ids} → {new_slug}')
            else:
                rows = conn.execute(text(
                    f"SELECT text_id, lit_epoch_slug, LEFT(original_text,35) FROM lit_part_sources WHERE text_id IN ({id_list})"
                )).fetchall()
                for r in rows:
                    log(f'  [dry-run] {r[0]} ({r[2]!r}) {r[1]} → {new_slug}')

        # ── A2c: Christ the King texts to OT-FOL-00-4 ────────────────────────

        log('\n[A2c] Re-key Christ the King Missal texts (369,370) to OT-FOL-00-4')
        # text 369: 'Dignus est Agnus' (in); text 370: 'Sedebit Dominus Rex' (co)
        # These are Christ the King antiphons that collide with ferial week-34 texts.
        ck_ids = [369, 370]
        if not dry_run:
            conn.execute(text(
                "UPDATE lit_part_sources SET lit_epoch_slug='OT-FOL-00-4' "
                "WHERE text_id IN (369, 370)"
            ))
            log('  Re-keyed 369,370 → OT-FOL-00-4')
        else:
            for r in conn.execute(text(
                "SELECT text_id, lit_epoch_slug, LEFT(original_text,40) FROM lit_part_sources WHERE text_id IN (369,370)"
            )).fetchall():
                log(f'  [dry-run] {r[0]} {r[1]} → OT-FOL-00-4  ({r[2]!r})')

        # ── A2d: promote duplicate-slot rows to option_num=2 ──────────────────

        log('\n[A2d] Promote alternative-antiphon rows from option_num=1 to option_num=2')
        # For each slot pair, the lower text_id is the primary (opt 1) from the JSON
        # first position; the higher id is the Vel:/alt text → option_num=2.
        # Confirmed: JSON list order = Missal order. The "Or:" alt is always second.
        promote_ids = [
            # (text_id_to_promote, brief description)
            (2,    'Easter Sun in.  alt: Surrexit Dominus vere'),
            (5,    'Easter Mon in.  alt: Resurrexit Dominus a mortuis'),
            (18,   'DivMercy in.    alt: Accipite iucunditatem'),
            (86,   'Ascension in.   alt: Viri Galilaei'),
            (87,   'Ascension co.   alt: Ecce ego vobiscum'),
            (235,  'Lent2 Sun in.   alt: Reminiscere'),
            (250,  'Lent3 Sun in.   alt: Cum sanctificatus'),
            (322,  'OT10 co.        alt: Deus caritas est'),
            (371,  'OT34 in.        alt: Loquetur Dominus pacem (ferial)'),
            (372,  'OT34 co.        alt: Laudate Dominum omnes gentes (ferial)'),
            # Christmas octave / Epiphany collisions
            (188,  'Dec31 in.       alt: Puer natus est (dec31)'),
            (189,  'Dec31 co.       alt: Misit Deus (dec31)'),
            (208,  'EPI-Sat in.     alt: Misit Deus Filium suum (saturday)'),
            (209,  'EPI-Sat co.     alt: De plenitudine (saturday)'),
            # NB: DivMercy entrance has TWO antiphons on PASC-AD_ASC-02-1 part 1:
            #   text 17 'Quasi modo geniti' = PRIMARY (option_num stays 1)
            #   text 18 'Accipite iucunditatem' = alternative (promoted to 2, above)
            # PASC-OCT-02-1 communion trio: cycle_sun=None/2/3 → option_nums 1/2/3
            (34,   'Easter3 co B    (cycle_sun=2) → opt 2'),
            (35,   'Easter3 co C    (cycle_sun=3) → opt 3'),
            # PASC-AD_ASC-03-1 (3rd Sunday) 3 communion alts
            (34,   'Easter3 Sun co B → opt 2'),  # already listed above
        ]
        # Deduplicate
        seen = set()
        promote_unique = []
        for tid, desc in promote_ids:
            if tid not in seen:
                seen.add(tid)
                promote_unique.append((tid, desc))

        # For the Easter-3 communion trio (text_ids 33/34/35 on PASC-AD_ASC-03-1)
        # we actually need opt 1=33(cycle_sun=None), opt 2=34(cycle_sun=2), opt 3=35(cycle_sun=3)
        # 33 is already opt 1. Apply separately.
        easter3_ca = [(34, 2), (35, 3)]

        for tid, opt in easter3_ca:
            if (tid, 'easter3') not in seen:
                seen.add((tid, 'easter3'))
                check = conn.execute(text(
                    "SELECT text_id, option_num, LEFT(original_text,35) FROM lit_part_sources WHERE text_id=:i"
                ), {'i': tid}).fetchone()
                if check:
                    if not dry_run:
                        conn.execute(text(
                            "UPDATE lit_part_sources SET option_num=:o WHERE text_id=:i"
                        ), {'o': opt, 'i': tid})
                        log(f'  text_id {tid} option_num → {opt}  ({check[2]})')
                    else:
                        log(f'  [dry-run] text_id {tid} option_num {check[1]} → {opt}  ({check[2]!r})')

        # Promote all others to option_num=2
        for tid, desc in promote_unique:
            if (tid, 'easter3') in seen:
                continue  # handled above
            check = conn.execute(text(
                "SELECT text_id, option_num, LEFT(original_text,35) FROM lit_part_sources WHERE text_id=:i"
            ), {'i': tid}).fetchone()
            if check is None:
                log(f'  [skip] text_id {tid} not found (may have been deleted in A2a?)')
                continue
            if check[1] == 2:
                log(f'  [skip] text_id {tid} already option_num=2')
                continue
            if not dry_run:
                conn.execute(text(
                    "UPDATE lit_part_sources SET option_num=2 WHERE text_id=:i"
                ), {'i': tid})
                log(f'  text_id {tid} option_num → 2  ({check[2]})')
            else:
                log(f'  [dry-run] text_id {tid} option_num {check[1]} → 2  ({check[2]!r})')

        # ── A4: missing primary keys ───────────────────────────────────────────

        log('\n[A4] Add missing primary keys')

        if pk_exists(conn, 'psalms'):
            log('  [skip] psalms PK already set')
        else:
            if not dry_run:
                conn.execute(text('ALTER TABLE psalms ADD PRIMARY KEY (chapter, verse)'))
            log('  Added PK (chapter, verse) on psalms' + (' [dry-run]' if dry_run else ''))

        if pk_exists(conn, 'dates_for_easter'):
            log('  [skip] dates_for_easter PK already set')
        else:
            if not dry_run:
                conn.execute(text('ALTER TABLE dates_for_easter ADD PRIMARY KEY (yr)'))
            log('  Added PK (yr) on dates_for_easter' + (' [dry-run]' if dry_run else ''))

        if pk_exists(conn, 'p_liturgical_day_slug_overrides'):
            log('  [skip] p_liturgical_day_slug_overrides PK already set')
        else:
            # First prune the all-NULL rows (B3 pre-step); safe to do here since
            # B3 will see them gone when it runs.
            nulls = conn.execute(text(
                "SELECT COUNT(*) FROM p_liturgical_day_slug_overrides WHERE slug_ovr IS NULL AND title_ovr IS NULL"
            )).scalar()
            log(f'  Will prune {nulls} all-NULL override rows before adding PK')
            if not dry_run:
                conn.execute(text(
                    "DELETE FROM p_liturgical_day_slug_overrides WHERE slug_ovr IS NULL AND title_ovr IS NULL"
                ))
                conn.execute(text('ALTER TABLE p_liturgical_day_slug_overrides ADD PRIMARY KEY (lit_day_id)'))
            log('  Pruned + added PK (lit_day_id) on p_liturgical_day_slug_overrides' + (' [dry-run]' if dry_run else ''))

        # ── A8: backfill gr_index_entry.match_status ──────────────────────────

        log('\n[A8] Backfill gr_index_entry.match_status NULL → unmatched')
        n_null = conn.execute(text(
            "SELECT COUNT(*) FROM gr_index_entry WHERE match_status IS NULL"
        )).scalar()
        if n_null == 0:
            log('  [skip] No NULL match_status rows')
        else:
            if not dry_run:
                conn.execute(text(
                    "UPDATE gr_index_entry SET match_status='unmatched' WHERE match_status IS NULL"
                ))
            log(f'  Updated {n_null} rows' + (' [dry-run]' if dry_run else ''))

        # ── A7: p_jurisdiction lookup + FKs ───────────────────────────────────

        log('\n[A7] Create p_jurisdiction and add FKs')
        if not table_exists(conn, 'p_jurisdiction'):
            if not dry_run:
                conn.execute(text("""
                    CREATE TABLE p_jurisdiction (
                        jurisdiction_code VARCHAR(64) NOT NULL PRIMARY KEY,
                        display_name      VARCHAR(200) NOT NULL,
                        sort_order        SMALLINT UNSIGNED NOT NULL DEFAULT 100,
                        is_active         TINYINT NOT NULL DEFAULT 1,
                        notes             VARCHAR(500)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO p_jurisdiction (jurisdiction_code, display_name, sort_order)
                    VALUES
                        ('UNIVERSAL', 'Universal (all jurisdictions)', 10),
                        ('US',        'United States',                 20)
                """))
            log('  Created p_jurisdiction + seeded 2 rows' + (' [dry-run]' if dry_run else ''))
        else:
            log('  [skip] p_jurisdiction already exists')

        jur_fks = [
            ('lit_part_sources',      'fk_lps_jurisdiction',   'jurisdiction'),
            ('proper_of_saints',      'fk_saints_jurisdiction', 'jurisdiction'),
            ('proper_of_seasons',     'fk_pos_jurisdiction',   'jurisdiction'),
            ('lit_observance_resolved','fk_obsres_jurisdiction','jurisdiction'),
        ]
        for tbl, fk, col in jur_fks:
            if fk_exists(conn, tbl, fk):
                log(f'  [skip] {fk} already present')
                continue
            if not dry_run:
                # Orphan check (table must exist at this point)
                orphans = conn.execute(text(
                    f"SELECT COUNT(*) FROM `{tbl}` t "
                    f"LEFT JOIN p_jurisdiction j ON j.jurisdiction_code=t.`{col}` "
                    f"WHERE j.jurisdiction_code IS NULL"
                )).scalar()
                if orphans > 0:
                    raise RuntimeError(f'Orphan jurisdictions in {tbl}: {orphans} rows — aborting')
                conn.execute(text(
                    f"ALTER TABLE `{tbl}` ADD CONSTRAINT `{fk}` "
                    f"FOREIGN KEY (`{col}`) REFERENCES p_jurisdiction(jurisdiction_code) "
                    f"ON UPDATE CASCADE ON DELETE RESTRICT"
                ))
            log(f'  Added FK {fk} on {tbl}' + (' [dry-run]' if dry_run else ''))

        # ── A6: p_review_status lookup + FKs ─────────────────────────────────

        log('\n[A6] Create p_review_status and add FKs')
        if not table_exists(conn, 'p_review_status'):
            if not dry_run:
                conn.execute(text("""
                    CREATE TABLE p_review_status (
                        status_code  VARCHAR(20) NOT NULL PRIMARY KEY,
                        display_name VARCHAR(80) NOT NULL,
                        sort_order   SMALLINT UNSIGNED NOT NULL DEFAULT 100
                    )
                """))
                conn.execute(text("""
                    INSERT INTO p_review_status (status_code, display_name, sort_order)
                    VALUES
                        ('draft',     'Draft',     10),
                        ('reviewed',  'Reviewed',  20),
                        ('published', 'Published', 30),
                        ('rejected',  'Rejected',  40),
                        ('approved',  'Approved',  50)
                """))
            log('  Created p_review_status + seeded 5 codes' + (' [dry-run]' if dry_run else ''))
        else:
            log('  [skip] p_review_status already exists')

        # Note: local_chants uses column `status`; lit_part_sources uses `review_status`.
        # We add FKs on each in place (no rename — that's a B-item to address opportunistically).
        rs_fks = [
            ('lit_part_sources', 'fk_lps_review_status', 'review_status'),
            ('local_chants',     'fk_lc_status',         'status'),
        ]
        for tbl, fk, col in rs_fks:
            if fk_exists(conn, tbl, fk):
                log(f'  [skip] {fk} already present')
                continue
            if not dry_run:
                # Orphan check (table must exist at this point)
                orphans = conn.execute(text(
                    f"SELECT COUNT(*) FROM `{tbl}` t "
                    f"LEFT JOIN p_review_status r ON r.status_code=t.`{col}` "
                    f"WHERE r.status_code IS NULL AND t.`{col}` IS NOT NULL"
                )).scalar()
                if orphans > 0:
                    raise RuntimeError(f'Orphan review statuses in {tbl}.{col}: {orphans} rows — aborting')
                conn.execute(text(
                    f"ALTER TABLE `{tbl}` ADD CONSTRAINT `{fk}` "
                    f"FOREIGN KEY (`{col}`) REFERENCES p_review_status(status_code) "
                    f"ON UPDATE CASCADE ON DELETE RESTRICT"
                ))
            log(f'  Added FK {fk} on {tbl}.{col}' + (' [dry-run]' if dry_run else ''))

        # ── B3: prune p_liturgical_day_slug_overrides, drop slug_ovr ──────────

        log('\n[B3] Prune p_liturgical_day_slug_overrides & drop slug_ovr column')
        # All-NULL rows already deleted in A4 above if PK didn't exist yet;
        # handle idempotently in case A4 was already applied.
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM p_liturgical_day_slug_overrides WHERE slug_ovr IS NULL AND title_ovr IS NULL"
        )).scalar()
        if remaining > 0:
            if not dry_run:
                conn.execute(text(
                    "DELETE FROM p_liturgical_day_slug_overrides WHERE slug_ovr IS NULL AND title_ovr IS NULL"
                ))
            log(f'  Deleted {remaining} all-NULL rows' + (' [dry-run]' if dry_run else ''))
        else:
            log('  [skip] No all-NULL rows (already pruned)')

        # slug_ovr: confirmed unused in all loaders (grep shows it in make-lit-epoch.sql
        # comment only, and the comment says it is NOT applied). Drop it.
        if col_exists(conn, 'p_liturgical_day_slug_overrides', 'slug_ovr'):
            if not dry_run:
                conn.execute(text('ALTER TABLE p_liturgical_day_slug_overrides DROP COLUMN slug_ovr'))
            log('  Dropped column slug_ovr' + (' [dry-run]' if dry_run else ''))
        else:
            log('  [skip] slug_ovr column not present')

        # ── B2: rename local_chants.`office-part` → office_part ───────────────

        log('\n[B2] Rename local_chants.`office-part` → office_part and rebuild v_chant_item')
        if col_exists(conn, 'local_chants', 'office-part'):
            # Drop the index on the old column name first
            if index_exists(conn, 'local_chants', 'idx_local_chants_office_part'):
                if not dry_run:
                    conn.execute(text('ALTER TABLE local_chants DROP INDEX idx_local_chants_office_part'))
            if not dry_run:
                conn.execute(text(
                    "ALTER TABLE local_chants CHANGE `office-part` office_part VARCHAR(16)"
                ))
                conn.execute(text(
                    "ALTER TABLE local_chants ADD INDEX idx_local_chants_office_part (office_part)"
                ))
            log('  Renamed office-part → office_part + rebuilt index' + (' [dry-run]' if dry_run else ''))
        elif col_exists(conn, 'local_chants', 'office_part'):
            log('  [skip] office_part already exists')
        else:
            log('  [skip] neither office-part nor office_part found in local_chants')

        # Rebuild v_chant_item to use the new column name.
        # (Even in dry-run we read the current definition so we can display the diff.)
        view_def = conn.execute(text(
            "SELECT VIEW_DEFINITION FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_SCHEMA='liturgio' AND TABLE_NAME='v_chant_item'"
        )).scalar()
        if view_def and '`office-part`' in view_def:
            new_def = view_def.replace('`office-part`', 'office_part')
            if not dry_run:
                conn.execute(text(f'CREATE OR REPLACE VIEW v_chant_item AS {new_def}'))
            log('  Rebuilt v_chant_item (replaced `office-part` with office_part)' + (' [dry-run]' if dry_run else ''))
        elif view_def:
            log('  [skip] v_chant_item does not reference `office-part` (already updated?)')
        else:
            log('  [skip] v_chant_item view not found in INFORMATION_SCHEMA')

        # ── rollback on dry-run ────────────────────────────────────────────────

        if dry_run:
            log('\n[dry-run] Rolling back — no changes committed.')
            conn.execute(text('ROLLBACK'))
            # sqlalchemy begin() will raise on rollback inside begin(); we re-raise
            # to skip the implicit commit. Use a savepoint trick instead:
            raise _DryRunRollback()

    log('\n✓ Migration committed successfully.')


class _DryRunRollback(Exception):
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would happen without committing')
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run)
    except _DryRunRollback:
        print('\n[dry-run complete — transaction rolled back]')
    except Exception as e:
        print(f'\nERROR: {e}', file=sys.stderr)
        raise
