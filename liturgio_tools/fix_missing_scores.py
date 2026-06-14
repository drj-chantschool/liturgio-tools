#!/usr/bin/env python3
"""
fix_missing_scores.py

Reads a target .tex file, finds missing chant files (both direct
\\gregorioscore{} calls and paths implied by \\psalm, \\psalma, \\hymn,
\\responsory, \\lesson, \\introd), then attempts to fix each one:

  1. Disk fuzzy-match: look for a directory/filename that is a prefix match
     for the imported incipit-dir and/or filename.

  2. DB pull: query gregobase_chants in the local liturgio MySQL database,
     write the .gabc file to the expected location.

When a match is uncertain (imperfect score or multiple candidates) the script
asks you to choose interactively.

Usage:
    python fix_missing_scores.py tenebrae.tex [--dry-run] [--no-db] [-v]
"""

import re
import sys
import json
import argparse
import getpass
import logging
from pathlib import Path

from gabc_tools.gbchant import extract_gabc_body

log = logging.getLogger(__name__)

# Mapping from chant-root subdirectory name to GregoBase office-part code(s).
# A match whose office-part is not in the expected set is never treated as exact.
CHANT_TYPE_OFFICE_PARTS: dict[str, set[str]] = {
    'Antiphona':        {'an'},
    'antiphons-english':{'an'},
    'Gradual':          {'gr'},
    'Graduale':         {'gr'},
    'Introitus':        {'in'},
    'Communio':         {'co'},
    'Offertorium':      {'of'},
    'Responsum':        {'re'},
    'Resp-breve':       {'rb'},
    'Tractus':          {'tr'},
    'Alleluia':         {'al'},
    'hymns':            {'hy'},
    'Kyrie':            {'ky'},
    'Psalms':           {'ps'},
    'Communes':         {'or'},
    'Varia':            {'va'},
}

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def prefix_score(a: str, b: str) -> float:
    """
    [0,1] score: how much the shorter word-list is a prefix of the longer,
    where words are split on '-'.

      'christus-factus'     vs 'christus-factus-est'  -> 1.0
      'a-b-c'               vs 'a-b-c-d-e'            -> 0.6
      'ab-cde'              vs 'ab-xyz'                -> 0.0
    """
    a_w = a.lower().split('-')
    b_w = b.lower().split('-')
    short, long_ = (a_w, b_w) if len(a_w) <= len(b_w) else (b_w, a_w)
    if long_[: len(short)] != short:
        return 0.0
    return len(short) / len(long_)


def filename_score(a: str, b: str) -> float:
    """
    Compare two .gabc filenames (extension stripped).  Splits on '_' and '-'.

      'solesmes'      vs 'solesmes_1974'  -> 1.0  (prefix match)
      'english'       vs 'english-brevis' -> 1.0
      'solesmes'      vs 'english'        -> 0.0
    """
    def tokenise(name: str):
        stem = re.sub(r'\.gabc$', '', name, flags=re.IGNORECASE)
        return re.split(r'[-_]', stem.lower())

    a_w = tokenise(a)
    b_w = tokenise(b)
    short, long_ = (a_w, b_w) if len(a_w) <= len(b_w) else (b_w, a_w)
    if long_[: len(short)] != short:
        return 0.0
    return len(short) / len(long_)


# ---------------------------------------------------------------------------
# Parsing the .tex file
# ---------------------------------------------------------------------------

GREGORIOSCORE_RE = re.compile(r'\\gregorioscore\{([^}]+)\}')

# Patterns for macro calls that imply gregorioscore paths.
# Each entry: (regex, expander_fn)
# The expander returns a list of rel-path strings for a given match.
# 'version' defaults are per loth.cls; choir-only paths are included
# because the script doesn't know the class options.

def _psalm_paths(m: re.Match) -> list[str]:
    ant, psalm, tone = m.group(1), m.group(2), m.group(3)
    ver = m.group(5) or 'solesmes'
    paths = [
        f'Antiphona/{ant}/{ver}.gabc',
        f'Psalms/{psalm}/{tone}.gabc',       # choir mode
    ]
    if ver != 'english':
        paths.append(f'Antiphona/{ant}/english.gabc')
    return paths

def _psalma_paths(m: re.Match) -> list[str]:
    latant, engant, psalm, tone = m.group(1), m.group(2), m.group(3), m.group(4)
    ver = m.group(6) or 'solesmes'
    return [
        f'Antiphona/{latant}/{ver}.gabc',
        f'antiphons-english/{engant}/english.gabc',
        f'Psalms/{psalm}/{tone}.gabc',
    ]

