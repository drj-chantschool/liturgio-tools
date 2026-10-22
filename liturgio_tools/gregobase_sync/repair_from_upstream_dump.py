"""
One-off repair (2026-10-09) of ``gregobase_chants`` fields the old scraper lost
or mangled. Dry run by default; ``--apply`` writes, in one transaction, after
copying the table to ``gregobase_chants_bak_20261009``.

Sources, newest first:
- our own ``headers`` column (the live site's GABC header, scraped verbatim):
  incipit spacing, untruncated commentary, the office-part code;
- the upstream SQL dump (gregobase_online.sql, 2024-01-05, in a clone of
  https://github.com/drj-chantschool/GregoBase at ~/liturgio/GregoBase)
  for what the site never exposed: mode_var, cantusid, initial, tex_verses, and
  verses the old scraper folded into ``gabc``. A dump value is only used where
  our row still agrees with the dump on the field it depends on.
- tex_verses is restored only for chants whose live page still offers
  "(1st verse only)" downloads (``--tex-live-check``, read-only page GETs), so
  verses GregoBase has since removed are not brought back.

Usage:
    python repair_from_upstream_dump.py [--tex-live-check] [--apply]
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

import keyring
from sqlalchemy import create_engine, text

from gabc_tools.gbchant import gabc_elements

import gregobase_scrape as gs

REPO = Path(__file__).resolve().parents[3]
DUMP = REPO / "GregoBase" / "gregobase_online.sql"
BACKUP = "gregobase_chants_bak_20261009"

_NUM = re.compile(r"[^,)]+")


def _parse_values(s: str, i: int):
    rows = []
    while True:
        while s[i] in " \n\r\t,":
            i += 1
        if s[i] == ";":
            return rows
        i += 1
        row = []
        while True:
            while s[i] in " \n":
                i += 1
            if s[i] == "'":
                i += 1
                buf = []
                while True:
                    ch = s[i]
                    if ch == "\\":
                        n = s[i + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(n, n))
                        i += 2
                    elif ch == "'":
                        if s[i + 1] == "'":
                            buf.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        buf.append(ch)
                        i += 1
                row.append("".join(buf))
            else:
                m = _NUM.match(s, i)
                v = m.group(0).strip()
                i = m.end()
                row.append(None if v == "NULL" else v)
            while s[i] == " ":
                i += 1
            if s[i] == ",":
                i += 1
                continue
            if s[i] == ")":
                i += 1
                break
        rows.append(row)


def load_dump(path: Path) -> dict[int, dict]:
    src = path.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"INSERT INTO `gregobase_chants` \(([^)]*)\) VALUES", src):
        cols = [c.strip(" `") for c in m.group(1).split(",")]
        for r in _parse_values(src, m.end()):
            out[int(r[0])] = dict(zip(cols, r))
    return out


def blank(v) -> bool:
    return v is None or str(v).strip() == ""


def ws(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def header_value(headers: str | None, key: str) -> str | None:
    """A key from GregoBase's generated header only: whatever follows the first
    %% is a header the transcriber left inside the gabc, not a GregoBase column."""
    return gs.parse_header_kv((headers or "").split("%%", 1)[0]).get(key)


def gabc_text(raw: str | None) -> tuple[str, str] | None:
    """(form, text) for a plain or JSON-string gabc value; None for a JSON list."""
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return ("plain", raw)
    return ("json", obj) if isinstance(obj, str) else None


def split_off_verses(our_gabc: str, up_body: str) -> tuple[str, str] | None:
    """
    Split our gabc where the dump's body ends, comparing without whitespace.
    Returns (body, verses) taken from OUR text, or None if the bodies differ.
    """
    target = ws(up_body)
    if not ws(our_gabc).startswith(target):
        return None
    seen = 0
    for idx, ch in enumerate(our_gabc):
        if seen == len(target):
            return our_gabc[:idx].rstrip(), our_gabc[idx:].strip()
        if not ch.isspace():
            seen += 1
    return None


def tex_live_check(ids: list[int]) -> dict[int, bool]:
    out = {}
    for i in ids:
        r = gs.http_get(f"{gs.BASE}/chant.php?id={i}")
        out[i] = not r.history and gs.has_verse_links(r.text)
    return out


def plan(ours: dict[int, dict], up: dict[int, dict], live_verses: dict[int, bool] | None):
    changes: dict[int, dict] = collections.defaultdict(dict)
    notes = collections.defaultdict(list)
    codes = set(gs.USAGE_NAMES)

    for i, o in ours.items():
        u = up.get(i)
        hdr = o["headers"]

        # incipit: the scraper ate spaces next to <i> tags; the header's name: kept them.
        name = header_value(hdr, "name")
        if name and name.strip() != (o["incipit"] or "").strip() and ws(name) == ws(o["incipit"]):
            changes[i]["incipit"] = name.strip()

        # commentary: cut at the first ';' by the old header parser.
        com = header_value(hdr, "commentary")
        if com and not blank(o["commentary"]) and com != o["commentary"] and com.startswith(o["commentary"]):
            changes[i]["commentary"] = com

        # office-part: a transcriber's own header line overrode GregoBase's code.
        if o["office-part"] is not None and o["office-part"] not in codes:
            name_op = header_value(hdr, "office-part") or o["office-part"]
            code = {k.lower(): v for k, v in gs.USAGE_MAP.items()}.get(name_op.strip().lower())
            if code:
                changes[i]["office-part"] = code
            else:
                notes["office-part unmapped"].append((i, o["office-part"], name_op))

        if not u:
            continue

        if blank(o["mode_var"]) and not blank(u["mode_var"]):
            if (o["mode"] or None) == (u["mode"] or None):
                changes[i]["mode_var"] = u["mode_var"]
            else:
                notes["mode_var skipped: mode changed since dump"].append((i, u["mode"], o["mode"]))

        if blank(o["cantusid"]) and not blank(u["cantusid"]):
            changes[i]["cantusid"] = u["cantusid"]

        if str(o["initial"]) == "1" and not blank(u["initial"]) and str(u["initial"]) != "1":
            changes[i]["initial"] = int(u["initial"])

        # gabc_verses the old scraper left inside gabc (its 1st-verse prefix test failed).
        if blank(o["gabc_verses"]) and not blank(u["gabc_verses"]):
            ours_g = gabc_text(o["gabc"])
            up_body = "\n".join(p for k, p in gabc_elements(u["gabc"]) if k == "gabc")
            if ours_g is None:
                notes["gabc_verses: our gabc is a JSON list"].append(i)
            elif ws(ours_g[1]) == ws(up_body) + ws(u["gabc_verses"]):
                body, verses = split_off_verses(ours_g[1], up_body)
                changes[i]["gabc"] = body if ours_g[0] == "plain" else json.dumps(body, ensure_ascii=False)
                changes[i]["gabc_verses"] = verses
            else:
                notes["gabc_verses: not in our gabc, left for the next scrape"].append(i)

        if blank(o["tex_verses"]) and not blank(u["tex_verses"]):
            if live_verses is None:
                notes["tex_verses: run with --tex-live-check"].append(i)
            elif not live_verses.get(i):
                notes["tex_verses: live page has no verses any more"].append(i)
            elif not blank(o["gabc_verses"] or changes[i].get("gabc_verses")) and blank(u["gabc_verses"]):
                notes["tex_verses: now gabc_verses on the live site"].append(i)
            else:
                changes[i]["tex_verses"] = u["tex_verses"]

    return {i: c for i, c in changes.items() if c}, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tex-live-check", action="store_true",
                    help="GET each tex_verses candidate's chant page (read-only) to confirm it still has verses")
    args = ap.parse_args()

    up = load_dump(DUMP)
    user = "jcost" if args.apply else "liturgio_ro"
    pw = keyring.get_password("liturgio-mysql", user)
    engine = create_engine(f"mysql+mysqlconnector://{user}:{pw}@localhost:3306/liturgio", future=True)
    with engine.connect() as conn:
        ours = {r["id"]: dict(r) for r in conn.execute(text("SELECT * FROM gregobase_chants")).mappings()}

    live = None
    if args.tex_live_check:
        cand = [i for i, u in up.items() if i in ours and not blank(u["tex_verses"]) and blank(ours[i]["tex_verses"])]
        live = tex_live_check(cand)

    changes, notes = plan(ours, up, live)
    per_col = collections.Counter(c for ch in changes.values() for c in ch)
    print(f"{len(changes)} rows to change; per column: {dict(per_col)}")
    for col in per_col:
        sample = [(i, ours[i][col], ch[col]) for i, ch in changes.items() if col in ch][:4]
        for i, old, new in sample:
            print(f"  {col} {i}: {str(old)[:70]!r} -> {str(new)[:70]!r}")
    for k, v in notes.items():
        print(f"  NOTE {k}: {len(v)}  {v[:12]}")

    if not args.apply:
        print("dry run; pass --apply to write")
        return

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS `{BACKUP}`"))
        conn.execute(text(f"CREATE TABLE `{BACKUP}` AS SELECT * FROM gregobase_chants"))
        for i, ch in changes.items():
            sets = ", ".join(f"`{c}` = :{re.sub(r'[^a-z_]', '_', c)}" for c in ch)
            params = {re.sub(r"[^a-z_]", "_", c): v for c, v in ch.items()}
            params["id"] = i
            conn.execute(text(f"UPDATE gregobase_chants SET {sets} WHERE id = :id"), params)
    print(f"applied; backup in {BACKUP}")


if __name__ == "__main__":
    sys.exit(main())
