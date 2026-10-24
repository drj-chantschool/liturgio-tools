#!/usr/bin/env python3
"""
Import the GregoBase staging table (``gregobase_chants``) into the unified ``chants`` table.

``gregobase_chants`` is a raw mirror of upstream; nothing but this import (and the propers
loaders' text matcher) reads it. Each staging row becomes ``chants`` row ``gregobase:<id>``
with ``elements`` built by :func:`gabc_tools.elements.to_elements`.

Re-sync rule (``origin_hash`` = SHA-256 of the staging row's upstream columns):

* not in ``chants`` yet: insert it. A chant with no group gets a new singleton ``chant_group``
  (listed in the report, so a human can merge it into an existing group later).
* hash unchanged: nothing.
* hash changed, ``is_curated = 0``: re-import elements and upstream metadata. Our own columns
  (``chant_group_id``, ``notes``, ``status``...) are never touched.
* hash changed, ``is_curated = 1``: **never overwritten**. ``origin_pending_hash`` is set to the
  new hash, flagging the row for review. Clear it (and update ``origin_hash``) once reviewed.

Rows with ``duplicateof`` set, and empty stubs (no incipit; upstream has a few whose gabc is just
a clef), are skipped unless something already points at them (``lit_part_sources`` /
``chant_indices`` ``chant_uuid``, ``chants.derived_from_uid``).

Run after every GregoBase scrape and before any propers load. Dry run by default.

Usage:
    python -m liturgio_tools.chants_import [--execute] [--ids 123 456]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date

from gabc_tools.elements import to_elements
from sqlalchemy import text

ORIGIN = 'GREGOBASE'
UPSTREAM_COLS = ['cantusid', 'version', 'incipit', 'initial', 'office-part', 'mode', 'mode_var',
                 'transcriber', 'commentary', 'headers', 'gabc', 'gabc_verses', 'tex_verses',
                 'tex_verses_pdf', 'remarks', 'copyrighted', 'duplicateof']
# chants columns refreshed from upstream on a re-sync, besides elements and origin_hash
SYNCED_COLS = ['version', 'incipit', 'office_part', 'mode', 'mode_var', 'transcriber',
               'commentary', 'cantusid', 'copyrighted', 'headers']


def origin_hash(row: dict) -> str:
    payload = json.dumps({k: row.get(k) for k in UPSTREAM_COLS}, sort_keys=True,
                         ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def chant_values(row: dict) -> dict:
    """The ``chants`` columns that come from a staging row."""
    return {
        'chant_uuid': f"gregobase:{row['id']}",
        'version': row['version'],
        'incipit': row['incipit'],
        'office_part': row['office-part'],
        'mode': row['mode'],
        'mode_var': row['mode_var'],
        'transcriber': row['transcriber'],
        'commentary': row['commentary'],
        'cantusid': row['cantusid'],
        'copyrighted': row['copyrighted'],
        'headers': row['headers'],
        'elements': json.dumps(to_elements(row), ensure_ascii=False),
        'origin_id': str(row['id']),
        'origin_hash': origin_hash(row),
    }


def group_name(row: dict) -> str:
    """``<incipit> (<part>. mode <m>) - <id>``, the convention of the existing groups."""
    part = f"{row['office-part']}. " if row['office-part'] else ''
    mode = f"mode {row['mode']}" if row['mode'] else 'mode ?'
    return f"{(row['incipit'] or '').strip() or 'gregobase'} ({part}{mode}) - {row['id']}"


@dataclass
class Plan:
    inserts: list = field(default_factory=list)        # (row, chant_group_id or None)
    updates: list = field(default_factory=list)        # row
    flagged: list = field(default_factory=list)        # (row, new_hash)
    new_groups: list = field(default_factory=list)     # row (gets a singleton group)
    skipped_dups: int = 0
    skipped_stubs: int = 0
    kept_dups: list = field(default_factory=list)      # ids of referenced duplicateof rows
    unchanged: int = 0


def build_plan(conn, ids=None, group_for: dict | None = None) -> Plan:
    """
    Compare staging with ``chants``. ``group_for`` maps gregobase id -> chant_group_id for
    chants not yet imported (the one-off migration passes the old ``gregobase_chant_group_map``);
    anything not in it gets a singleton group.
    """
    where = f"WHERE id IN ({','.join(str(int(i)) for i in ids)})" if ids else ''
    staging = conn.execute(text(f'SELECT * FROM gregobase_chants {where} ORDER BY id')).mappings().all()
    have = {r['origin_id']: r for r in conn.execute(text(
        'SELECT origin_id, origin_hash, origin_pending_hash, is_curated FROM chants WHERE origin_source = :o'),
        {'o': ORIGIN}).mappings()}
    referenced = set(conn.execute(text(
        "SELECT chant_uuid FROM lit_part_sources WHERE chant_uuid LIKE 'gregobase:%' "
        "UNION SELECT chant_uuid FROM chant_indices WHERE chant_uuid LIKE 'gregobase:%' "
        "UNION SELECT derived_from_uid FROM chants WHERE derived_from_uid LIKE 'gregobase:%'")).scalars())
    group_for = group_for or {}
    plan = Plan()
    for row in staging:
        row = dict(row)
        oid = str(row['id'])
        cur = have.get(oid)
        if cur is None:
            is_ref = f'gregobase:{oid}' in referenced
            if row['duplicateof'] is not None:
                if not is_ref:
                    plan.skipped_dups += 1
                    continue
                plan.kept_dups.append(row['id'])
            elif not (row['incipit'] or '').strip() and not is_ref:
                plan.skipped_stubs += 1
                continue
            gid = group_for.get(row['id'])
            plan.inserts.append((row, gid))
            if gid is None:
                plan.new_groups.append(row)
            continue
        h = origin_hash(row)
        if h == cur['origin_hash']:
            plan.unchanged += 1
        elif cur['is_curated']:
            if h != cur['origin_pending_hash']:
                plan.flagged.append((row, h))
            else:
                plan.unchanged += 1
        else:
            plan.updates.append(row)
    return plan


def _new_group(conn, row: dict) -> int:
    name = group_name(row)
    if conn.execute(text('SELECT 1 FROM chant_group WHERE canonical_name = :n'), {'n': name}).first():
        name = f"{name} (gregobase:{row['id']})"
    res = conn.execute(text(
        'INSERT INTO chant_group (canonical_name, incipit, notes) VALUES (:n, :i, :notes)'),
        {'n': name[:200], 'i': (row['incipit'] or '')[:200] or None,
         'notes': f"Created {date.today().isoformat()} as a singleton for ungrouped gregobase:{row['id']} "
                  f"(chants import); merge it if it duplicates an existing group"})
    return res.lastrowid


_INSERT = text(
    'INSERT INTO chants (chant_uuid, chant_group_id, version, incipit, office_part, mode, mode_var, '
    'transcriber, commentary, notation, elements, cantusid, copyrighted, headers, translation_source_code, '
    'is_text_exact, status, origin_source, origin_id, origin_hash) '
    'VALUES (:chant_uuid, :gid, :version, :incipit, :office_part, :mode, :mode_var, :transcriber, '
    ":commentary, 'gabc', :elements, :cantusid, :copyrighted, :headers, NULL, NULL, NULL, :origin, :origin_id, "
    ':origin_hash)')
_UPDATE = text(
    'UPDATE chants SET elements = :elements, origin_hash = :origin_hash, origin_pending_hash = NULL, '
    + ', '.join(f'{c} = :{c}' for c in SYNCED_COLS)
    + ' WHERE chant_uuid = :chant_uuid AND is_curated = 0')
_FLAG = text('UPDATE chants SET origin_pending_hash = :h WHERE chant_uuid = :u AND is_curated = 1')


def apply_plan(conn, plan: Plan) -> None:
    """Write a plan in the caller's transaction."""
    for row, gid in plan.inserts:
        if gid is None:
            gid = _new_group(conn, row)
        conn.execute(_INSERT, {**chant_values(row), 'gid': gid, 'origin': ORIGIN})
    for row in plan.updates:
        conn.execute(_UPDATE, chant_values(row))
    for row, h in plan.flagged:
        conn.execute(_FLAG, {'h': h, 'u': f"gregobase:{row['id']}"})


