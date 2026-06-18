#!/usr/bin/env python3
"""
upload_english_chants.py

Walks chant directories (Introitus/, Offertorium/, Communio/, Alleluia/, etc.) for english*.gabc files,
resolves the corresponding chant_group_id via the sibling Latin file (or the
directory name when no Latin sibling exists), checks for duplicates, and
inserts new rows into local_chants.

Interactive when needed:
  - Ambiguous chant group: shows ~30 neumes of each candidate alongside
    the English, lets you pick (or show more, or skip).
  - Missing translation source: shows book/commentary and lets you pick.

Usage:
    python upload_english_chants.py [--dry-run] [-v]
"""

import datetime
import json
import re
import sys
import uuid
import argparse
import getpass
import logging
from pathlib import Path

from gabc_tools.gbchant import extract_gabc_body, parse_gabc_header

log = logging.getLogger(__name__)

CHANT_ROOT = Path(__file__).parent

PARTS = {
    'Introitus':    'in',
    'Offertorium':  'of',
    'Communio':     'co',
    'Alleluia':     'al',
    'Gradual':      'gr',
    'Tractus':      'tr',
    'Antiphona':    'an',
    'Resp-breve':   'rb',
    'Responsum':    're',
    'Canticum':     'ca',
    'hymns':        'hy',
}

# Map GABC header display names → gregobase office-part codes.
# Values not found here (e.g. 'Vesperae Ant E') pass through as-is.
OFFICE_PART_NORM = {
    'introitus': 'in',    'introit': 'in',
    'offertorium': 'of',  'offertory': 'of',
    'communio': 'co',     'communion': 'co',
    'alleluia': 'al',
    'graduale': 'gr',     'gradual': 'gr',
    'tractus': 'tr',      'tract': 'tr',
    'antiphona': 'an',
    'responsorium breve': 'rb',
    'responsorium': 're',
    'hymnus': 'hy',
    'canticum': 'ca',
    'psalmus': 'ps',
}


def normalize_part_code(office_part: str) -> str:
    return OFFICE_PART_NORM.get(office_part.lower().strip(), office_part.strip())


DIR_TO_OFFICE_PART = {
    'Introitus': 'Introitus',
    'Offertorium': 'Offertorium',
    'Communio': 'Communio',
    'Alleluia': 'Alleluia',
    'Gradual': 'Graduale',
    'Tractus': 'Tractus',
    'Antiphona': 'Antiphona',
    'Resp-breve': 'Responsorium breve',
    'Responsum': 'Responsorium',
    'Canticum': 'Canticum',
    'hymns': 'Hymnus',
}

GREGOBASE_KNOWN_PARTS = frozenset({
    'in', 'gr', 'tr', 'al', 'of', 'co', 'an', 're', 'rb', 'hy', 'ca', 'ps',
    'se', 'ky', 'or', 'va', 'su', 'tp', 'pa', 'im', 'rh', 'pr',
})


def is_recognized_part(raw: str) -> bool:
    lower = raw.lower().strip()
    if lower in OFFICE_PART_NORM or lower in GREGOBASE_KNOWN_PARTS:
        return True
    return bool(re.match(
        r'(Vesperae|Laudes|Officium|Hora|Completorium|Tertia|Sexta|Nona|'
        r'Ad |Invitatorium|1V |M Ant|B Ant)', raw.strip()))


def infer_mode_from_annotation(annotation: str) -> str:
    roman = {'I': '1', 'II': '2', 'III': '3', 'IV': '4',
             'V': '5', 'VI': '6', 'VII': '7', 'VIII': '8'}
    parts = annotation.strip().rstrip(';').split()
    if parts and parts[-1] in roman:
        return roman[parts[-1]]
    m = re.match(r'(\d)', annotation.strip())
    if m:
        return m.group(1)
    return ''


