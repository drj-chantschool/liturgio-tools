"""
Mirror the live GregoBase site (gregobase.selapa.net) into ``gregobase_chants``.

Where each column comes from (see GregoBase's chant.php / download.php, in
https://github.com/drj-chantschool/GregoBase):

- incipit, version, cantusid, remarks, transcriber, office-part (the
  ``<span class="usage CODE">`` code) and copyrighted come from the chant page.
- commentary, mode and mode_var come from the page's "Illuminare Score editor"
  link, whose URL fragment is ``commentary: …`` + ``annotation:`` lines +
  ``%%`` + the GABC. It is the only place the page prints the untruncated
  commentary and the mode variant (``annotation: 8 G``).
- gabc comes from the GABC download(s); a multi-part chant (stored upstream as
  a JSON list of ``['gabc'|'tex', …]`` elements) has one download per element.
- gabc_verses comes from the page's "Neumz" link, whose fragment is every gabc
  element followed by a newline, then a newline, then gabc_verses verbatim.
- duplicateof comes from chant.php's 301 redirect (to the original chant, or to
  scores.php for a retired one, stored as -1).

Never scraped, because the site does not expose them: ``initial`` and
``tex_verses`` (TeX source that only appears rendered inside the PDF/PNG). The
scraper leaves both untouched and reports chants whose verses it could not
capture, so they can be filled from a GregoBase SQL dump instead. For those it
also stores a reconstruction from the PDFs (tex_verses_pdf.py) in the separate
``tex_verses_pdf`` column, which is never a source and never touches tex_verses.
"""
import hashlib
import json
import re
import time
from urllib.parse import unquote, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from sqlalchemy import MetaData, Table, create_engine, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
import logging


BASE = "https://gregobase.selapa.net"
UA = {
    "User-Agent": "liturgio-gregobase-sync/0.1 (contact: <EMAIL> @gmail.com; rate-limited)"
}

SLEEP_SECONDS = 0.35
TIMEOUT_SECONDS = 30

# If True, a sidecar table records which ids have been fetched so an interrupted
# run can resume; pass --refresh to fetch everything again.
USE_SYNC_STATE = True

# Your engine (must be writable if you want to upsert)
CONNECTION_STRING = "mysql+mysqlconnector://{user}:{password}@localhost:3306/liturgio"
ECHO_SQL = True

# Incipit listing letters GregoBase uses (note: no X/Y)
INICIPIT_LETTERS = ["none", "A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","Z"]

# GregoBase include/txt.php: office-part code -> $txt['usage'] (the "Usage" name
# shown on the page and written as office-part: in GABC downloads) ...
USAGE_NAMES = {
    'al': 'Alleluia', 'an': 'Antiphona', 'ca': 'Canticum', 'co': 'Communio',
    'gr': 'Graduale', 'hy': 'Hymnus', 'in': 'Introitus', 'im': 'Improperia',
    'ky': 'Kyriale', 'of': 'Offertorium', 'pa': 'Prosa', 'ps': 'Psalmus',
    're': 'Responsorium', 'rb': 'Responsorium breve', 'rh': 'Rhythmus',
    'se': 'Sequentia', 'su': 'Supplicatio', 'tr': 'Tractus', 'tp': 'Tropa',
    'or': 'Toni Communes', 'pr': 'Praefationes', 'va': 'Varia',
}
# ... and $txt['usage_s'], the abbreviation chant.php emits as the first
# "annotation:" line of the editor links (empty -> no annotation line).
USAGE_ABBREV = {
    'an': 'Ant', 'ca': 'Cant', 'co': 'Comm', 'gr': 'Grad', 'hy': 'Hymn',
    'in': 'Intr', 'of': 'Offert', 're': 'Resp', 'rb': 'R. br', 'se': 'Seq',
    'tr': 'Tract',
}

# Usage name -> code; init() refreshes it from scores.php.
USAGE_MAP: dict[str, str] = {v: k for k, v in USAGE_NAMES.items()}

_ILLUMINARE_PREFIX = "https://editor.sourceandsummit.com/legacy/#"
_NEUMZ_PREFIX = "https://scrib.io/#q="


_session = requests.Session()
_session.headers.update(UA)

