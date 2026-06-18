#!/usr/bin/env python
"""
liturgio_tools.py

CLI helper for the composer's assistant subagent.
Provides read and write access to the liturgio MySQL database.

Usage:
    python liturgio_tools.py lookup-day --date YYYY-MM-DD [--jurisdiction UNIVERSAL]
    python liturgio_tools.py search-chant --incipit TEXT --part PART_CODE
    python liturgio_tools.py get-chant --chant-group-id N [--gregobase-id N]
    python liturgio_tools.py save-english --chant-group-id N --gabc-file PATH --source CODE [--is-exact 0|1] [--notes TEXT] [--derived-from UID]
    python liturgio_tools.py assign --jurisdiction CODE --part-code CODE --epoch SLUG --chant-group-id N --authority CODE [--wkday 1-7] [--wknum-mod-4 0-3] [--wknum-mod-2 0-1] [--cycle-sun 0|1|2] [--cycle-wk 0|1]
"""

import argparse
import getpass
import json
import re
import sys
import uuid
from pathlib import Path

from gabc_tools.gbchant import extract_gabc_body


# ---------------------------------------------------------------------------
# DB connection helpers
# ---------------------------------------------------------------------------

def _get_engine(user: str, db_name: str = 'liturgio'):
    import keyring
    from sqlalchemy import create_engine, text, exc as sa_exc

    service = 'liturgio-mysql'

    interactive = sys.stdin.isatty()

    while True:
        password = keyring.get_password(service, user)
        if password is None:
            if not interactive:
                sys.exit(f'ERROR: No keyring password for {user}@{db_name}. '
                         f'Run interactively to set it: '
                         f'python -c "import keyring; keyring.set_password(\'{service}\', \'{user}\', \'PASSWORD\')"')
            password = getpass.getpass(f'MySQL password for {user}@localhost/{db_name}: ')
            keyring.set_password(service, user, password)

        conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
        engine = create_engine(conn_str, future=True)
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            return engine
        except (sa_exc.OperationalError, sa_exc.ProgrammingError) as exc:
            msg = str(exc).lower()
            if 'access denied' in msg or '1045' in msg:
                if not interactive:
                    sys.exit(f'ERROR: Access denied for {user}@{db_name}. '
                             f'Stored password may be wrong — fix it interactively.')
                print(f'Access denied for {user} — removing stored password and retrying.')
                keyring.delete_password(service, user)
            else:
                raise


def get_ro_engine():
    return _get_engine('liturgio_ro')


def get_rw_engine():
    return _get_engine('jcost')


# ---------------------------------------------------------------------------
# GABC helpers
# ---------------------------------------------------------------------------