def _hymn_paths(m: re.Match) -> list[str]:
    name = m.group(1)
    ver = m.group(2) or 'english'
    return [
        f'hymns/{name}/{ver}.gabc',
        f'hymns/{name}/{ver}_v1.gabc',
    ]

def _responsory_paths(m: re.Match) -> list[str]:
    name = m.group(1)
    ver = m.group(2) or 'english'
    return [
        f'Resp-breve/{name}/{ver}.gabc',
        f'Resp-breve/{name}/{ver}-extended.gabc',
    ]

def _lesson_paths(m: re.Match) -> list[str]:
    return [f'Lectionis/{m.group(1)}.gabc']

def _introd_paths(m: re.Match) -> list[str]:
    tone = m.group(1)
    return [f'Communes/deus-in-adjutorium/{tone}.gabc']

# (compiled regex, expander, macro_name)
MACRO_PATTERNS = [
    # \psalm{ant}{psalm}{tone}{tonecat}[version]
    (re.compile(
        r'\\psalm\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}(?:\{([^}]*)\})?(?:\[([^\]]+)\])?'
    ), _psalm_paths, r'\psalm'),
    # \psalma{latant}{engant}{psalm}{tone}{tonecat}[version]
    (re.compile(
        r'\\psalma\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}(?:\{([^}]*)\})?(?:\[([^\]]+)\])?'
    ), _psalma_paths, r'\psalma'),
    # \hymn[version]{name}{...} — optional first bracket arg is version
    (re.compile(
        r'\\hymn(?:\[([^\]]+)\])?\{([^}]+)\}'
    ), lambda m: _hymn_paths(
        # reorder: group(1)=name for expander, group(2)=ver
        type('M', (), {'group': lambda self, n: [None, m.group(2), m.group(1)][n]})()
    ), r'\hymn'),
    # \responsory{name}[version]
    (re.compile(
        r'\\responsory\{([^}]+)\}(?:\[([^\]]+)\])?'
    ), _responsory_paths, r'\responsory'),
    # \lesson{name}{title}
    (re.compile(
        r'\\lesson\{([^}]+)\}'
    ), _lesson_paths, r'\lesson'),
    # \introd{tone}
    (re.compile(
        r'\\introd\{([^}]+)\}'
    ), _introd_paths, r'\introd'),
]


class Hit:
    """One missing chant file, with enough context to fix it."""
    def __init__(self, rel_path: str, line_idx: int | None = None,
                 col_start: int | None = None, col_end: int | None = None,
                 macro: str | None = None):
        self.rel_path  = rel_path     # e.g. 'Antiphona/zelus-domine/solesmes.gabc'
        self.line_idx  = line_idx     # 0-based line index in file
        self.col_start = col_start    # char offset of \\gregorioscore (or macro call)
        self.col_end   = col_end
        self.macro     = macro        # None = direct \\gregorioscore; else e.g. '\\psalm'
        # Filled in later:
        self.replacement: str | None = None   # new rel_path (for direct hits)

    @property
    def is_direct(self):
        return self.macro is None

    def __str__(self):
        src = f'line {self.line_idx + 1}' if self.line_idx is not None else '?'
        via = f' (via {self.macro})' if self.macro else ''
        return f'{self.rel_path}{via} [{src}]'


def parse_tex(tex_path: Path) -> tuple[list[str], list[Hit]]:
    """Read .tex and return (lines, list[Hit]) for every gregorioscore path found."""
    with open(tex_path, encoding='utf-8') as f:
        lines = f.readlines()

    hits: list[Hit] = []

    for i, line in enumerate(lines):
        # Direct \gregorioscore
        for m in GREGORIOSCORE_RE.finditer(line):
            hits.append(Hit(
                rel_path=m.group(1),
                line_idx=i,
                col_start=m.start(),
                col_end=m.end(),
                macro=None,
            ))

        # Macro-implied paths
        for pattern, expander, macro_name in MACRO_PATTERNS:
            for m in pattern.finditer(line):
                try:
                    for rp in expander(m):
                        hits.append(Hit(
                            rel_path=rp,
                            line_idx=i,
                            col_start=m.start(),
                            col_end=m.end(),
                            macro=macro_name,
                        ))
                except Exception as exc:
                    log.debug("Expander error for %s at line %d: %s", macro_name, i + 1, exc)

    return lines, hits


