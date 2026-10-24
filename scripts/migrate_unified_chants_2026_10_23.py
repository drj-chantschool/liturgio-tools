#!/usr/bin/env python3
"""
2026-10-23: unified `chants` table (ROADMAP "Unified `chants` table").

One table of our own that the chant sources feed, replacing `local_chants`, the read path through
`gregobase_chants` + `gregobase_chant_group_map`, and the `v_chant_item` union.

Phase `create` (additive, safe to re-run):
  1. CREATE TABLE chants: PK chant_uuid ('gregobase:<id>' / 'local:<uuid>', so lit_part_sources,
     chant_indices and derived_from_uid survive unchanged), `elements` JSON element list, the
     local_chants metadata, provenance (origin_source/origin_id/origin_hash, is_curated,
     origin_pending_hash).
  2. Copy local_chants -> chants ('local:<local_chant_id>', elements = one gabc element). Rows
     already copied are refreshed when local_chants.updated_at is newer.
  3. Import gregobase_chants via liturgio_tools.chants_import, taking chant_group_id from
     gregobase_chant_group_map; the ~85 ungrouped chants get singleton groups.
  4. gregobase:20515 is_curated = 1 (its office-part was hand-fixed in staging, maintenance.md 2026-09).

Phase `retire` (run once every consumer reads `chants`):
  1. Re-run steps 2-3 and copy any group changes made in gregobase_chant_group_map meanwhile.
  2. v_chant_item becomes a view over chants (same columns; `gabc` = the element list as JSON,
     which gabc_tools' gabc_elements/extract_gabc_body read as GregoBase's array form).
  3. RENAME local_chants / gregobase_chant_group_map to *_bak_2026_10_23 and drop their FKs
     (so they block no chant_group delete). sync_to_do.py skips *_bak_* tables.

Usage:
    python scripts/migrate_unified_chants_2026_10_23.py [--phase create|retire] [--dry-run]
"""
import argparse
import json
import sys

from gabc_tools.elements import to_elements
from sqlalchemy import text

from liturgio_tools import chants_import
from liturgio_tools.cli import get_rw_engine

BAK = '_bak_2026_10_23'

DDL = """
CREATE TABLE chants (
  `chant_uuid` varchar(80) NOT NULL COMMENT 'gregobase:<id> | local:<uuid>; referenced by lit_part_sources, chant_indices, derived_from_uid',
  `chant_group_id` bigint unsigned NOT NULL,
  `version` varchar(128) DEFAULT 'english',
  `incipit` varchar(256) DEFAULT NULL,
  `office_part` varchar(16) DEFAULT NULL,
  `mode` varchar(8) DEFAULT NULL,
  `mode_var` varchar(16) DEFAULT NULL,
  `transcriber` varchar(128) DEFAULT 'Doctor J',
  `commentary` varchar(256) DEFAULT NULL,
  `notation` varchar(10) NOT NULL DEFAULT 'gabc',
  `elements` json NOT NULL COMMENT '[[type, content, options], ...]; see gabc_tools.elements',
  `headers` text COMMENT 'GregoBase GABC header lines (name, office-part, mode, book, transcriber)',
  `cantusid` varchar(32) DEFAULT NULL,
  `copyrighted` tinyint(1) DEFAULT NULL,
  `translation_source_code` varchar(40) DEFAULT NULL,
  `source_citation` varchar(500) DEFAULT NULL COMMENT 'Verifiable reference for translation (e.g. liturgies.net URL, book+page, psalm+verse)',
  `is_text_exact` tinyint unsigned DEFAULT '1',
  `derived_from_uid` varchar(80) DEFAULT NULL COMMENT 'English adaptation of this chant_uuid (not provenance)',
  `status` varchar(20) DEFAULT 'draft',
  `notes` varchar(500) DEFAULT NULL,
  `origin_source` varchar(20) DEFAULT NULL COMMENT 'Upstream feed (GREGOBASE); NULL = made here',
  `origin_id` varchar(64) DEFAULT NULL COMMENT 'Row id in the upstream feed',
  `origin_hash` char(64) DEFAULT NULL COMMENT 'SHA-256 of the upstream row at import (chants_import.origin_hash)',
  `origin_pending_hash` char(64) DEFAULT NULL COMMENT 'Upstream changed on a curated row: new hash awaiting review',
  `is_curated` tinyint(1) NOT NULL DEFAULT '0' COMMENT '1 = edited here; a re-import never overwrites it',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`chant_uuid`),
  UNIQUE KEY `uq_chants_origin` (`origin_source`, `origin_id`),
  KEY `idx_chants_group` (`chant_group_id`),
  KEY `idx_chants_version` (`version`),
  KEY `idx_chants_mode` (`mode`),
  KEY `idx_chants_status` (`status`),
  KEY `idx_chants_translation_source` (`translation_source_code`),
  KEY `idx_chants_office_part` (`office_part`),
  KEY `idx_chants_incipit` (`incipit`),
  KEY `idx_chants_derived_from` (`derived_from_uid`),
  CONSTRAINT `fk_chants_group` FOREIGN KEY (`chant_group_id`) REFERENCES `chant_group` (`chant_group_id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_chants_status` FOREIGN KEY (`status`) REFERENCES `p_review_status` (`status_code`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_chants_translation_source` FOREIGN KEY (`translation_source_code`) REFERENCES `p_translation_source` (`translation_source_code`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='Every chant score, whatever its source (GregoBase import, Dr. J transcriptions)'
"""