def http_get(url: str) -> requests.Response:
    r = _session.get(url, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    time.sleep(SLEEP_SECONDS)
    return r

def http_get_text(url: str) -> str:
    return http_get(url).text

def abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    return BASE + "/" + href


def build_usage_map() -> dict[str, str]:
    html = http_get_text(f"{BASE}/scores.php")
    soup = BeautifulSoup(html, "html.parser")

    usage_map: dict[str, str] = {v: k for k, v in USAGE_NAMES.items()}
    # the "by usage" section links look like: usage.php?id=al
    for a in soup.select("a[href*='usage.php?id=']"):
        name = a.get_text(" ", strip=True)
        href = a.get("href", "")
        m = re.search(r"usage\.php\?id=([^&#]+)", href)
        if name and m:
            usage_map[name] = m.group(1)
    return usage_map

def split_gabc_header_body(gabc_text: str) -> tuple[str, str]:
    if "%%" not in gabc_text:
        return ("", gabc_text.strip())
    head, body = gabc_text.rsplit("%%", 1)
    return head.strip(), body.strip()

def download_content(download_text: str) -> str:
    """The stored gabc content of a download.php response: everything after the
    generated header's ``%%`` line (it may still carry the transcriber's own header)."""
    if "%%\n" in download_text:
        return download_text.split("%%\n", 1)[1]
    return download_text

def parse_header_kv(header_text: str) -> dict[str, str]:
    """
    Parse ``key:value;`` GABC header lines. Values may themselves contain ``;``
    (e.g. ``commentary:Joan. 13, 34; Ps. 118, 1;``), so only one trailing ``;``
    per line is a terminator. The first occurrence of a key wins: GregoBase's
    generated header comes before any header the transcriber left in the gabc.
    """
    out: dict[str, str] = {}
    for line in header_text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if v.endswith(";"):
            v = v[:-1].rstrip()
        if k and k not in out:
            out[k] = v
    return out

def split_mode_and_var(mode_str: str | None) -> tuple[str | None, str | None]:
    """
    "8" -> ("8", None)
    "8G" -> ("8", "G")
    "VIII G" -> ("VIII", "G")
    """
    if not mode_str:
        return None, None
    m = mode_str.strip()
    mm = re.match(r"^(\d+|[IVX]+)\s*([A-Za-z].*)?$", m)
    if not mm:
        return m, None
    mode = mm.group(1)
    var = (mm.group(2) or "").strip() or None
    return mode, var

def parse_mode_annotation(annotation: str | None) -> tuple[str | None, str | None]:
    """
    Invert chant.php's mode annotation: ``T. pereg.`` for mode p, ``C``/``D``/``E``
    upper-cased for modes c/d/e, otherwise ``<mode>[ <mode_var>]``.
    """
    if not annotation or not annotation.strip():
        return None, None
    s = annotation.strip()
    if s == "T. pereg.":
        return "p", None
    mode, _, var = s.partition(" ")
    if mode in ("C", "D", "E"):
        mode = mode.lower()
    return mode, (var.strip() or None)

def parse_illuminare_fragment(fragment: str, office_part: str | None) -> dict:
    """
    Parse the "Illuminare Score editor" link fragment chant.php builds:
    ``[commentary: <c>\\n]`` + ``[annotation: <usage abbrev>\\n]`` +
    ``[annotation: <mode>\\n]`` + ``%%\\n`` + gabc.
    """
    head = fragment.split("%%\n", 1)[0]
    commentary = None
    if head.startswith("commentary: "):
        idx = head.find("\nannotation: ")
        commentary = head[len("commentary: "):idx if idx >= 0 else len(head)]
        commentary = commentary[:-1] if commentary.endswith("\n") else commentary
        head = head[idx + 1:] if idx >= 0 else ""
    annotations = re.findall(r"^annotation: (.*)$", head, flags=re.MULTILINE)
    if office_part and USAGE_ABBREV.get(office_part) and annotations:
        annotations = annotations[1:]
    mode, mode_var = parse_mode_annotation(annotations[0] if annotations else None)
    return {"commentary": commentary or None, "mode": mode, "mode_var": mode_var}

def _link_fragment(soup: BeautifulSoup, prefix: str) -> str | None:
    a = soup.find("a", href=lambda h: bool(h) and h.startswith(prefix))
    return unquote(a["href"][len(prefix):]) if a else None

def parse_chant_page_meta(chant_html: str) -> dict:
    soup = BeautifulSoup(chant_html, "html.parser")

    incipit = None
    h3 = soup.find("h3")
    if h3:
        # The h3 also holds the "report a problem" / "Edit" spans; drop them. Do NOT
        # use get_text(strip=True): format_incipit() wraps "(…)", " Ps." and
        # " sine/cum Alleluia" in <i>, and stripping each text node eats the spaces.
        for span in h3.find_all("span"):
            span.decompose()
        incipit = re.sub(r"\s+", " ", h3.get_text()).strip() or None
        if incipit and re.fullmatch(r"░░\d+░░", incipit):
            incipit = None

    def section(name: str) -> str | None:
        h4 = None
        for tag in soup.find_all("h4"):
            if tag.get_text(strip=True).lower() == name.lower():
                h4 = tag
                break
        if not h4:
            return None

        chunks = []
        for sib in h4.find_next_siblings():
            if sib.name == "h4":
                break
            txt = sib.get_text("\n", strip=True)
            if txt:
                chunks.append(txt)

        return "\n".join(chunks).strip() if chunks else None

    version = section("Version")
    usage = section("Usage")
    remarks = section("Remarks")
    cantusid = section("Cantus ID")
    history = section("History")

    usage_span = soup.select_one("span.usage")
    office_part = None
    if usage_span:
        office_part = next((c for c in usage_span.get("class", []) if c != "usage"), None)

    transcriber = None
    if history:
        m = re.search(r"Original transcriber:\s*(.+)$", history, flags=re.MULTILINE)
        if m:
            transcriber = m.group(1).strip()

    score = soup.find(id="score")
    copyrighted = bool(score and "still under copyright" in score.get_text())

    illuminare = _link_fragment(soup, _ILLUMINARE_PREFIX)
    editor = parse_illuminare_fragment(illuminare, office_part) if illuminare is not None else {}

    return {
        "incipit": incipit,
        "version": version,
        "usage": usage,
        "office_part": office_part,
        "remarks": remarks,
        "commentary": editor.get("commentary"),
        "transcriber": transcriber,
        "cantusid": cantusid,
        "copyrighted": copyrighted,
        "has_editor_links": illuminare is not None,
        "mode": editor.get("mode"),
        "mode_var": editor.get("mode_var"),
        "neumz_gabc": _link_fragment(soup, _NEUMZ_PREFIX),
    }

def extract_download_links(chant_html: str) -> dict:
    """
    GABC download links. A plain chant has ``GABC`` (gabc + gabc_verses) and, when
    it has verses, ``GABC (1st verse only)``. A multi-part chant has one
    ``…&elem=N`` link per gabc element (labelled ``GABC`` or ``Element N``) and its
    1st-verse link returns nothing.
    """
    soup = BeautifulSoup(chant_html, "html.parser")
    out: dict = {}
    elems: list[tuple[int, str]] = []

    for a in soup.select("a[href*='download.php']"):
        href = a["href"]
        q = parse_qs(urlparse(href).query)
        if q.get("format") != ["gabc"]:
            continue
        if "elem" in q:
            elems.append((int(q["elem"][0]), abs_url(href)))
        elif q.get("1verse") == ["1"]:
            out["gabc_1verse"] = abs_url(href)
        else:
            out["gabc_full"] = abs_url(href)

    if elems:
        out["gabc_elems"] = [u for _, u in sorted(elems)]
    return out

def has_verse_links(chant_html: str) -> bool:
    """chant.php prints the "(1st verse only)" links iff gabc_verses or tex_verses is set."""
    return "1st verse only" in chant_html

def split_neumz_verses(neumz_gabc: str | None, elements: list[str]) -> tuple[bool, str | None]:
    """
    chant.php's Neumz link is ``''.join(e + '\\n' for e in elements) + '\\n' + gabc_verses``.
    Returns (matched, gabc_verses); matched is False when the link doesn't start
    with the downloaded elements (e.g. the chant was edited between requests).
    """
    if neumz_gabc is None:
        return False, None
    prefix = "".join(e + "\n" for e in elements) + "\n"
    if not neumz_gabc.startswith(prefix):
        return False, None
    return True, (neumz_gabc[len(prefix):] or None)


def fetch_chant_payloads(chant_id: int) -> dict:
    resp = http_get(f"{BASE}/chant.php?id={chant_id}")
    if resp.history:
        # chant.php 301s a duplicate to its original, or to scores.php if retired.
        final = urlparse(resp.url)
        target = parse_qs(final.query).get("id", [None])[0]
        dup = int(target) if final.path.endswith("chant.php") and target else -1
        return {"duplicateof": dup}

    chant_html = resp.text
    meta = parse_chant_page_meta(chant_html)
    links = extract_download_links(chant_html)
    payload = {
        "chant_html": chant_html,
        "meta": meta,
        "headers_text": None,
        "elements": None,
        "gabc_body": None,
        "gabc_verses": None,
        "verses_unmatched": False,
        "header_kv": {},
    }

    if links.get("gabc_elems"):
        texts = [http_get_text(u) for u in links["gabc_elems"]]
        elements = [download_content(t) for t in texts]
        first = texts[0]
    elif links.get("gabc_full"):
        # The 1st-verse download is exactly the stored gabc; the full one appends verses.
        first = http_get_text(links.get("gabc_1verse") or links["gabc_full"])
        elements = [download_content(first)]
    else:
        return payload

    headers_text, body = split_gabc_header_body(first)
    matched, verses = split_neumz_verses(meta["neumz_gabc"], elements)
    payload.update(
        headers_text=headers_text or None,
        elements=elements,
        gabc_body=body or None,
        gabc_verses=verses,
        verses_unmatched=not matched,
        header_kv=parse_header_kv(headers_text or ""),
    )
    return payload

def merge_gabc_elements(elements: list[str], existing_gabc: str | None) -> str:
    """
    Store a multi-part chant as GregoBase does: a JSON list of elements. The site
    never exposes the ``tex`` elements, so keep the ones already in the DB when
    the number of gabc elements still matches, and replace only the gabc ones.
    """
    try:
        old = json.loads(existing_gabc) if existing_gabc else None
    except ValueError:
        old = None
    if isinstance(old, list) and sum(1 for e in old if e and e[0] == "gabc") == len(elements):
        it = iter(elements)
        merged = [["gabc", next(it)] + list(e[2:]) if e and e[0] == "gabc" else e for e in old]
        return json.dumps(merged, ensure_ascii=False)
    return json.dumps([["gabc", e] for e in elements], ensure_ascii=False)

def build_row_dict(chant_id: int, existing: dict | None = None) -> dict:
    """
    Scrape one chant into a row for upsert_chant(). Only keys the page actually
    determines are present, so absent ones (initial, tex_verses, …) are left as
    they are in the DB. A duplicate returns just ``{id, duplicateof}``.

    Diagnostic keys starting with ``_`` are not columns.
    """
    existing = existing or {}
    payload = fetch_chant_payloads(chant_id)
    if "meta" not in payload:
        return {"id": chant_id, "duplicateof": payload["duplicateof"]}

    meta = payload["meta"]
    hkv = payload["header_kv"]

    office_part = meta.get("office_part") or USAGE_MAP.get(meta.get("usage") or "", meta.get("usage"))
    row = {
        "id": chant_id,
        "cantusid": meta.get("cantusid"),
        "version": meta.get("version") or hkv.get("version"),
        "incipit": meta.get("incipit") or hkv.get("name") or f"(id={chant_id})",
        "office-part": office_part,
        "transcriber": meta.get("transcriber") or hkv.get("transcriber"),
        "headers": payload["headers_text"],
        "gabc_verses": payload["gabc_verses"],
        "remarks": meta.get("remarks"),
        "copyrighted": int(bool(meta.get("copyrighted"))),
        "duplicateof": None,
    }
    if meta.get("has_editor_links"):
        row["commentary"] = meta.get("commentary")
        row["mode"] = meta.get("mode")
        row["mode_var"] = meta.get("mode_var")
    else:
        # No editor links (e.g. copyrighted): fall back to the download header, which
        # has the mode but not its variant.
        row["mode"], row["mode_var"] = split_mode_and_var(hkv.get("mode"))
        if hkv.get("commentary"):
            row["commentary"] = hkv["commentary"]

    elements = payload["elements"]
    if elements and (len(elements) > 1 or (existing.get("gabc") or "").startswith("[")):
        row["gabc"] = merge_gabc_elements(elements, existing.get("gabc"))
    else:
        # One element (the usual case, even when the site serves it via &elem=1):
        # store the plain body, with its header in `headers`.
        row["gabc"] = payload["gabc_body"]

    row["_verses_unmatched"] = payload["verses_unmatched"] and bool(elements)
    row["_tex_verses_missing"] = (
        has_verse_links(payload["chant_html"]) and not payload["gabc_verses"]
        and not existing.get("tex_verses")
    )
    if row["_tex_verses_missing"]:
        # The real TeX source isn't on the site: reconstruct a lookalike from the
        # PDFs into its own column, so it can never overwrite a clean tex_verses.
        from tex_verses_pdf import fetch_tex_verses
        row["tex_verses_pdf"] = fetch_tex_verses(chant_id, lambda url: http_get(url).content)
    return row


def scrape_all_ids() -> list[int]:
    ids = set()

    for letter in INICIPIT_LETTERS:
        url = f"{BASE}/incipit.php?letter={letter}"
        html = http_get_text(url)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='chant.php?id=']"):
            m = re.search(r"id=(\d+)", a.get("href", ""))
            if m:
                ids.add(int(m.group(1)))

    return sorted(ids)