def add_gabc_headers(path: Path, headers_to_add: list[tuple[str, str]]) -> None:
    content = path.read_text(encoding='utf-8-sig', errors='replace')
    new_lines = '\n'.join(f'{key}: {value};' for key, value in headers_to_add)
    if '%%' in content:
        before, after = content.split('%%', 1)
        content = before.rstrip('\n') + '\n' + new_lines + '\n%%' + after
    else:
        content = new_lines + '\n' + content
    path.write_text(content, encoding='utf-8')


# Order matters: RM is checked before GM so "tr. RM and GM" -> RM
TRANSLATION_SOURCE_PATTERNS = [
    # inexact variants first (more specific) — "tr. based on X" or "tr. X (adapted)"
    (r'\btr\.\s+based\s+on\s+RM\b',        'ROMAN_MISSAL_2010_ICEL',    False),
    (r'\btr\.\s*RM\b\s*\(adapted\)',        'ROMAN_MISSAL_2010_ICEL',    False),
    (r'\btr\.\s*RM\b',                      'ROMAN_MISSAL_2010_ICEL',    True),
    (r'\btr\.\s+based\s+on\s+GM\b',        'GREGORIAN_MISSAL',          False),
    (r'\btr\.\s*GM\b\s*\(adapted\)',        'GREGORIAN_MISSAL',          False),
    (r'\btr\.\s*GM\b',                      'GREGORIAN_MISSAL',          True),
    (r'\btr\.\s+based\s+on\s+APC\b',       'ABBEY_PSALMS_CANTICLES',    False),
    (r'\btr\.\s*APC\b\s*\(adapted\)',       'ABBEY_PSALMS_CANTICLES',    False),
    (r'\btr\.\s*APC\b',                     'ABBEY_PSALMS_CANTICLES',    True),
    (r'\btr\.\s+based\s+on\s+NAB\b',       'NEW_AMERICAN_BIBLE',        False),
    (r'\btr\.\s*NAB\b\s*\(adapted\)',       'NEW_AMERICAN_BIBLE',        False),
    (r'\btr\.\s*NAB\b',                     'NEW_AMERICAN_BIBLE',        True),
    (r'\btr\.\s+based\s+on\s+(?:61\s*)?SJM\b', 'SAINT_JOSEPH_MISSAL_1961', False),
    (r'\btr\.\s*(?:61\s*)?SJM\b\s*\(adapted\)', 'SAINT_JOSEPH_MISSAL_1961', False),
    (r'\btr\.\s*(?:61\s*)?SJM\b',          'SAINT_JOSEPH_MISSAL_1961',  True),
]

RULE = '-' * 72


class InterventionCounter:
    def __init__(self, total: int):
        self.total = total
        self.n = 0

    def tick(self) -> str:
        self.n += 1
        return f'[{self.n}/{self.total}]  '


# ---------------------------------------------------------------------------
# GABC helpers
# ---------------------------------------------------------------------------

def parse_gabc_headers(path: Path) -> dict[str, str]:
    """Return {key: value} for every header line before %%, with lowercased keys."""
    content = path.read_text(encoding='utf-8-sig', errors='replace')
    return {k.strip().lower(): v for k, v in parse_gabc_header(content).items()}


def first_neumes(gabc_body: str, n: int = 30) -> str:
    """
    Return the first n syllable(neume) pairs, preserving word boundaries.
    Clefs and barlines are skipped.
    """
    tokens: list[tuple[bool, str, str]] = []
    for m in re.finditer(r'((?:[^()<>]|<[^>]*>)*)\(([^)]*)\)', gabc_body):
        syllable = m.group(1)
        neume    = m.group(2)
        syl      = syllable.strip()
        if not syl or re.fullmatch(r'[,.:;`*]+', neume) or re.fullmatch(r'[cf]\d', neume):
            continue
        word_start = bool(re.search(r'\s', syllable)) or not tokens
        tokens.append((word_start, syl, neume))
        if len(tokens) >= n:
            break

    parts: list[str] = []
    for word_start, syl, neume in tokens:
        token = f'{syl}({neume})'
        parts.append((' ' + token) if (word_start and parts) else token)
    return ''.join(parts)