def build_gabc_file(row: dict) -> str:
    headers = (row.get('headers') or '').strip()
    if not headers:
        lines = []
        if row.get('incipit'):
            lines.append(f"name:{row['incipit']};")
        if row.get('office-part'):
            lines.append(f"office-part:{row['office-part']};")
        if row.get('mode'):
            lines.append(f"mode:{row['mode']};")
        if row.get('version'):
            lines.append(f"book:{row['version']};")
        if row.get('transcriber'):
            lines.append(f"transcriber:{row['transcriber']};")
        headers = '\n'.join(lines)
    body = extract_gabc_body(row.get('gabc') or '')
    return f'{headers}\n%%\n{body}'


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_lookup_day(args):
    from sqlalchemy import text

    engine = get_ro_engine()
    with engine.connect() as conn:
        # Resolve the liturgical day via proper_of_seasons + lit_epoch (epoch model)
        pos = conn.execute(text("""
            SELECT pos.lit_day_id, le.title, le.season, le.subseason, le.wknum, le.seq,
                   pos.cycle_wk, pos.cycle_sun, pos.wkday
            FROM proper_of_seasons pos
            JOIN lit_epoch le ON le.slug = pos.lit_day_id
            WHERE pos.dt = :dt AND pos.jurisdiction = :jur
        """), {'dt': args.date, 'jur': args.jurisdiction}).fetchone()

        if not pos:
            print(f'No liturgical day found for {args.date} ({args.jurisdiction})')
            sys.exit(1)

        lit_day_id = pos[0]
        title      = pos[1]
        season     = pos[2]
        subseason  = pos[3]
        wknum      = pos[4]
        seq        = pos[5]
        cycle_wk   = pos[6]
        cycle_sun  = pos[7]
        wkday      = pos[8]

        print(f'Date:          {args.date}')
        print(f'Liturgical day: {title} ({lit_day_id})')
        print(f'Season:        {season} / {subseason}  week {wknum}  seq {seq}')
        print(f'Cycle:         cycle_wk={cycle_wk}  cycle_sun={cycle_sun}  wkday={wkday}')
        print()

        # Fetch existing lit_part_assignments anchored to this day's epoch slug
        # (using the depth-in-tree approach: match on lit_epoch_slug = lit_day_id
        # to show direct day-level assignments; callers wanting full resolution
        # should use query-daily-mass-parts.sql)
        assignments = conn.execute(text("""
            SELECT lpa.assignment_id, lpa.part_id, lpa.chant_group_id,
                   lpa.assignment_authority_code, lpa.notes,
                   sp.part_code, sp.display_name,
                   cg.canonical_name
            FROM lit_part_assignment lpa
            JOIN service_part sp ON sp.part_id = lpa.part_id
            JOIN chant_group cg ON cg.chant_group_id = lpa.chant_group_id
            WHERE lpa.jurisdiction = :jur
              AND lpa.lit_epoch_slug = :epoch_slug
            ORDER BY sp.display_order
        """), {
            'jur': args.jurisdiction,
            'epoch_slug': lit_day_id,
        }).fetchall()

        # Fetch all PROPER service parts
        proper_parts = conn.execute(text("""
            SELECT part_id, part_code, display_name, display_order
            FROM service_part
            WHERE part_class = 'PROPER' AND service_code = 'MASS'
            ORDER BY display_order
        """)).fetchall()

        assigned_part_ids = {row[1]: row for row in assignments}

        print(f'{"PART":<12} {"STATUS":<12} {"AUTHORITY":<12} {"CHANT GROUP"}')
        print('-' * 70)
        for part in proper_parts:
            part_id, part_code, display_name, _ = part
            if part_id in assigned_part_ids:
                a = assigned_part_ids[part_id]
                authority = a[3] or ''
                chant_name = a[7] or f'group {a[2]}'
                print(f'{display_name:<12} {"ASSIGNED":<12} {authority:<12} {chant_name}')

                # Show GR page numbers if available
                gr_pages = conn.execute(text("""
                    SELECT gcs.source, gcs.page, gs.title, gs.year
                    FROM gregobase_chant_group_map gcgm
                    JOIN gregobase_chant_sources gcs ON gcs.chant_id = gcgm.gregobase_id
                    JOIN gregobase_sources gs ON gs.id = gcs.source
                    WHERE gcgm.chant_group_id = :gid
                      AND gcs.source IN (1, 2, 4)
                    ORDER BY gcs.source, gcs.sequence
                """), {'gid': a[2]}).fetchall()
                for pg in gr_pages:
                    print(f'             GR ({pg[3]} {pg[2]}): p. {pg[1]}')
            else:
                print(f'{display_name:<12} {"unassigned":<12}')
        print()

        # Return structured data for subagent use
        result = {
            'date': args.date,
            'jurisdiction': args.jurisdiction,
            'lit_day_id': lit_day_id,
            'epoch_slug': lit_day_id,
            'title': title,
            'season': season,
            'subseason': subseason,
            'wknum': wknum,
            'seq': seq,
            'cycle_wk': cycle_wk,
            'cycle_sun': cycle_sun,
            'wkday': wkday,
            'assignments': [
                {
                    'part_code': a[5],
                    'display_name': a[6],
                    'chant_group_id': a[2],
                    'chant_group_name': a[7],
                    'authority': a[3],
                }
                for a in assignments
            ],
            'unassigned_parts': [
                {'part_id': p[0], 'part_code': p[1], 'display_name': p[2]}
                for p in proper_parts
                if p[0] not in assigned_part_ids
            ],
        }
        # Print JSON for subagent to parse
        print('\n=== JSON ===')
        print(json.dumps(result, indent=2))