def sha256_optional(s: str | None) -> str | None:
    if s is None:
        return None
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()

def ensure_sync_state_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS gregobase_sync_state (
          id INT PRIMARY KEY,
          gabc_sha256 CHAR(64) NOT NULL,
          verses_sha256 CHAR(64) NULL,
          fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
    """))

def upsert_sync_state(conn, sync_state: Table, chant_id: int, gabc: str | None, gabc_verses: str | None):
    new_g = sha256_optional(gabc) or ("0" * 64)
    new_v = sha256_optional(gabc_verses)

    ins = mysql_insert(sync_state).values(
        id=chant_id,
        gabc_sha256=new_g,
        verses_sha256=new_v,
    )

    stmt = ins.on_duplicate_key_update(
        gabc_sha256 = ins.inserted.gabc_sha256,
        verses_sha256 = ins.inserted.verses_sha256,
    )

    conn.execute(stmt)


# Content fields that must never be overwritten with NULL if the DB already has a
# value (a NULL here usually means a failed download, not a deletion upstream).
_NULL_PROTECT = {"gabc", "headers", "gabc_verses", "mode", "mode_var", "cantusid", "commentary",
                 "tex_verses_pdf"}

def upsert_chant(conn, chants: Table, row: dict, exists: bool):
    row = {k: v for k, v in row.items() if not k.startswith("_")}
    if set(row) == {"id", "duplicateof"}:
        # A duplicate/retired chant: GregoBase no longer serves its content.
        if exists:
            conn.execute(update(chants).where(chants.c.id == row["id"])
                         .values(duplicateof=row["duplicateof"]))
        return
    update_cols = {}
    for name, val in row.items():
        if name == "id":
            continue
        if name in _NULL_PROTECT and val is None:
            continue
        update_cols[name] = val
    if exists:
        conn.execute(update(chants).where(chants.c.id == row["id"]).values(**update_cols))
    else:
        conn.execute(mysql_insert(chants).values(**row))

def get_synced_ids(conn, sync_state: Table) -> set[int]:
    rows = conn.execute(select(sync_state.c.id))
    return {r[0] for r in rows}


def init():
    import argparse
    import keyring, getpass
    global USAGE_MAP
    USAGE_MAP = build_usage_map()

    argparser = argparse.ArgumentParser()
    argparser.add_argument('--user')
    argparser.add_argument(
        "--refresh", action="store_true",
        help="Re-fetch every chant, including ones already recorded in gregobase_sync_state",
    )
    argparser.add_argument(
        "--ids", type=lambda s: [int(x) for x in s.split(",") if x.strip()],
        help="Comma-separated chant ids to (re)fetch, instead of the whole site",
    )
    argparser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v info, -vv debug, -vvv trace)"
    )
    options = argparser.parse_args()

    level_map = {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG,
        3: logging.NOTSET
    }

    logging.basicConfig(level=level_map.get(options.verbose, logging.NOTSET))

    user = options.user or getpass.getuser()
    service = "liturgio-mysql"

    password = keyring.get_password(service, user)
    if password is None:
        password = getpass.getpass(f"Enter password for {user}: ")
        keyring.set_password(service, user, password)

    options.engine = create_engine(
        CONNECTION_STRING.format(user=user, password=password),
        echo=(options.verbose >= 2),
        future=True
    )
    return options

if __name__ == "__main__":
    import time

    start = time.time()
    processed = 0
    updated = 0
    failed = 0
    tex_verses_missing: list[int] = []
    tex_verses_rebuilt: set[int] = set()
    verses_unmatched: list[int] = []

    md = MetaData()

    options = init()

    engine = options.engine

    chants = Table("gregobase_chants", md, autoload_with=engine)

    sync_state = None
    with engine.begin() as conn:
        if USE_SYNC_STATE:
            ensure_sync_state_table(conn)
            sync_state = Table("gregobase_sync_state", md, autoload_with=engine)

    with engine.connect() as conn:
        existing_rows = {
            r["id"]: dict(r) for r in conn.execute(
                select(chants.c.id, chants.c.gabc, chants.c.tex_verses)).mappings()
        }

    # incipit.php hides duplicates, so also revisit every id we already hold:
    # that is how a chant that became a duplicate (a 301) gets noticed.
    if options.ids:
        ids = sorted(set(options.ids))
    else:
        ids = sorted(set(scrape_all_ids()) | set(existing_rows))

    if USE_SYNC_STATE and not options.refresh and not options.ids:
        with engine.connect() as conn:
            done = get_synced_ids(conn, sync_state)

        ids = [i for i in ids if i not in done]
        print(f"{len(done)} already synced; {len(ids)} remaining (use --refresh to re-fetch all)")

    print(f"Found {len(ids)} chant ids")

    BATCH = 20
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            for i, chant_id in enumerate(ids, 1):
                try:
                    existing = existing_rows.get(chant_id)
                    row = build_row_dict(chant_id, existing)
                    if row.get("_tex_verses_missing"):
                        tex_verses_missing.append(chant_id)
                        if row.get("tex_verses_pdf"):
                            tex_verses_rebuilt.add(chant_id)
                    if row.get("_verses_unmatched"):
                        verses_unmatched.append(chant_id)

                    upsert_chant(conn, chants, row, exists=existing is not None)
                    updated += 1
                    if USE_SYNC_STATE and sync_state is not None:
                        upsert_sync_state(conn, sync_state, chant_id, row.get("gabc"), row.get("gabc_verses"))
                    processed += 1
                except Exception as e:
                    failed += 1
                    print(f"[ERROR] chant {chant_id}: {e}")

                if i % BATCH == 0:
                    now = time.time()
                    elapsed = now - start
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    remaining = len(ids) - processed
                    eta_seconds = remaining / rate if rate > 0 else float("inf")

                    print(
                        f"{processed}/{len(ids)} processed | {updated} updated | "
                        f"{rate:.2f} chants/s | elapsed {elapsed/60:.1f} min | "
                        f"ETA {eta_seconds/60:.1f} min"
                    )

                    tx.commit()
                    tx = conn.begin()
        finally:
            tx.commit()

            elapsed = time.time() - start
            rate = processed / elapsed if elapsed else 0

            print("\n========== FINAL REPORT ==========")
            print(f"Total chants:     {len(ids)}")
            print(f"Processed:        {processed}")
            print(f"Updated:          {updated}")
            print(f"Failed:           {failed}")
            print(f"Elapsed:          {elapsed/60:.1f} minutes")
            print(f"Avg speed:        {rate:.2f} chants/sec")
            print("==================================")
            if tex_verses_missing:
                rebuilt = [i for i in tex_verses_missing if i in tex_verses_rebuilt]
                failed_rebuild = [i for i in tex_verses_missing if i not in tex_verses_rebuilt]
                print(f"\n{len(tex_verses_missing)} chant(s) have TeX verses the site doesn't expose and none "
                      f"are stored; the real source needs a GregoBase SQL dump.")
                print(f"  reconstructed into tex_verses_pdf ({len(rebuilt)}): " + ", ".join(map(str, rebuilt)))
                print(f"  could not reconstruct ({len(failed_rebuild)}): " + ", ".join(map(str, failed_rebuild)))
            if verses_unmatched:
                print(f"\n{len(verses_unmatched)} chant(s) whose Neumz link didn't match the downloaded "
                      f"gabc, so gabc_verses was not updated:")
                print("  " + ", ".join(map(str, verses_unmatched)))