def gabc_to_text(gabc_body: str) -> str:
    """Extract plain readable lyrics from a GABC body (no accidentals, markup, or directives)."""
    pairs = re.findall(r'((?:[^()]|(?:<v>[()]</v>))*)\(([^)]*)\)', gabc_body)
    raw = ''.join(syl for syl, _neume in pairs)
    raw = re.sub(r'<[^>]*>', '', raw)
    raw = re.sub(r'[{}]', '', raw)
    raw = re.sub(r'(?:R|V)/\s*\.?|\*|~|i\s*j\.?|E\s*u\s*o\s*u\s*a\s*e\.?', '', raw)
    raw = re.sub(r':', '', raw)
    return re.sub(r'\s+', ' ', raw).strip()


def kebab_to_incipit(dir_name: str) -> str:
    """'cantate-domino-novum' -> 'Cantate domino novum'."""
    words = dir_name.replace('-', ' ').split()
    if not words:
        return ''
    return words[0].capitalize() + (' ' + ' '.join(words[1:]) if len(words) > 1 else '')


def detect_translation_source(headers: dict) -> tuple[str | None, bool]:
    """Return (source_code_or_None, is_text_exact)."""
    commentary = headers.get('commentary', '')
    book       = headers.get('book', '')
    for pattern, code, is_exact in TRANSLATION_SOURCE_PATTERNS:
        if re.search(pattern, commentary, re.IGNORECASE):
            return (code, is_exact)
    book_lower = book.lower()
    if 'roman missal' in book_lower:
        return ('ROMAN_MISSAL_2010_ICEL', True)
    if 'gregorian missal' in book_lower:
        return ('GREGORIAN_MISSAL', True)
    return (None, True)


def find_latin_sibling(eng_path: Path) -> Path | None:
    """Return first non-english *.gabc in the same directory, or None."""
    for f in sorted(eng_path.parent.glob('*.gabc')):
        if not f.stem.lower().startswith('english'):
            return f
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_engine(user: str = 'jcost'):
    import keyring
    from sqlalchemy import create_engine, text
    service  = 'liturgio-mysql'
    password = keyring.get_password(service, user)
    if password is None:
        password = getpass.getpass(f'MySQL password for {user}@localhost/liturgio: ')
        keyring.set_password(service, user, password)
    engine = create_engine(
        f'mysql+mysqlconnector://{user}:{password}@localhost:3306/liturgio',
        future=True,
    )
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


def fetch_translation_source_choices(engine) -> list[dict]:
    """Active translation source codes from p_translation_source, in display order."""
    from sqlalchemy import text as sqlt
    with engine.connect() as conn:
        rows = conn.execute(sqlt("""
            SELECT translation_source_code, display_name
              FROM p_translation_source
             WHERE is_active = 1
             ORDER BY sort_order
        """)).fetchall()
    return [{'code': r[0], 'display_name': r[1]} for r in rows]


def _dedup_ids(rows: list[tuple]) -> list[int]:
    seen: set[int] = set()
    result = []
    for cgid, *_ in rows:
        if cgid not in seen:
            seen.add(cgid)
            result.append(cgid)
    return result


def lookup_exact(engine, incipit: str, part_code: str, mode: str | None) -> list[int]:
    """Exact case-insensitive incipit + part + optional mode query."""
    from sqlalchemy import text as sqlt

    def _query(use_mode: bool) -> list[tuple]:
        sql    = """
            SELECT gcm.chant_group_id, gc.mode
              FROM gregobase_chants gc
              JOIN gregobase_chant_group_map gcm ON gc.id = gcm.gregobase_id
             WHERE LOWER(gc.incipit) = LOWER(:inc)
               AND gc.`office-part` = :part
        """
        params: dict = {'inc': incipit, 'part': part_code}
        if use_mode and mode:
            sql   += '   AND gc.mode = :mode\n'
            params['mode'] = mode
        with engine.connect() as conn:
            return conn.execute(sqlt(sql), params).fetchall()

    rows = _query(use_mode=True) if mode else []
    if not rows:
        rows = _query(use_mode=False)
    return _dedup_ids(rows)