LOCAL_COLS = ['chant_group_id', 'version', 'incipit', 'office_part', 'mode', 'mode_var', 'transcriber',
              'commentary', 'notation', 'translation_source_code', 'source_citation', 'is_text_exact',
              'derived_from_uid', 'status', 'notes', 'created_at', 'updated_at']

VIEW = """
CREATE OR REPLACE VIEW v_chant_item AS
SELECT c.chant_uuid AS chant_item_uid,
       IF(c.chant_uuid LIKE 'gregobase:%', 'gregobase', 'local') AS source_code,
       SUBSTRING_INDEX(c.chant_uuid, ':', -1) AS source_pk,
       c.chant_group_id, c.version, c.notation, c.incipit, c.mode, c.mode_var, c.office_part,
       CAST(c.elements AS CHAR) AS gabc,
       c.translation_source_code, c.is_text_exact, c.derived_from_uid, c.status,
       c.created_at, c.updated_at, c.cantusid, c.transcriber, c.commentary, c.copyrighted,
       NULL AS duplicateof
FROM chants c
"""


def table_exists(c, name):
    return c.execute(text('SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() '
                          'AND table_name = :t'), {'t': name}).first() is not None


def copy_local(c, dry):
    rows = c.execute(text(
        "SELECT l.*, ch.updated_at AS ch_updated FROM local_chants l "
        "LEFT JOIN chants ch ON ch.chant_uuid = CONCAT('local:', l.local_chant_id)")).mappings().all()
    todo = [r for r in rows if r['ch_updated'] is None or r['updated_at'] > r['ch_updated']]
    print(f'local_chants: {len(rows)} rows, {len(todo)} to copy/refresh')
    if dry:
        return
    cols = ', '.join(LOCAL_COLS)
    vals = ', '.join(f':{k}' for k in LOCAL_COLS)
    upd = ', '.join(f'{k} = VALUES({k})' for k in LOCAL_COLS if k != 'created_at')
    for r in todo:
        c.execute(text(f'INSERT INTO chants (chant_uuid, elements, {cols}) VALUES (:u, :el, {vals}) '
                       f'ON DUPLICATE KEY UPDATE elements = VALUES(elements), {upd}'),
                  {**{k: r[k] for k in LOCAL_COLS}, 'u': f"local:{r['local_chant_id']}",
                   'el': json.dumps(to_elements({'gabc': r['gabc']}), ensure_ascii=False)})


def import_gregobase(c, dry):
    group_for = {}
    if table_exists(c, 'gregobase_chant_group_map'):
        group_for = dict(c.execute(text('SELECT gregobase_id, chant_group_id FROM gregobase_chant_group_map')).all())
    plan = chants_import.build_plan(c, group_for=group_for)
    chants_import.report(plan)
    if not dry:
        chants_import.apply_plan(c, plan)
    if group_for and not dry:
        n = c.execute(text(
            "UPDATE chants ch JOIN gregobase_chant_group_map m ON ch.origin_source = 'GREGOBASE' "
            "AND ch.origin_id = CAST(m.gregobase_id AS CHAR) "
            "SET ch.chant_group_id = m.chant_group_id WHERE ch.chant_group_id <> m.chant_group_id")).rowcount
        print(f'group changes copied from gregobase_chant_group_map: {n}')


def drop_fks(c, table):
    for (name,) in c.execute(text(
            "SELECT constraint_name FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
            "AND table_name = :t AND constraint_type = 'FOREIGN KEY'"), {'t': table}).all():
        c.execute(text(f'ALTER TABLE `{table}` DROP FOREIGN KEY `{name}`'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', choices=['create', 'retire'], default='create')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    eng = get_rw_engine()
    with eng.connect() as c:
        if not table_exists(c, 'chants'):
            print('CREATE TABLE chants')
            if a.dry_run:
                sys.exit('dry run stops here: chants does not exist yet')
            c.execute(text(DDL))   # DDL commits implicitly
    with eng.begin() as c:
        if not table_exists(c, 'local_chants'):
            sys.exit('local_chants is gone: already retired')
        copy_local(c, a.dry_run)
        import_gregobase(c, a.dry_run)
        if not a.dry_run:
            c.execute(text("UPDATE chants SET is_curated = 1 WHERE chant_uuid = 'gregobase:20515'"))
        n_missing = c.execute(text(
            "SELECT COUNT(*) FROM (SELECT chant_uuid u FROM lit_part_sources UNION SELECT chant_uuid FROM chant_indices "
            "UNION SELECT derived_from_uid FROM chants) r WHERE u IS NOT NULL "
            "AND u NOT IN (SELECT chant_uuid FROM chants)")).scalar()
        print(f'chant_uuid references with no chants row: {n_missing}')
        if a.dry_run:
            c.rollback()
            print('dry run; nothing written')
            return
    if a.phase == 'retire':
        with eng.connect() as c:
            c.execute(text(VIEW))
            for t in ('local_chants', 'gregobase_chant_group_map'):
                drop_fks(c, t)
                c.execute(text(f'RENAME TABLE `{t}` TO `{t}{BAK}`'))
                print(f'{t} -> {t}{BAK}')
            print('v_chant_item now reads chants')


if __name__ == '__main__':
    main()