def cmd_search_chant(args):
    from sqlalchemy import text

    engine = get_ro_engine()

    # Map part_code to office-part strings used in gregobase
    part_to_office = {
        'in': ['in', 'Introitus', 'Introit'],
        'gr': ['gr', 'Gradual', 'Graduale'],
        'al': ['al', 'Alleluia'],
        'tr': ['tr', 'Tractus', 'Tract'],
        'of': ['of', 'Offertorium', 'Offertory'],
        'co': ['co', 'Communio', 'Communion'],
    }
    office_parts = part_to_office.get(args.part, [args.part])

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT gc.id, gc.incipit, gc.mode, gc.`office-part`, gc.gabc,
                   GROUP_CONCAT(
                       CONCAT(gs.year, ' ', gs.title, ' p.', gcs.page)
                       ORDER BY gcs.source, gcs.sequence
                       SEPARATOR ' | '
                   ) AS gr_pages,
                   gcgm.chant_group_id
            FROM gregobase_chants gc
            LEFT JOIN gregobase_chant_group_map gcgm ON gcgm.gregobase_id = gc.id
            LEFT JOIN gregobase_chant_sources gcs ON gcs.chant_id = gc.id AND gcs.source IN (1, 2, 4)
            LEFT JOIN gregobase_sources gs ON gs.id = gcs.source
            WHERE gc.incipit LIKE :incipit
            GROUP BY gc.id, gc.incipit, gc.mode, gc.`office-part`, gc.gabc, gcgm.chant_group_id
            ORDER BY gc.incipit
            LIMIT 20
        """), {'incipit': f'%{args.incipit}%'}).fetchall()

        if not rows:
            print(f'No chants found matching "{args.incipit}"')
            return

        print(f'Found {len(rows)} chant(s) matching "{args.incipit}":\n')
        results = []
        for row in rows:
            gc_id, incipit, mode, office_part, gabc_raw, gr_pages, group_id = row
            body = extract_gabc_body(gabc_raw or '')
            # First 80 chars of body
            preview = body[:80].replace('\n', ' ') + ('…' if len(body) > 80 else '')

            print(f'  [{gc_id}] {incipit}')
            print(f'       mode={mode}  office-part={office_part}  group_id={group_id}')
            if gr_pages:
                print(f'       GR: {gr_pages}')
            print(f'       GABC: {preview}')
            print()

            results.append({
                'gregobase_id': gc_id,
                'incipit': incipit,
                'mode': mode,
                'office_part': office_part,
                'chant_group_id': group_id,
                'gr_pages': gr_pages,
                'gabc_preview': preview,
            })

        print('\n=== JSON ===')
        print(json.dumps(results, indent=2))


def cmd_get_chant(args):
    from sqlalchemy import text

    engine = get_ro_engine()

    with engine.connect() as conn:
        if args.gregobase_id:
            rows = conn.execute(text("""
                SELECT gc.id, gc.incipit, gc.mode, gc.`office-part`, gc.gabc,
                       gc.headers, gc.version, gc.transcriber, gc.commentary,
                       gcgm.chant_group_id
                FROM gregobase_chants gc
                LEFT JOIN gregobase_chant_group_map gcgm ON gcgm.gregobase_id = gc.id
                WHERE gc.id = :id
            """), {'id': args.gregobase_id}).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT gc.id, gc.incipit, gc.mode, gc.`office-part`, gc.gabc,
                       gc.headers, gc.version, gc.transcriber, gc.commentary,
                       gcgm.chant_group_id
                FROM gregobase_chants gc
                JOIN gregobase_chant_group_map gcgm ON gcgm.gregobase_id = gc.id
                WHERE gcgm.chant_group_id = :gid
            """), {'gid': args.chant_group_id}).fetchall()

        if not rows:
            print('No chant found.')
            sys.exit(1)

        results = []
        for row in rows:
            gc_id, incipit, mode, office_part, gabc_raw, headers, version, transcriber, commentary, group_id = row

            gabc_full = build_gabc_file({
                'incipit': incipit,
                'office-part': office_part,
                'mode': mode,
                'version': version,
                'transcriber': transcriber,
                'headers': headers,
                'gabc': gabc_raw,
            })

            # GR page numbers
            gr_pages = conn.execute(text("""
                SELECT gs.year, gs.title, gcs.page
                FROM gregobase_chant_sources gcs
                JOIN gregobase_sources gs ON gs.id = gcs.source
                WHERE gcs.chant_id = :id AND gcs.source IN (1, 2, 4)
                ORDER BY gcs.source, gcs.sequence
            """), {'id': gc_id}).fetchall()

            print(f'=== Gregobase #{gc_id}: {incipit} ===')
            print(f'Mode: {mode}  Part: {office_part}  Group: {group_id}')
            if commentary:
                print(f'Commentary: {commentary}')
            for pg in gr_pages:
                print(f'GR page ({pg[0]} {pg[1]}): {pg[2]}')
            print()
            print(gabc_full)
            print()

            results.append({
                'gregobase_id': gc_id,
                'incipit': incipit,
                'mode': mode,
                'office_part': office_part,
                'chant_group_id': group_id,
                'commentary': commentary,
                'gr_pages': [{'year': pg[0], 'book': pg[1], 'page': pg[2]} for pg in gr_pages],
                'gabc_full': gabc_full,
            })

        # Also fetch any existing local_chants (English) for the group
        group_id = results[0]['chant_group_id'] if results else args.chant_group_id
        if group_id:
            english = conn.execute(text("""
                SELECT local_chant_id, version, incipit, mode, translation_source_code,
                       source_citation, is_text_exact, status, gabc, notes, created_at
                FROM local_chants
                WHERE chant_group_id = :gid
                ORDER BY created_at
            """), {'gid': group_id}).fetchall()

            if english:
                print(f'=== Existing English (local_chants) for group {group_id} ===')
                for e in english:
                    print(f'  [{e[0]}] version={e[1]} status={e[7]} source={e[4]} exact={e[6]}')
                    print(f'  incipit: {e[2]}')
                    if e[5]:
                        print(f'  citation: {e[5]}')
                    print()

        print('\n=== JSON ===')
        print(json.dumps(results, indent=2, default=str))