def lookup_prefix(engine, incipit: str, part_code: str) -> list[int]:
    """Prefix LIKE search on first 1–3 words. Used when no Latin sibling."""
    from sqlalchemy import text as sqlt

    words    = incipit.split()
    patterns = list(dict.fromkeys(
        ' '.join(words[:n]) + '%' for n in (3, 2, 1) if words[:n]
    ))

    seen: set[int] = set()
    rows: list[tuple] = []
    with engine.connect() as conn:
        for pat in patterns:
            result = conn.execute(sqlt("""
                SELECT gcm.chant_group_id, gc.mode
                  FROM gregobase_chants gc
                  JOIN gregobase_chant_group_map gcm ON gc.id = gcm.gregobase_id
                 WHERE LOWER(gc.incipit) LIKE LOWER(:pat)
                   AND gc.`office-part` = :part
            """), {'pat': pat, 'part': part_code}).fetchall()
            for row in result:
                if row[0] not in seen:
                    seen.add(row[0])
                    rows.append(row)
            if rows:
                break
    return _dedup_ids(rows)


def chant_already_exists(engine, chant_group_id: int, version: str) -> bool:
    from sqlalchemy import text as sqlt
    with engine.connect() as conn:
        count = conn.execute(sqlt("""
            SELECT COUNT(*) FROM local_chants
             WHERE chant_group_id = :cgid AND version = :ver
        """), {'cgid': chant_group_id, 'ver': version}).scalar()
    return bool(count)


def insert_local_chant(engine, row: dict) -> None:
    from sqlalchemy import text as sqlt
    with engine.begin() as conn:
        conn.execute(sqlt("""
            INSERT INTO local_chants
                (local_chant_id, chant_group_id, version, incipit, `office-part`,
                 mode, transcriber, commentary, gabc, translation_source_code,
                 is_text_exact, notes, status)
            VALUES
                (:uid, :cgid, :version, :incipit, :part,
                 :mode, :transcriber, :commentary, :gabc, :translation_source,
                 :is_text_exact, :notes, 'draft')
        """), row)


def fetch_group_candidates(engine, group_ids: list[int], part_code: str) -> list[dict]:
    """
    For each chant_group_id return a display dict with canonical_name,
    version string, and raw GABC body (from a representative gregobase entry,
    preferring the Solesmes version).
    """
    from sqlalchemy import text as sqlt
    results = []
    with engine.connect() as conn:
        for cgid in group_ids:
            cg = conn.execute(sqlt(
                'SELECT canonical_name FROM chant_group WHERE chant_group_id = :id'
            ), {'id': cgid}).fetchone()
            canonical = cg[0] if cg else f'(group {cgid})'

            gc = conn.execute(sqlt("""
                SELECT gc.incipit, gc.mode, gc.version, gc.gabc
                  FROM gregobase_chants gc
                  JOIN gregobase_chant_group_map gcm ON gc.id = gcm.gregobase_id
                 WHERE gcm.chant_group_id = :cgid
                   AND gc.`office-part`   = :part
                 ORDER BY
                   CASE WHEN gc.version LIKE '%Solesmes%' THEN 0
                        WHEN gc.version LIKE '%Vatican%'  THEN 1
                        ELSE 2 END,
                   gc.id
                 LIMIT 1
            """), {'cgid': cgid, 'part': part_code}).fetchone()

            if gc:
                gabc_body   = extract_gabc_body(gc[3] or '')
                version_str = gc[2] or ''
            else:
                gabc_body   = ''
                version_str = ''

            results.append({
                'chant_group_id': cgid,
                'canonical_name': canonical,
                'version':        version_str,
                'gabc_body':      gabc_body,
            })
    return results


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def prompt_chant_group(group_ids: list[int], part_code: str,
                       eng_path: Path, eng_body: str, engine,
                       counter: 'InterventionCounter | None' = None) -> int | None:
    """
    Show the English neumes and each candidate's Latin neumes, let user pick.
    Returns the chosen chant_group_id, or None to skip.
    """
    candidates = fetch_group_candidates(engine, group_ids, part_code)
    n = 30
    rel = str(eng_path.relative_to(CHANT_ROOT)).replace('\\', '/')
    prefix = counter.tick() if counter else ''

    while True:
        print()
        print(RULE)
        print(f'  {prefix}English  ({rel}):')
        print(f'    {first_neumes(eng_body, n) or "(no body found)"}')
        print()

        for i, c in enumerate(candidates, 1):
            lat_neumes = first_neumes(c['gabc_body'], n) if c['gabc_body'] else '(no GABC)'
            print(f'  {i}.  [{c["chant_group_id"]}] {c["canonical_name"]}')
            if c['version']:
                print(f'       version: {c["version"]}')
            print(f'       {lat_neumes}')

        print()
        print(f'  m  show more neumes (currently {n})')
        print(f'  s  skip this file')
        print(RULE)

        choice = input('  Pick: ').strip().lower()

        if choice == 'm':
            n = 100
            continue
        if choice in ('s', ''):
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]['chant_group_id']
        except ValueError:
            pass
        print(f'  Enter 1–{len(candidates)}, m, or s.')