def report(plan: Plan) -> None:
    print(f'insert: {len(plan.inserts)}  re-import: {len(plan.updates)}  '
          f'flag curated for review: {len(plan.flagged)}  unchanged: {plan.unchanged}  '
          f'skipped duplicateof: {plan.skipped_dups}  skipped empty stubs: {plan.skipped_stubs}')
    if plan.kept_dups:
        print(f'  kept duplicateof rows that are referenced: {plan.kept_dups}')
    if plan.new_groups:
        print(f'  new singleton chant_groups ({len(plan.new_groups)}); merge any that duplicate a group:')
        for r in plan.new_groups:
            print(f'    gregobase:{r["id"]}  {group_name(r)}')
    for r, _ in plan.flagged:
        print(f'  CURATED, upstream changed (review, then clear origin_pending_hash): gregobase:{r["id"]} {r["incipit"]}')
    for r in plan.updates[:50]:
        print(f'  re-import gregobase:{r["id"]} {r["incipit"]}')
    if len(plan.updates) > 50:
        print(f'  ... and {len(plan.updates) - 50} more')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--execute', action='store_true', help='write (default: dry run)')
    ap.add_argument('--ids', nargs='+', type=int, help='limit to these gregobase ids')
    a = ap.parse_args(argv)
    from liturgio_tools.cli import get_rw_engine
    with get_rw_engine().begin() as conn:
        plan = build_plan(conn, a.ids)
        report(plan)
        if not a.execute:
            print('dry run; pass --execute to write')
            conn.rollback()
            return
        apply_plan(conn, plan)
    print('written')


if __name__ == '__main__':
    sys.exit(main())
