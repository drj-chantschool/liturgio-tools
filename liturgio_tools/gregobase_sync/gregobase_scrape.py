import hashlib
import re
import time

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
import logging


BASE = "https://gregobase.selapa.net"
UA = {
    "User-Agent": "liturgio-gregobase-sync/0.1 (contact: <EMAIL> @gmail.com; rate-limited)"
}

SLEEP_SECONDS = 0.35
TIMEOUT_SECONDS = 30

# If True, uses a sidecar table to skip unchanged chants.
USE_SYNC_STATE = True

# Your engine (must be writable if you want to upsert)
CONNECTION_STRING = "mysql+mysqlconnector://{user}:{password}@localhost:3306/liturgio"
ECHO_SQL = True

# Incipit listing letters GregoBase uses (note: no X/Y)
INICIPIT_LETTERS = ["none", "A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","Z"]


_session = requests.Session()
_session.headers.update(UA)

def http_get_text(url: str) -> str:
    r = _session.get(url, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    time.sleep(SLEEP_SECONDS)
    return r.text

def abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    return BASE + "/" + href


def build_usage_map() -> dict[str, str]:
    html = http_get_text(f"{BASE}/scores.php")
    soup = BeautifulSoup(html, "html.parser")

    usage_map: dict[str, str] = {}
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

def parse_header_kv(header_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in header_text.replace("\r\n", "\n").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip().lower()] = v.strip()
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

def html_node_to_tex(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    if isinstance(node, Tag):
        name = node.name.lower()

        inner = "".join(html_node_to_tex(c) for c in node.children)

        if name == "i":
            return r"{\it " + inner + "}"
        if name == "b":
            return r"{\bf " + inner + "}"
        if name == "br":
            return r"\\"
        return inner

    return ""

VERSE_LINE_RE = re.compile(r"^\s*(\d+)\s+")
def extract_tex_verses_from_chant_html(chant_html: str) -> str | None:
    soup = BeautifulSoup(chant_html, "html.parser")

    # Candidate blocks: paragraphs / divs / spans that contain a line starting with "2 "
    candidates = []
    for tag in soup.find_all(["p", "div", "span"]):
        txt = tag.get_text(" ", strip=True)
        if VERSE_LINE_RE.match(txt) and txt.startswith("2 "):
            candidates.append(tag)

    if not candidates:
        return None

    block = max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
    block_tex = "".join(html_node_to_tex(c) for c in block.children).strip()

    # Normalize whitespace a bit
    block_tex = re.sub(r"\r\n", "\n", block_tex)
    block_tex = re.sub(r"[ \t]+", " ", block_tex)
    block_tex = re.sub(r"\n\s*\n+", "\n\n", block_tex).strip()

    parts = re.split(r"\n(?=\s*\d+\s)", block_tex)
    parts = [p.strip() for p in parts if p.strip()]

    out_parts = []
    for p in parts:
        m = re.match(r"^\s*(\d+)\s+(.*)$", p, flags=re.DOTALL)
        if not m:
            continue
        n = int(m.group(1))
        if n >= 2:
            out_parts.append(p)

    if not out_parts:
        return None

    return "\\\\\n\\\\\n".join(v.rstrip().rstrip("\\") + r"\\" for v in out_parts)

def parse_chant_page_meta(chant_html: str) -> dict[str, str | None]:
    soup = BeautifulSoup(chant_html, "html.parser")

    h3 = soup.find("h3")
    incipit = h3.get_text(strip=True) if h3 else None

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
    commentary = section("Commentary")
    history = section("History")

    transcriber = None
    if history:
        m = re.search(r"Original transcriber:\s*(.+)$", history, flags=re.MULTILINE)
        if m:
            transcriber = m.group(1).strip()

    return {
        "incipit": incipit,
        "version": version,
        "usage": usage,
        "remarks": remarks,
        "commentary": commentary,
        "transcriber": transcriber,
    }

def extract_download_links(chant_html: str) -> dict[str, str]:
    soup = BeautifulSoup(chant_html, "html.parser")
    out: dict[str, str] = {}

    for a in soup.select("a[href]"):
        txt = a.get_text(" ", strip=True)
        href = a["href"]
        if txt == "GABC":
            out["gabc_full"] = abs_url(href)
        elif txt == "GABC (1st verse only)":
            out["gabc_1verse"] = abs_url(href)

    return out


def fetch_chant_payloads(chant_id: int) -> dict:
    chant_html = http_get_text(f"{BASE}/chant.php?id={chant_id}")
    meta = parse_chant_page_meta(chant_html)
    links = extract_download_links(chant_html)

    full_url = links.get("gabc_full")
    if not full_url:
        return {
            "chant_html": chant_html,
            "meta": meta,
            "headers_text": None,
            "gabc_body": None,
            "gabc_verses_tail": None,
            "header_kv": {},
        }

    full_text = http_get_text(full_url)
    full_head, full_body = split_gabc_header_body(full_text)

    headers_text = full_head
    gabc_body = full_body
    gabc_verses_tail = None

    one_url = links.get("gabc_1verse")
    if one_url:
        one_text = http_get_text(one_url)
        one_head, one_body = split_gabc_header_body(one_text)

        one_body_s = (one_body or "").strip()
        full_body_s = (full_body or "").strip()

        if one_body_s and one_body_s == full_body_s:
            headers_text = one_head or full_head
            gabc_body = full_body
            gabc_verses_tail = None
        elif one_body_s and full_body.startswith(one_body):
            headers_text = one_head or full_head
            gabc_body = one_body
            gabc_verses_tail = full_body[len(one_body):].lstrip() or None
        else:
            headers_text = full_head
            gabc_body = full_body
            gabc_verses_tail = None


    header_kv = parse_header_kv(headers_text or "")

    return {
        "meta": meta,
        "headers_text": headers_text or None,
        "gabc_body": gabc_body or None,
        "gabc_verses_tail": gabc_verses_tail,
        "header_kv": header_kv,
    }

def build_row_dict(chant_id: int) -> dict:
    payload = fetch_chant_payloads(chant_id)
    meta = payload["meta"]
    hkv = payload["header_kv"]

    incipit = meta.get("incipit") or hkv.get("name") or f"(id={chant_id})"
    version = meta.get("version") or hkv.get("version")
    office_part = hkv.get("office-part") or meta.get("usage")
    office_part_code = USAGE_MAP.get(office_part, office_part)
    mode, mode_var = split_mode_and_var(hkv.get("mode"))
    commentary = meta.get("commentary") or hkv.get("commentary")
    transcriber = meta.get("transcriber") or hkv.get("transcriber")

    return {
        "id": chant_id,
        "cantusid": hkv.get("cantusid"),
        "version": version,
        "incipit": incipit,
        "initial": 1,
        "office-part": office_part_code,
        "mode": mode,
        "mode_var": mode_var,
        "transcriber": transcriber,
        "commentary": commentary,
        "headers": payload["headers_text"],
        "gabc": payload["gabc_body"],
        "gabc_verses": payload["gabc_verses_tail"],
        "tex_verses": None,
        "remarks": meta.get("remarks"),
        "copyrighted": 0,
        "duplicateof": None,
    }


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

def should_update(conn, sync_state: Table, chant_id: int, gabc: str | None, gabc_verses: str | None) -> bool:
    new_g = sha256_optional(gabc) or ("0" * 64)
    new_v = sha256_optional(gabc_verses)

    row = conn.execute(
        select(sync_state.c.gabc_sha256, sync_state.c.verses_sha256).where(sync_state.c.id == chant_id)
    ).first()

    if row is None:
        return True

    old_g, old_v = row
    return old_g != new_g or old_v != new_v

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


def upsert_chant(conn, chants: Table, row: dict):
    stmt = mysql_insert(chants).values(**row)
    update_cols = {c.name: stmt.inserted[c.name] for c in chants.columns if c.name != "id"}
    conn.execute(stmt.on_duplicate_key_update(**update_cols))

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
    skipped = 0

    md = MetaData()

    options = init()

    engine = options.engine

    chants = Table("gregobase_chants", md, autoload_with=engine)

    sync_state = None
    with engine.begin() as conn:
        if USE_SYNC_STATE:
            ensure_sync_state_table(conn)
            sync_state = Table("gregobase_sync_state", md, autoload_with=engine)

    ids = scrape_all_ids()

    if USE_SYNC_STATE:
        with engine.connect() as conn:
            done = get_synced_ids(conn, sync_state)

        ids = [i for i in ids if i not in done]
        print(f"{len(done)} already synced; {len(ids)} remaining")

    print(f"Found {len(ids)} chant ids")

    BATCH = 20
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            for i, chant_id in enumerate(ids, 1):
                try:
                    row = build_row_dict(chant_id)

                    do_update = True
                    if USE_SYNC_STATE and sync_state is not None:
                        do_update = should_update(conn, sync_state, chant_id, row.get("gabc"), row.get("gabc_verses"))

                    if do_update:
                        upsert_chant(conn, chants, row)
                        updated += 1
                        if USE_SYNC_STATE and sync_state is not None:
                            upsert_sync_state(conn, sync_state, chant_id, row.get("gabc"), row.get("gabc_verses"))
                    else:
                        skipped += 1
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
            print(f"Skipped:          {skipped}")
            print(f"Failed:           {failed}")
            print(f"Elapsed:          {elapsed/60:.1f} minutes")
            print(f"Avg speed:        {rate:.2f} chants/sec")
            print("==================================")