def prompt_translation_source(headers: dict, eng_path: Path, choices: list[dict],
                              counter: 'InterventionCounter | None' = None) -> tuple[str | None, bool]:
    """
    Show book/commentary/text and let user pick a translation source code.
    Append 'i' to the number for an inexact (adapted) translation.
    Returns (source_code_or_None_or_'SKIP', is_text_exact).
    """
    rel = str(eng_path.relative_to(CHANT_ROOT)).replace('\\', '/')
    prefix = counter.tick() if counter else ''
    print()
    print(RULE)
    print(f'  {prefix}Translation source unknown: {rel}')
    if headers.get('book'):
        print(f'    book:        {headers["book"]}')
    if headers.get('commentary'):
        print(f'    commentary:  {headers["commentary"]}')
    try:
        raw_gabc = eng_path.read_text(encoding='utf-8', errors='replace')
        chant_text = gabc_to_text(extract_gabc_body(raw_gabc))
        if chant_text:
            print(f'    text:        {chant_text}')
    except OSError:
        pass
    print()
    for i, c in enumerate(choices, 1):
        print(f'  {i} / {i}i  {c["code"]}  ({c["display_name"]})')
    print(f'  n     none / unknown  (insert with no source)')
    print(f'  s     skip this file')
    print(f'  (append i for inexact/adapted, e.g. 1i)')
    print(RULE)

    while True:
        choice = input('  Source: ').strip().lower()
        if choice == 'n' or choice == '':
            return (None, True)
        if choice == 's':
            return ('SKIP', True)
        inexact = choice.endswith('i')
        num_str = choice[:-1] if inexact else choice
        try:
            idx = int(num_str) - 1
            if 0 <= idx < len(choices):
                return (choices[idx]['code'], not inexact)
        except ValueError:
            pass
        print(f'  Enter 1–{len(choices)} (optionally followed by i), n, or s.')


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def fetch_existing_chant(engine, chant_group_id: int, version: str) -> dict | None:
    from sqlalchemy import text as sqlt
    with engine.connect() as conn:
        row = conn.execute(sqlt("""
            SELECT local_chant_id, gabc, incipit, `office-part` AS part, mode,
                   transcriber, commentary, translation_source_code, is_text_exact, notes
              FROM local_chants
             WHERE chant_group_id = :cgid AND version = :ver
             LIMIT 1
        """), {'cgid': chant_group_id, 'ver': version}).mappings().fetchone()
    return dict(row) if row else None


def row_differs(existing: dict, new_row: dict) -> bool:
    checks = [
        ('gabc',                    'gabc'),
        ('incipit',                 'incipit'),
        ('part',                    'part'),
        ('mode',                    'mode'),
        ('transcriber',             'transcriber'),
        ('commentary',              'commentary'),
        ('translation_source_code', 'translation_source'),
        ('is_text_exact',           'is_text_exact'),
        ('notes',                   'notes'),
    ]
    for db_col, row_key in checks:
        if str(existing.get(db_col) or '') != str(new_row.get(row_key) or ''):
            return True
    return False