def cmd_save_english(args):
    from sqlalchemy import text

    gabc_path = Path(args.gabc_file)
    if not gabc_path.exists():
        print(f'File not found: {args.gabc_file}')
        sys.exit(1)

    gabc_content = gabc_path.read_text(encoding='utf-8').strip()

    # Basic validation: must have %% separator
    if '%%' not in gabc_content:
        print('Error: GABC file must contain %% separator between headers and body.')
        sys.exit(1)

    # Extract incipit from headers
    incipit = None
    office_part = None
    mode = None
    for line in gabc_content.split('%%')[0].splitlines():
        line = line.strip()
        if line.startswith('name:'):
            incipit = line[5:].rstrip(';').strip()
        elif line.startswith('office-part:'):
            office_part = line[12:].rstrip(';').strip()
        elif line.startswith('mode:'):
            mode = line[5:].rstrip(';').strip()

    local_chant_id = str(uuid.uuid4())
    is_exact = int(args.is_exact)

    engine = get_rw_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO local_chants
                (local_chant_id, chant_group_id, version, incipit, `office-part`, mode,
                 gabc, translation_source_code, source_citation, is_text_exact,
                 derived_from_uid, status, notes)
            VALUES
                (:uid, :gid, 'english', :incipit, :office_part, :mode,
                 :gabc, :source, :citation, :is_exact,
                 :derived_from, 'draft', :notes)
        """), {
            'uid': local_chant_id,
            'gid': args.chant_group_id,
            'incipit': incipit,
            'office_part': office_part,
            'mode': mode,
            'gabc': gabc_content,
            'source': args.source,
            'citation': args.source_citation,
            'is_exact': is_exact,
            'derived_from': args.derived_from,
            'notes': args.notes,
        })
        conn.commit()

    print(f'Saved local_chant: {local_chant_id}')
    print(f'  chant_group_id:  {args.chant_group_id}')
    print(f'  incipit:         {incipit}')
    print(f'  source:          {args.source}')
    print(f'  citation:        {args.source_citation}')
    print(f'  is_text_exact:   {is_exact}')
    print()
    print('=== JSON ===')
    print(json.dumps({'local_chant_id': local_chant_id, 'chant_group_id': args.chant_group_id, 'incipit': incipit}, indent=2))


def cmd_merge_groups(args):
    import difflib
    from sqlalchemy import text

    keep_id  = args.keep
    merge_id = args.merge

    if keep_id == merge_id:
        sys.exit('ERROR: --keep and --merge must be different IDs.')

    ro_engine = get_ro_engine()
    with ro_engine.connect() as conn:
        # 1. Verify both groups exist
        def fetch_group(gid):
            row = conn.execute(text(
                'SELECT chant_group_id, canonical_name FROM chant_group WHERE chant_group_id = :id'
            ), {'id': gid}).fetchone()
            if row is None:
                sys.exit(f'ERROR: chant_group {gid} not found.')
            return {'id': row[0], 'name': row[1]}

        keep  = fetch_group(keep_id)
        merge = fetch_group(merge_id)

        print(f'Keep  [{keep_id}]: {keep["name"]}')
        print(f'Merge [{merge_id}]: {merge["name"]}')
        print()

        # 2. Mode check
        def fetch_modes(gid):
            rows = conn.execute(text("""
                SELECT DISTINCT gc.mode
                FROM gregobase_chant_group_map gcgm
                JOIN gregobase_chants gc ON gc.id = gcgm.gregobase_id
                WHERE gcgm.chant_group_id = :gid AND gc.mode IS NOT NULL AND gc.mode != ''
            """), {'gid': gid}).fetchall()
            return {r[0] for r in rows}

        keep_modes  = fetch_modes(keep_id)
        merge_modes = fetch_modes(merge_id)
        print(f'Modes — keep: {keep_modes or "(none)"}  merge: {merge_modes or "(none)"}')

        if keep_modes and merge_modes and keep_modes.isdisjoint(merge_modes):
            sys.exit(f'ERROR: Mode mismatch — keep has {keep_modes}, merge has {merge_modes}. '
                     f'These are probably different chants.')

        # 3. Name similarity check
        ratio = difflib.SequenceMatcher(
            None,
            keep["name"].lower(),
            merge["name"].lower(),
        ).ratio()
        print(f'Name similarity: {ratio:.2f}')

        if ratio < 0.4 and not args.force:
            sys.exit(f'ERROR: Names are very different (similarity={ratio:.2f}). '
                     f'Use --force to override.')

        if ratio < 0.65 and not args.force:
            ans = input(f'WARNING: Name similarity is low ({ratio:.2f}). Proceed? [y/N] ').strip().lower()
            if ans != 'y':
                sys.exit('Aborted.')

        # Preview what will be moved
        def count_rows(table, col, gid):
            row = conn.execute(text(
                f'SELECT COUNT(*) FROM {table} WHERE {col} = :gid'
            ), {'gid': gid}).fetchone()
            return row[0]

        n_map   = count_rows('gregobase_chant_group_map', 'chant_group_id', merge_id)
        n_local = count_rows('local_chants',              'chant_group_id', merge_id)
        n_lpa   = count_rows('lit_part_assignment',       'chant_group_id', merge_id)
        print()
        print(f'Rows to remap:')
        print(f'  gregobase_chant_group_map : {n_map}')
        print(f'  local_chants              : {n_local}')
        print(f'  lit_part_assignment       : {n_lpa}')

        # Find gregobase_ids that would cause a duplicate key conflict
        duplicates = conn.execute(text("""
            SELECT m.gregobase_id
            FROM gregobase_chant_group_map m
            JOIN gregobase_chant_group_map k
              ON k.gregobase_id = m.gregobase_id
             AND k.chant_group_id = :keep_id
            WHERE m.chant_group_id = :merge_id
        """), {'keep_id': keep_id, 'merge_id': merge_id}).fetchall()
        dup_ids = [r[0] for r in duplicates]
        if dup_ids:
            print(f'  ({len(dup_ids)} gregobase map row(s) are already in keep group — will be deleted)')

    print()
    if not args.force:
        ans = input('Proceed with merge? [y/N] ').strip().lower()
        if ans != 'y':
            sys.exit('Aborted.')

    # Execute merge in a single transaction
    rw_engine = get_rw_engine()
    with rw_engine.connect() as conn:
        # gregobase_chant_group_map: delete duplicates, then remap the rest
        if dup_ids:
            conn.execute(text("""
                DELETE FROM gregobase_chant_group_map
                WHERE chant_group_id = :merge_id AND gregobase_id IN :ids
            """), {'merge_id': merge_id, 'ids': tuple(dup_ids)})

        conn.execute(text("""
            UPDATE gregobase_chant_group_map
            SET chant_group_id = :keep_id
            WHERE chant_group_id = :merge_id
        """), {'keep_id': keep_id, 'merge_id': merge_id})

        # local_chants
        conn.execute(text("""
            UPDATE local_chants SET chant_group_id = :keep_id
            WHERE chant_group_id = :merge_id
        """), {'keep_id': keep_id, 'merge_id': merge_id})

        # lit_part_assignment
        conn.execute(text("""
            UPDATE lit_part_assignment SET chant_group_id = :keep_id
            WHERE chant_group_id = :merge_id
        """), {'keep_id': keep_id, 'merge_id': merge_id})

        # Delete the merged group
        conn.execute(text(
            'DELETE FROM chant_group WHERE chant_group_id = :merge_id'
        ), {'merge_id': merge_id})

        conn.commit()

    print(f'Done. chant_group {merge_id} merged into {keep_id} and deleted.')
    print()
    print('=== JSON ===')
    print(json.dumps({
        'kept': keep_id,
        'deleted': merge_id,
        'gregobase_map_remapped': n_map - len(dup_ids),
        'gregobase_map_deleted': len(dup_ids),
        'local_chants_remapped': n_local,
        'lit_part_assignment_remapped': n_lpa,
    }, indent=2))


def cmd_resolve(args):
    """Resolve liturgical precedence for a year (or a single date) and optionally
    materialize the result into lit_observance_resolved."""
    import datetime
    from liturgio_tools.liturgical_calendar.resolver import (
        resolve_year,
        resolve_date,
        materialize_year,
    )

    local_sols = list(args.local_solemnity or [])

    if args.materialize:
        engine = get_rw_engine()
    else:
        engine = get_ro_engine()

    if args.date:
        # Single-date mode: resolve the surrounding year but filter to the date.
        target = datetime.date.fromisoformat(args.date)
        year   = target.year
    elif args.year:
        year   = args.year
        target = None
    else:
        print('ERROR: supply either --year YYYY or --date YYYY-MM-DD', file=sys.stderr)
        sys.exit(1)

    if args.materialize:
        n = materialize_year(engine, args.jurisdiction, year, local_sols)
        print(f'Materialized {n} rows for {args.jurisdiction} / {year}')
        if target:
            rows = [r for r in resolve_date(engine, args.jurisdiction, target, local_sols)
                    if r['dt'] == target]
        else:
            rows = []  # already written; skip printing full year for brevity
    else:
        if target:
            rows = resolve_date(engine, args.jurisdiction, target, local_sols)
        else:
            rows = resolve_year(engine, args.jurisdiction, year, local_sols)

    if rows:
        # Print a compact table.
        header = f"{'DATE':<12} {'ROLE':<14} {'TRANSFERRED':<12} {'RANK':<25} {'EPOCH'}"
        print(header)
        print('-' * len(header))
        for r in sorted(rows, key=lambda x: (x['dt'], x['role'])):
            xfer = f"(from {r['nominal_dt']})" if r['is_transferred'] else ''
            print(
                f"{str(r['dt']):<12} {r['role']:<14} {xfer:<12} "
                f"{r['rank_code']:<25} {r['epoch_slug']}"
            )
        print()
        print(f"Total rows: {len(rows)}")

    print()
    print('=== JSON ===')
    print(json.dumps(rows, indent=2, default=str))


def cmd_assign(args):
    from sqlalchemy import text

    # Psalter rule: wknum_mod_* are only valid without an epoch slug
    if args.epoch and (args.wknum_mod_4 is not None or args.wknum_mod_2 is not None):
        sys.exit(
            'ERROR: --wknum-mod-4 / --wknum-mod-2 are psalter-fallback modifiers and '
            'cannot be combined with --epoch. Use either --epoch (for a specific liturgical '
            'context) or --wknum-mod-* (for a psalter fallback), not both.'
        )

    engine = get_rw_engine()
    with engine.connect() as conn:
        # Validate epoch slug if provided
        if args.epoch:
            epoch_row = conn.execute(text("""
                SELECT slug, kind, title FROM lit_epoch WHERE slug = :slug
            """), {'slug': args.epoch}).fetchone()
            if not epoch_row:
                sys.exit(f'ERROR: lit_epoch slug not found: {args.epoch!r}')
            print(f'Epoch: {epoch_row[2]!r} ({epoch_row[1]}) — {epoch_row[0]}')

        # Look up part_id from service_part
        part = conn.execute(text("""
            SELECT part_id FROM service_part
            WHERE part_code = :code AND service_code = 'MASS'
        """), {'code': args.part_code}).fetchone()

        if not part:
            print(f'service_part not found: {args.part_code}')
            sys.exit(1)

        part_id = part[0]

        conn.execute(text("""
            INSERT INTO lit_part_assignment
                (jurisdiction, part_id, lit_epoch_slug,
                 wknum_mod_4, wknum_mod_2,
                 wkday, cycle_sun, cycle_wk,
                 chant_group_id, assignment_authority_code, option_num, notes)
            VALUES
                (:jur, :part_id, :epoch_slug,
                 :wknum_mod_4, :wknum_mod_2,
                 :wkday, :cycle_sun, :cycle_wk,
                 :gid, :authority, :option_num, :notes)
        """), {
            'jur': args.jurisdiction,
            'part_id': part_id,
            'epoch_slug': args.epoch,
            'wknum_mod_4': args.wknum_mod_4,
            'wknum_mod_2': args.wknum_mod_2,
            'wkday': args.wkday,
            'cycle_sun': args.cycle_sun,
            'cycle_wk': args.cycle_wk,
            'gid': args.chant_group_id,
            'authority': args.authority,
            'option_num': args.option_num,
            'notes': args.notes,
        })
        conn.commit()

    epoch_display = args.epoch if args.epoch else '(psalter fallback)'
    print(f'Assigned {args.part_code} for epoch={epoch_display} ({args.jurisdiction})')
    print(f'  chant_group_id: {args.chant_group_id}')
    print(f'  authority:      {args.authority}')
    print(f'  option_num:     {args.option_num}')
    print(f'  wkday={args.wkday}  wknum_mod_4={args.wknum_mod_4}  wknum_mod_2={args.wknum_mod_2}')
    print(f'  cycle_sun={args.cycle_sun}  cycle_wk={args.cycle_wk}')
    print()
    print('=== JSON ===')
    print(json.dumps({
        'jurisdiction': args.jurisdiction,
        'epoch': args.epoch,
        'part_code': args.part_code,
        'chant_group_id': args.chant_group_id,
        'authority': args.authority,
        'option_num': args.option_num,
        'wkday': args.wkday,
        'wknum_mod_4': args.wknum_mod_4,
        'wknum_mod_2': args.wknum_mod_2,
        'cycle_sun': args.cycle_sun,
        'cycle_wk': args.cycle_wk,
    }, indent=2))



# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Liturgio DB CLI helper')
    sub = parser.add_subparsers(dest='command', required=True)

    # lookup-day
    p_day = sub.add_parser('lookup-day', help='Look up liturgical day and existing chant assignments')
    p_day.add_argument('--date', required=True, help='Date in YYYY-MM-DD format')
    p_day.add_argument('--jurisdiction', default='UNIVERSAL')

    # search-chant
    p_search = sub.add_parser('search-chant', help='Search gregobase chants by incipit')
    p_search.add_argument('--incipit', required=True, help='Text to search for (partial match)')
    p_search.add_argument('--part', required=True, help='Part code: in, gr, al, of, co')

    # get-chant
    p_get = sub.add_parser('get-chant', help='Get full GABC and metadata for a chant')
    p_get.add_argument('--chant-group-id', type=int, default=None)
    p_get.add_argument('--gregobase-id', type=int, default=None)

    # save-english
    p_save = sub.add_parser('save-english', help='Save an English GABC adaptation to local_chants')
    p_save.add_argument('--chant-group-id', type=int, required=True)
    p_save.add_argument('--gabc-file', required=True, help='Path to .gabc file to save')
    p_save.add_argument('--source', required=True,
                        help='translation_source_code: ROMAN_MISSAL_2010_ICEL | GREGORIAN_MISSAL | ABBEY_PSALMS_CANTICLES | NEW_AMERICAN_BIBLE')
    p_save.add_argument('--source-citation', default=None,
                        help='Verifiable reference: URL, book+page, psalm+verse')
    p_save.add_argument('--is-exact', type=int, default=1, choices=[0, 1],
                        help='1 if translation text matches exactly, 0 if adapted')
    p_save.add_argument('--notes', default=None)
    p_save.add_argument('--derived-from', default=None, help='UID of source chant (e.g. gregobase:1234)')

    # merge-groups
    p_merge = sub.add_parser('merge-groups', help='Merge two chant_group records into one')
    p_merge.add_argument('--keep',  type=int, required=True, help='chant_group_id to retain')
    p_merge.add_argument('--merge', type=int, required=True, help='chant_group_id to absorb and delete')
    p_merge.add_argument('--force', action='store_true',
                         help='Skip name-similarity warning prompt')

    # resolve
    p_resolve = sub.add_parser(
        'resolve',
        help='Resolve liturgical precedence and transfers for a year or date'
    )
    p_resolve.add_argument('--jurisdiction', default='UNIVERSAL',
                           help='Jurisdiction code (default: UNIVERSAL)')
    p_resolve.add_argument('--year', type=int, default=None,
                           help='Civil year to resolve (e.g. 2028)')
    p_resolve.add_argument('--date', default=None,
                           help='Single date YYYY-MM-DD (resolves its whole year, '
                                'filters output to this date)')
    p_resolve.add_argument('--local-solemnity', action='append', metavar='SLUG',
                           help='Saint slug to elevate to PROPER_SOLEMNITY '
                                '(repeat for multiple, e.g. --local-solemnity st-mark)')
    p_resolve.add_argument('--materialize', action='store_true',
                           help='Write resolved rows to lit_observance_resolved '
                                '(idempotent; requires RW access)')

    # assign
    p_assign = sub.add_parser('assign', help='Assign a chant group to a liturgical epoch/part')
    p_assign.add_argument('--jurisdiction', default='UNIVERSAL')
    p_assign.add_argument('--part-code', required=True, help='e.g. in, gr, al, of, co')
    p_assign.add_argument('--epoch', default=None,
                          help='lit_epoch slug to target (e.g. PASC-AD_ASC-02-5 for a day, '
                               'PASC-AD_ASC-02 for the whole 2nd week of Easter). '
                               'Omit for a psalter fallback (use with --wknum-mod-* instead).')
    p_assign.add_argument('--chant-group-id', type=int, required=True)
    p_assign.add_argument('--authority', required=True, help='GRADUALE | MISSAL | OCM | CUSTOM')
    p_assign.add_argument('--wkday', type=int, default=None,
                          help='Day of week: 1=Sun…7=Sat. NULL (default) = any day. '
                               'Narrows an epoch or psalter assignment to a specific weekday.')
    p_assign.add_argument('--wknum-mod-4', type=int, default=None, choices=[0, 1, 2, 3],
                          help='Psalter week mod 4 (0-3). Only valid without --epoch.')
    p_assign.add_argument('--wknum-mod-2', type=int, default=None, choices=[0, 1],
                          help='Psalter week mod 2 (0-1). Only valid without --epoch.')
    p_assign.add_argument('--cycle-sun', type=int, default=None,
                          help='Sunday lectionary year: 1=A, 2=B, 0=C (liturgical_year mod 3). '
                               'NULL = all years.')
    p_assign.add_argument('--cycle-wk', type=int, default=None,
                          help='Weekday lectionary year: liturgical_year mod 2. NULL = all years. '
                               'At most one of --cycle-sun / --cycle-wk may be set.')
    p_assign.add_argument('--option-num', type=int, default=1,
                          help='Option number: 1=primary/GR-default (default), 2=first alternate, '
                               'etc. Use 2+ to record an "or" option alongside the primary.')
    p_assign.add_argument('--notes', default=None)

    args = parser.parse_args()

    dispatch = {
        'lookup-day': cmd_lookup_day,
        'search-chant': cmd_search_chant,
        'get-chant': cmd_get_chant,
        'save-english': cmd_save_english,
        'merge-groups': cmd_merge_groups,
        'resolve': cmd_resolve,
        'assign': cmd_assign,
    }
    dispatch[args.command](args)


if __name__ == '__main__':
    main()