def split_path(gabc_rel: str):
    """
    'Gradual/christus-factus/english.gabc' -> ('Gradual', 'christus-factus', 'english.gabc')
    Handles up to 3 parts; returns (None,None,None) if unrecognised.
    """
    parts = gabc_rel.replace('\\', '/').split('/')
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        # e.g. Lectionis/name.gabc
        return parts[0], None, parts[1]
    return None, None, None


# ---------------------------------------------------------------------------
# Disk candidate search
# ---------------------------------------------------------------------------

def disk_candidates(chant_root: Path, chant_type: str,
                    incipit_dir: str | None, filename: str
                    ) -> list[tuple[float, Path]]:
    """
    Return [(score, abs_path), ...] sorted best-first.

    Scores both the directory name (incipit_dir) and the filename
    independently; combined score = dir_score * file_score so that both
    axes matter.
    """
    type_dir = chant_root / chant_type
    if not type_dir.is_dir():
        return []

    results: list[tuple[float, Path]] = []

    for d in type_dir.iterdir():
        if not d.is_dir():
            continue

        # Directory score (skip entirely if 0)
        dir_score = prefix_score(incipit_dir, d.name) if incipit_dir else 1.0
        if dir_score == 0.0:
            continue

        # Filename matching within this directory
        for f in d.glob('*.gabc'):
            f_score = filename_score(filename, f.name)
            if f_score == 0.0:
                continue
            combined = dir_score * f_score
            results.append((combined, f))

    results.sort(key=lambda t: t[0], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Interactive prompting
# ---------------------------------------------------------------------------

def _ask_choice(prompt_header: str, options: list[str], extra: list[str] | None = None) -> int | None:
    """
    Print numbered options, return the chosen index (0-based) into `options`,
    or None if the user chose an extra option (printed after options).

    Returns -1 if the user picked the first extra option (typically "None of the above").
    """
    print()
    print(prompt_header)
    for i, opt in enumerate(options, 1):
        print(f'  {i}. {opt}')
    base = len(options) + 1
    extras = extra or []
    for j, e in enumerate(extras):
        print(f'  {base + j}. {e}')
    print()

    while True:
        raw = input('Choice (or Enter to skip): ').strip()
        if not raw:
            return None
        try:
            n = int(raw)
        except ValueError:
            print('  Please enter a number.')
            continue
        if 1 <= n <= len(options):
            return n - 1
        if extras and len(options) < n <= len(options) + len(extras):
            return -(n - len(options))   # -1 for first extra, -2 for second, etc.
        print(f'  Enter 1–{len(options) + len(extras)} or blank to skip.')


def prompt_disk(candidates: list[tuple[float, Path]],
                chant_root: Path,
                hit: Hit,
                dry_run: bool) -> tuple[str | None, bool]:
    """
    Ask the user to pick a disk candidate.

    Returns (chosen_rel_path_or_None, proceed_to_db).
    proceed_to_db is True when the user picks "None of the above".
    """
    PERFECT = 1.0
    top_score = candidates[0][0]

    # Perfect single match → accept silently
    if top_score == PERFECT and len(candidates) == 1:
        score, path = candidates[0]
        rel = str(path.relative_to(chant_root)).replace('\\', '/')
        if rel != hit.rel_path:
            log.info('  Auto-accepting perfect disk match: %s', rel)
            return rel, False

    # Otherwise, build option list (cap at 8)
    shown = candidates[:8]
    opts = []
    for score, path in shown:
        rel = str(path.relative_to(chant_root)).replace('\\', '/')
        marker = ' [EXACT]' if score == PERFECT else f' [score={score:.2f}]'
        opts.append(f'{rel}{marker}')

    idx = _ask_choice(
        f'Disk matches for  {hit.rel_path}:',
        opts,
        extra=['None of the above (check DB instead)'],
    )

    if idx is None:
        return None, False          # skip entirely
    if idx == -1:
        return None, True           # proceed to DB
    score, path = shown[idx]
    rel = str(path.relative_to(chant_root)).replace('\\', '/')
    return rel, False


def prompt_db(rows_ranked: list[tuple[float, dict]],
              chant_type: str, incipit_dir: str, filename: str,
              chant_root: Path, dry_run: bool, n_neumes: int = 10) -> str | None:
    """
    Ask the user to pick a DB row, write the .gabc, return the rel path or None.
    """
    PERFECT = 1.0
    top_score = rows_ranked[0][0]
    shown = rows_ranked[:8]

    # If perfect single match accept silently; still confirm if multiple are tied
    if top_score == PERFECT and len([r for r in shown if r[0] == PERFECT]) == 1:
        score, row = shown[0]
        log.info('  Auto-accepting perfect DB match: id=%s %r', row['id'], row['incipit'])
        return _commit_db_row(row, chant_type, incipit_dir, filename, chant_root, dry_run)

    current_n = n_neumes
    while True:
        opts = []
        for score, row in shown:
            marker  = ' [EXACT]' if score == PERFECT else f' [score={score:.2f}]'
            part    = row.get('office-part') or ''
            mode    = row.get('mode') or ''
            version = row.get('version') or ''
            neumes  = first_neumes(row, current_n)
            meta    = '  '.join(filter(None, [part,
                                              f'mode {mode}' if mode else '',
                                              f'v:{version}' if version else '']))
            opts.append(f"id={row['id']}  {row['incipit']!r}  {meta}{marker}\n"
                        f"       {neumes}")

        is_english = filename.lower().startswith('english')
        extras = []
        if is_english:
            extras.append('Copy Latin / Paste English')
        extras.append(f'Show more neumes (currently {current_n})')
        extras.append('Skip (leave as missing)')

        idx = _ask_choice(
            f'DB matches for  {chant_type}/{incipit_dir}/{filename}:',
            opts,
            extra=extras,
        )

        # Map negative indices to named actions
        extra_actions = {}
        if is_english:
            extra_actions[-1] = 'copy_latin'
            extra_actions[-2] = 'show_more'
            extra_actions[-3] = 'skip'
        else:
            extra_actions[-1] = 'show_more'
            extra_actions[-2] = 'skip'

        action = extra_actions.get(idx)

        if action == 'show_more':
            current_n = 100
            continue
        if action == 'copy_latin':
            result = _do_copy_latin_paste_english(
                rows_ranked, chant_type, incipit_dir, filename,
                chant_root, dry_run, current_n)
            if result:
                return result
            continue   # re-show prompt on cancel/skip
        if idx is None or action == 'skip':
            return None
        score, row = shown[idx]
        return _commit_db_row(row, chant_type, incipit_dir, filename, chant_root, dry_run)


def _do_copy_latin_paste_english(rows_ranked: list[tuple[float, dict]],
                                 chant_type: str, incipit_dir: str,
                                 eng_filename: str, chant_root: Path,
                                 dry_run: bool, n_neumes: int) -> str | None:
    """
    Secondary prompt: pick a Latin DB row to use as reference, write it as
    solesmes.gabc, print it, then read a paste from stdin and write it to
    eng_filename.  Returns the eng rel-path on success, None on skip.
    """
    PERFECT = 1.0
    shown = rows_ranked[:8]

    opts = []
    for score, row in shown:
        marker  = ' [EXACT]' if score == PERFECT else f' [score={score:.2f}]'
        part    = row.get('office-part') or ''
        mode    = row.get('mode') or ''
        version = row.get('version') or ''
        neumes  = first_neumes(row, n_neumes)
        meta    = '  '.join(filter(None, [part,
                                          f'mode {mode}' if mode else '',
                                          f'v:{version}' if version else '']))
        opts.append(f"id={row['id']}  {row['incipit']!r}  {meta}{marker}\n"
                    f"       {neumes}")

    idx = _ask_choice(
        f'Select Latin source for  {chant_type}/{incipit_dir}/:',
        opts,
        extra=['Cancel'],
    )

    if idx is None or idx == -1:
        return None

    _, row = shown[idx]

    # Determine Latin filename: use 'solesmes.gabc' unless one already exists
    lat_filename = 'solesmes.gabc'
    chant_dir = chant_root / chant_type / incipit_dir
    existing_latin = [f for f in chant_dir.glob('*.gabc')
                      if not f.stem.lower().startswith('english')] if chant_dir.is_dir() else []
    if existing_latin:
        lat_filename = existing_latin[0].name

    lat_path = chant_root / chant_type / incipit_dir / lat_filename
    lat_content = build_gabc_file(row)

    if not dry_run:
        lat_path.parent.mkdir(parents=True, exist_ok=True)
        lat_path.write_text(lat_content, encoding='utf-8')
        print(f'\n  [Latin] Written: {lat_path}')
    else:
        print(f'\n  [dry-run] Would write Latin: {lat_path}')

    print(f'\n  Latin ({lat_filename}):')
    print('  ' + '─' * 64)
    print(lat_content.rstrip())
    print('  ' + '─' * 64)
    print(f'  Paste English gabc (Ctrl+Z then Enter when done; '
          f'Ctrl+Z immediately to skip):')

    lines = []
    try:
        for line in iter(sys.stdin.readline, ''):
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass

    eng_content = ''.join(lines).strip()
    if not eng_content:
        print('  Skipped English paste.')
        return None

    eng_path = chant_root / chant_type / incipit_dir / eng_filename
    if not dry_run:
        eng_path.write_text(eng_content + '\n', encoding='utf-8')
        print(f'  [English] Written: {eng_path}')
    else:
        print(f'  [dry-run] Would write English: {eng_path}')

    return f'{chant_type}/{incipit_dir}/{eng_filename}'


def try_paste_english(chant_root: Path, chant_type: str,
                      incipit_dir: str | None, filename: str,
                      dry_run: bool) -> str | None:
    """
    When an English .gabc is missing, look for a sibling Latin file in the
    same directory, print it, and let the user paste the English version.

    Returns 'written', 'skip', or None (caller should fall through to normal matching).
    """
    if incipit_dir is None:
        return None

    chant_dir = chant_root / chant_type / incipit_dir
    if not chant_dir.is_dir():
        return None

    # Find a non-english sibling .gabc to use as the reference
    latin_file = None
    for f in sorted(chant_dir.glob('*.gabc')):
        if not f.stem.lower().startswith('english'):
            latin_file = f
            break

    if latin_file is None:
        return None

    print(f'\n  Latin version ({latin_file.name}):')
    print('  ' + '─' * 64)
    print(latin_file.read_text(encoding='utf-8').rstrip())
    print('  ' + '─' * 64)
    print(f'  Paste English gabc (Ctrl+Z then Enter when done; '
          f'Ctrl+Z immediately to skip):')

    lines = []
    try:
        for line in iter(sys.stdin.readline, ''):
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass

    content = ''.join(lines).strip()
    if not content:
        print('  Skipped.')
        return 'skip'

    out_file = chant_dir / filename
    if not dry_run:
        out_file.write_text(content + '\n', encoding='utf-8')
        print(f'  Written: {out_file}')
    else:
        print(f'  [dry-run] Would write {out_file}')
    return 'written'


def _commit_db_row(row, chant_type: str, incipit_dir: str, filename: str,
                   chant_root: Path, dry_run: bool) -> str:
    out_dir  = chant_root / chant_type / incipit_dir
    out_file = out_dir / filename
    rel      = f'{chant_type}/{incipit_dir}/{filename}'

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file.write_text(build_gabc_file(row), encoding='utf-8')
        print(f'  [DB] Wrote {out_file}')
    else:
        print(f'  [DB dry-run] Would write {out_file}  (id={row["id"]})')
    return rel


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_engine(db_name: str):
    import keyring
    from sqlalchemy import create_engine, text, exc as sa_exc

    user    = 'liturgio_ro'
    service = 'liturgio-mysql'

    while True:
        password = keyring.get_password(service, user)
        if password is None:
            password = getpass.getpass(f'MySQL password for {user}@localhost/{db_name}: ')
            keyring.set_password(service, user, password)

        conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
        engine   = create_engine(conn_str, future=True)
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            return engine
        except (sa_exc.OperationalError, sa_exc.ProgrammingError) as exc:
            if _is_auth_error(exc):
                print(f'Access denied for {user} — removing stored password and retrying.')
                keyring.delete_password(service, user)
                # loop back to re-prompt
            else:
                raise


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception looks like a MySQL authentication failure."""
    msg = str(exc).lower()
    return 'access denied' in msg or '1045' in msg


def incipit_dir_to_text(incipit_dir: str) -> str:
    """'christus-factus-est' -> 'Christus factus est'"""
    words = incipit_dir.replace('-', ' ').split()
    return ' '.join(w.capitalize() if i == 0 else w for i, w in enumerate(words) if w)


def first_neumes(row, n: int = 10) -> str:
    """
    Return the first n syllable(neume) pairs from the gabc body, preserving word
    boundaries: syllables within the same word are concatenated without spaces,
    words are separated by a single space.
    e.g. 'Chris(g)tus(h) fac(h)tus(i) est(g)' (not 'Chris(g) tus(h) fac(h)...')
    """
    body = extract_gabc_body(row.get('gabc') or '')
    tokens = []   # list of (word_start: bool, syllable, neume)
    for m in re.finditer(r'((?:[^()<>]|<[^>]*>)*)\(([^)]*)\)', body):
        syllable = m.group(1)
        neume    = m.group(2)
        syl_stripped = syllable.strip()
        # Skip clefs (c4, f3, …), barlines (:: : , . ;), annotation markers
        if not syl_stripped or re.fullmatch(r'[,.:;`*]+', neume) or re.fullmatch(r'[cf]\d', neume):
            continue
        # A word boundary exists when there is whitespace before this match in
        # the original body (i.e. preceding text of the match contains space).
        word_start = bool(re.search(r'\s', syllable)) or not tokens
        tokens.append((word_start, syl_stripped, neume))
        if len(tokens) >= n:
            break

    parts = []
    for word_start, syllable, neume in tokens:
        token = f'{syllable}({neume})'
        if word_start and parts:
            parts.append(' ' + token)
        else:
            parts.append(token)
    return ''.join(parts)


def build_gabc_header(row) -> str:
    """
    Reconstruct a gabc header from DB columns.
    Uses the stored `headers` text if present; otherwise synthesises one
    from individual columns to match the format of hand-written files, e.g.:
        name:Zelus domus tuae;
        office-part:Antiphona;
        mode:8;
        book:The Liber Usualis, 1961, p. 626;
        transcriber:Andrew Hinkley;
    """
    headers = (row.get('headers') or '').strip()
    if headers:
        return headers

    # Synthesise from individual columns
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
    return '\n'.join(lines)


def build_gabc_file(row) -> str:
    parts = []
    header = build_gabc_header(row)
    if header:
        parts.append(header)
    parts.append('%%')
    parts.append(extract_gabc_body(row.get('gabc') or ''))
    return '\n'.join(parts) + '\n'


def db_candidates(engine, chant_type: str, incipit_dir: str, filename: str) -> list[tuple[float, dict]]:
    """Return [(score, row), ...] sorted best-first."""
    from sqlalchemy import text

    search = incipit_dir_to_text(incipit_dir)
    words  = search.split()
    prefix = ' '.join(words[:min(3, len(words))])
    first  = words[0] if words else ''

    query = text("""
        SELECT id, incipit, headers, gabc, `office-part`, mode, version, transcriber
        FROM gregobase_chants
        WHERE LOWER(incipit) LIKE LOWER(:pat)
        ORDER BY LENGTH(incipit)
        LIMIT 30
    """)

    seen_ids: set = set()
    rows: list = []
    with engine.connect() as conn:
        for pat in dict.fromkeys([f'{prefix}%', f'{first}%']):  # dedup patterns
            for r in conn.execute(query, {'pat': pat}).mappings():
                if r['id'] not in seen_ids:
                    seen_ids.add(r['id'])
                    rows.append(r)

    expected_parts = CHANT_TYPE_OFFICE_PARTS.get(chant_type)

    def score(r):
        db_dir = '-'.join((r['incipit'] or '').lower().split())
        s = prefix_score(incipit_dir, db_dir)
        if s > 0 and expected_parts:
            row_part = (r.get('office-part') or '').strip().lower()
            if row_part not in expected_parts:
                s = min(s, 0.85)   # cap below 1.0 so it's never treated as exact
        return s

    ranked = sorted(rows, key=score, reverse=True)
    return [(score(r), dict(r)) for r in ranked if score(r) > 0.0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('tex_file')
    parser.add_argument('--chant-root', default=None,
                        help='Root of chant directories (default: .tex file directory)')
    parser.add_argument('--db-name', default='liturgio')
    parser.add_argument('--no-db', action='store_true',
                        help='Skip DB lookup')
    parser.add_argument('--neumes', type=int, default=10, metavar='N',
                        help='Neumes to preview per DB candidate (default: 10)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Do not write any files or edit the .tex')
    parser.add_argument('-v', '--verbose', action='count', default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format='%(levelname)s: %(message)s',
    )

    tex_path   = Path(args.tex_file).resolve()
    chant_root = Path(args.chant_root).resolve() if args.chant_root else tex_path.parent

    if not tex_path.exists():
        sys.exit(f'Error: {tex_path} not found')

    lines, hits = parse_tex(tex_path)
    if not hits:
        print('No gregorioscore paths found (direct or via macros).')
        return

    # De-duplicate: same rel_path may appear multiple times (e.g. psalm repeated antiphon)
    seen: set[str] = set()
    missing: list[Hit] = []
    for hit in hits:
        full = chant_root / hit.rel_path
        if not full.exists() and hit.rel_path not in seen:
            seen.add(hit.rel_path)
            missing.append(hit)

    total = len({h.rel_path for h in hits})
    if not missing:
        print(f'All {total} chant files are present on disk.')
        return

    print(f'{len(missing)} missing file(s) out of {total} unique paths.\n')

    engine = None
    if not args.no_db:
        try:
            engine = get_engine(args.db_name)
            print(f'DB connected: {args.db_name}\n')
        except Exception as exc:
            print(f'Warning: DB unavailable ({args.db_name}): {exc}\n')
            ans = input('Continue without DB? [Y/n] ').strip().lower()
            if ans and ans.startswith('n'):
                sys.exit('Aborted.')

    for hit in missing:
        print(f'--- Missing: {hit}')

        chant_type, incipit_dir, filename = split_path(hit.rel_path)
        if chant_type is None or filename is None:
            print('  (unrecognised path structure, skipping)\n')
            continue

        # ---- English paste (when Latin sibling exists on disk) ----
        if filename.lower().startswith('english'):
            paste_result = try_paste_english(
                chant_root, chant_type, incipit_dir, filename, args.dry_run)
            if paste_result in ('written', 'skip'):
                print()
                continue

        # ---- Step 1: Disk ----
        candidates = disk_candidates(chant_root, chant_type, incipit_dir, filename)
        proceed_to_db = False

        if candidates:
            chosen_rel, proceed_to_db = prompt_disk(candidates, chant_root, hit, args.dry_run)
            if chosen_rel and chosen_rel != hit.rel_path:
                if hit.is_direct:
                    if args.dry_run:
                        print(f'  [dry-run] Would replace path: {hit.rel_path} -> {chosen_rel}')
                    else:
                        hit.replacement = chosen_rel
                        print(f'  [DISK] Will update import to: {chosen_rel}')
                else:
                    print(f'  [DISK] Suggestion: rename macro arg so it resolves to {chosen_rel}')
                    print(f'         (automatic edit not possible for {hit.macro} calls)')
                print()
                continue
            elif chosen_rel == hit.rel_path:
                print('  File already at expected path — no change needed.\n')
                continue
            elif chosen_rel is None and not proceed_to_db:
                print('  Skipped.\n')
                continue
        else:
            proceed_to_db = True
            print('  No disk match found.')

        # ---- Step 2: DB ----
        if not proceed_to_db or engine is None:
            if engine is None and proceed_to_db:
                print('  (DB not available)\n')
            print()
            continue

        if incipit_dir is None:
            print('  (cannot query DB without incipit directory)\n')
            continue

        try:
            ranked = db_candidates(engine, chant_type, incipit_dir, filename)
        except Exception as exc:
            log.error('  DB query failed: %s', exc)
            print()
            continue

        if not ranked:
            print('  No DB matches found.\n')
            continue

        result_path = prompt_db(ranked, chant_type, incipit_dir, filename,
                                chant_root, args.dry_run, args.neumes)
        if result_path:
            # File written (or dry-run reported); import path unchanged for direct hits
            # since we wrote to the exact location the .tex expects.
            pass
        else:
            print('  No DB fix applied.')
        print()

    # ---- Apply path replacements to the .tex file ----
    to_replace = [h for h in missing if h.is_direct and h.replacement]
    if to_replace:
        if args.dry_run:
            print('\n[dry-run] Would apply these import replacements:')
            for h in to_replace:
                print(f'  Line {h.line_idx + 1}: {h.rel_path}  ->  {h.replacement}')
        else:
            for h in to_replace:
                lines[h.line_idx] = lines[h.line_idx].replace(
                    f'\\gregorioscore{{{h.rel_path}}}',
                    f'\\gregorioscore{{{h.replacement}}}',
                )
            with open(tex_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print(f'\nUpdated {tex_path} ({len(to_replace)} import(s) replaced).')


if __name__ == '__main__':
    main()