def update_local_chant(engine, row: dict, local_chant_id: str) -> None:
    from sqlalchemy import text as sqlt
    with engine.begin() as conn:
        conn.execute(sqlt("""
            UPDATE local_chants
               SET incipit                = :incipit,
                   `office-part`          = :part,
                   mode                   = :mode,
                   transcriber            = :transcriber,
                   commentary             = :commentary,
                   gabc                   = :gabc,
                   translation_source_code = :translation_source,
                   is_text_exact          = :is_text_exact,
                   notes                  = :notes
             WHERE local_chant_id = :local_chant_id
        """), {**row, 'local_chant_id': local_chant_id})


def _read_gabc(eng_path: Path) -> tuple[dict, str]:
    headers = parse_gabc_headers(eng_path)
    content = eng_path.read_text(encoding='utf-8-sig', errors='replace')
    return headers, content


def process_file(eng_path: Path, part_code: str, engine, dry_run: bool,
                 counter: 'InterventionCounter | None' = None,
                 source_choices: list[dict] | None = None) -> str:
    headers, gabc_content = _read_gabc(eng_path)
    rel = str(eng_path.relative_to(CHANT_ROOT)).replace('\\', '/')
    version     = eng_path.stem   # 'english', 'english2', 'english-f3', …

    # --- Validate and fix missing/broken headers ---
    fixes = []       # auto-inferred and written to file
    problems = []    # user must fix manually
    to_add = []      # headers to prepend: [(key, value), ...]

    incipit = headers.get('name', '')
    if not incipit:
        incipit = kebab_to_incipit(eng_path.parent.name)
        to_add.append(('name', incipit))
        fixes.append(f'name: {incipit}  (from directory)')

    mode = headers.get('mode', '')
    if not mode:
        mode = infer_mode_from_annotation(headers.get('annotation', ''))
        if mode:
            to_add.append(('mode', mode))
            fixes.append(f'mode: {mode}  (from annotation)')
        else:
            problems.append('mode: ???  (could not infer)')

    office_part_raw = headers.get('office-part', '')
    if not office_part_raw:
        part_dir = eng_path.parent.parent.name
        office_part_raw = DIR_TO_OFFICE_PART.get(part_dir, '')
        if office_part_raw:
            to_add.append(('office-part', office_part_raw))
            fixes.append(f'office-part: {office_part_raw}  (from directory)')
        else:
            problems.append(f'office-part: ???  (could not infer from {part_dir})')
    elif not is_recognized_part(office_part_raw):
        problems.append(f'office-part: {office_part_raw}  (not recognized)')

    if fixes or problems:
        if to_add and not dry_run:
            add_gabc_headers(eng_path, to_add)
        if not dry_run:
            print()
            print(RULE)
            print(f'  Headers need attention: {rel}')
            for f in fixes:
                print(f'    ADDED    {f}')
            for p in problems:
                print(f'    FIX      {p}')
            print(f'  Edit the file if needed, then press Enter (or "s" to skip)')
            print(RULE)
            choice = input('  > ').strip().lower()
            if choice == 's':
                return 'SKIPPED    (user skipped after header check)'
            headers, gabc_content = _read_gabc(eng_path)
            incipit = headers.get('name', '') or incipit
            mode = headers.get('mode', '') or mode

    # --- Derive part_code from file headers when possible ---
    latin_sibling = find_latin_sibling(eng_path)
    if latin_sibling:
        lat_headers = parse_gabc_headers(latin_sibling)
        lat_incipit = lat_headers.get('name', '') or kebab_to_incipit(eng_path.parent.name)
        lat_mode    = lat_headers.get('mode', '') or mode
        lat_part    = lat_headers.get('office-part', '')
        if lat_part:
            part_code = normalize_part_code(lat_part)
        group_ids   = lookup_exact(engine, lat_incipit, part_code, lat_mode)
        if not group_ids:
            group_ids = lookup_prefix(engine, lat_incipit, part_code)
    else:
        eng_part = headers.get('office-part', '')
        if eng_part:
            part_code = normalize_part_code(eng_part)
        lat_incipit = kebab_to_incipit(eng_path.parent.name)
        group_ids   = lookup_prefix(engine, lat_incipit, part_code)

    if not group_ids:
        return f'UNMATCHED  (no chant_group found for "{lat_incipit}" / {part_code})'

    # --- Resolve ambiguity ---
    if len(group_ids) > 1:
        already = [cgid for cgid in group_ids if chant_already_exists(engine, cgid, version)]
        if len(already) > 1:
            return f'SKIPPED    (multiple chant_groups already in local_chants: {already})'
        if len(already) == 1:
            chant_group_id = already[0]   # unambiguous via existing row; fall through to update check
        else:
            if dry_run:
                return f'AMBIGUOUS  ({len(group_ids)} chant_groups: {group_ids})'
            print(f'\n  AMBIGUOUS: {rel}')
            eng_body = extract_gabc_body(gabc_content)
            chosen = prompt_chant_group(group_ids, part_code, eng_path, eng_body, engine, counter)
            if chosen is None:
                return 'SKIPPED    (user skipped)'
            chant_group_id = chosen
            headers, gabc_content = _read_gabc(eng_path)  # pick up any edits made during the prompt
    else:
        chant_group_id = group_ids[0]

    # --- Fetch existing row (for update check and source fallback) ---
    existing = fetch_existing_chant(engine, chant_group_id, version)

    # --- Translation source ---
    translation_source, is_text_exact = detect_translation_source(headers)
    if translation_source is None:
        if existing and existing.get('translation_source_code'):
            # Re-use previously recorded source; no need to prompt again
            translation_source = existing['translation_source_code']
            is_text_exact = bool(existing.get('is_text_exact', 1))
        elif dry_run:
            action = 'update' if existing else 'insert'
            return (f'DRY-RUN    would {action} chant_group_id={chant_group_id} '
                    f'translation_source=? (will prompt)')
        else:
            result, is_text_exact = prompt_translation_source(headers, eng_path, source_choices, counter)
            if result == 'SKIP':
                return 'SKIPPED    (user skipped)'
            translation_source = result   # may be None if user chose "n"
            headers, gabc_content = _read_gabc(eng_path)  # pick up any edits made during the prompt

    # --- Build row ---
    mtime = datetime.datetime.fromtimestamp(eng_path.stat().st_mtime)
    row = {
        'uid':                str(uuid.uuid4()),
        'cgid':               chant_group_id,
        'version':            version,
        'incipit':            headers.get('name', '') or incipit,
        'part':               headers.get('office-part', ''),
        'mode':               headers.get('mode', '') or mode or None,
        'transcriber':        headers.get('transcriber', 'Doctor J'),
        'commentary':         headers.get('commentary', '') or None,
        'gabc':               gabc_content,
        'translation_source': translation_source,
        'is_text_exact':      int(is_text_exact),
        'notes':              f'file_mtime: {mtime:%Y-%m-%d %H:%M:%S}',
    }

    # --- Insert or update ---
    if existing is not None:
        if not row_differs(existing, row):
            return f'SKIPPED    (chant_group_id={chant_group_id} version={version} unchanged)'
        if dry_run:
            return f'DRY-RUN    would update chant_group_id={chant_group_id} version={version}'
        update_local_chant(engine, row, existing['local_chant_id'])
        return f'UPDATED    chant_group_id={chant_group_id} translation_source={translation_source}'

    if dry_run:
        return (f'DRY-RUN    would insert chant_group_id={chant_group_id} '
                f'translation_source={translation_source}')

    insert_local_chant(engine, row)
    return f'INSERTED   chant_group_id={chant_group_id} translation_source={translation_source}'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _needs_intervention(dry_run_result: str) -> bool:
    return (dry_run_result.startswith('AMBIGUOUS') or
            'will prompt' in dry_run_result)

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('directory', nargs='?', default=None,
                        help='Chant directory to process (e.g. Communio, Antiphona, '
                             'or parent directory containing all of them). '
                             'Omit to process all known part directories.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without writing to the database')
    parser.add_argument('-v', '--verbose', action='count', default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format='%(levelname)s: %(message)s',
    )

    engine = get_engine('jcost')
    print(f'Connected to liturgio ({"DRY-RUN" if args.dry_run else "LIVE"})\n')

    source_choices = fetch_translation_source_choices(engine)

    # Resolve which directories to scan
    global CHANT_ROOT
    single_chant = None
    if args.directory:
        target = Path(args.directory).resolve()
        if target.name in PARTS:
            CHANT_ROOT = target.parent
            scan_dirs = {target.name: PARTS[target.name]}
        elif any((target / d).is_dir() for d in PARTS):
            CHANT_ROOT = target
            scan_dirs = PARTS
        elif target.parent.name in PARTS:
            # Single chant directory, e.g. Antiphona/in-conspectu-angelorum
            CHANT_ROOT = target.parent.parent
            single_chant = (target, PARTS[target.parent.name])
            scan_dirs = {}
        else:
            parser.error(f'{target} is not a recognized part directory, '
                         f'chant directory, or parent of one')
    else:
        scan_dirs = PARTS

    # Collect all files
    all_files: list[tuple[Path, str]] = []
    if single_chant:
        chant_dir, part_code = single_chant
        for eng_path in sorted(chant_dir.glob('english*.gabc')):
            all_files.append((eng_path, part_code))
    else:
        for part_dir, part_code in scan_dirs.items():
            root = CHANT_ROOT / part_dir
            if root.is_dir():
                for eng_path in sorted(root.glob('*/english*.gabc')):
                    all_files.append((eng_path, part_code))

    # Pre-scan with dry_run to count interventions needed (skipped in dry-run mode)
    counter = None
    if not args.dry_run:
        n_interventions = sum(
            1 for eng_path, part_code in all_files
            if _needs_intervention(process_file(eng_path, part_code, engine, dry_run=True))
        )
        if n_interventions:
            print(f'  {n_interventions} file(s) need intervention\n')
            counter = InterventionCounter(n_interventions)

    counts = {'inserted': 0, 'updated': 0, 'skipped': 0, 'unmatched': 0, 'ambiguous': 0, 'error': 0}

    current_part = None
    for eng_path, part_code in all_files:
        part_dir = eng_path.parts[-3]   # e.g. 'Introitus'
        if part_dir != current_part:
            if current_part is not None:
                print()
            print(f'=== {part_dir} ===')
            current_part = part_dir

        rel = str(eng_path.relative_to(CHANT_ROOT)).replace('\\', '/')
        try:
            status = process_file(eng_path, part_code, engine, args.dry_run, counter, source_choices)
        except Exception as exc:
            status = f'ERROR      {exc}'
            log.exception('Error processing %s', rel)

        tag = status.split()[0]
        if tag == 'INSERTED':
            counts['inserted'] += 1
        elif tag == 'UPDATED':
            counts['updated'] += 1
        elif tag == 'DRY-RUN':
            counts['inserted'] += 1
        elif tag == 'SKIPPED':
            counts['skipped'] += 1
        elif tag == 'UNMATCHED':
            counts['unmatched'] += 1
        elif tag == 'AMBIGUOUS':
            counts['ambiguous'] += 1
        else:
            counts['error'] += 1

        print(f'  {rel}')
        print(f'    {status}')
    print()

    action = 'would insert/update' if args.dry_run else 'inserted'
    parts = [
        f"{counts['inserted']} {action}",
    ]
    if counts['updated']:
        parts.append(f"{counts['updated']} updated")
    parts += [
        f"{counts['skipped']} skipped",
        f"{counts['unmatched']} unmatched",
    ]
    if counts['ambiguous']:
        parts.append(f"{counts['ambiguous']} ambiguous (dry-run only)")
    if counts['error']:
        parts.append(f"{counts['error']} errors")
    print('Summary: ' + ', '.join(parts))


if __name__ == '__main__':
    main()
