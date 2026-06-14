"""
Tests for gregobase_scrape.py parsing functions.
Uses real fixture files (889.html, gabc/889.gabc) where possible.
Run with: pytest test_scrape.py -v
"""
import json
import pathlib
import textwrap
from unittest.mock import patch

import pytest

import gregobase_scrape as gs

FIXTURE_DIR = pathlib.Path(__file__).parent
HTML_889 = (FIXTURE_DIR / "889.html").read_text(encoding="utf-8")
GABC_889 = (FIXTURE_DIR / "gabc" / "889.gabc").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# split_gabc_header_body
# ---------------------------------------------------------------------------

def test_split_gabc_header_body_real():
    head, body = gs.split_gabc_header_body(GABC_889)
    assert "name:Qui habitat" in head
    assert "office-part:Tractus" in head
    assert "mode:2" in head
    assert "transcriber:Andrew Hinkley" in head
    assert body.startswith("(f3)")

def test_split_gabc_header_body_no_separator():
    head, body = gs.split_gabc_header_body("just body text")
    assert head == ""
    assert body == "just body text"

def test_split_gabc_header_body_uses_last_separator():
    # rsplit means only the last %% is used as separator
    text = "a:1;\n%%\nb:2;\n%%\n(c4)notes"
    head, body = gs.split_gabc_header_body(text)
    assert "b:2" in head
    assert body == "(c4)notes"


# ---------------------------------------------------------------------------
# parse_header_kv
# ---------------------------------------------------------------------------

def test_parse_header_kv_real():
    head, _ = gs.split_gabc_header_body(GABC_889)
    kv = gs.parse_header_kv(head)
    assert kv["name"] == "Qui habitat"
    assert kv["office-part"] == "Tractus"
    assert kv["mode"] == "2"
    assert kv["transcriber"] == "Andrew Hinkley"

def test_parse_header_kv_keys_lowercased():
    kv = gs.parse_header_kv("Name:Kyrie;\nOffice-Part:of;")
    assert "name" in kv
    assert "office-part" in kv

def test_parse_header_kv_skips_empty_parts():
    kv = gs.parse_header_kv("a:1;;; ;b:2;")
    assert kv == {"a": "1", "b": "2"}

def test_parse_header_kv_skips_no_colon():
    kv = gs.parse_header_kv("goodkey:goodval;badpart;another:val;")
    assert kv == {"goodkey": "goodval", "another": "val"}


# ---------------------------------------------------------------------------
# split_mode_and_var
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("inp,expected", [
    ("2",       ("2",    None)),
    ("8",       ("8",    None)),
    ("8G",      ("8",    "G")),
    ("8g",      ("8",    "g")),
    ("VIII",    ("VIII", None)),
    ("VIII G",  ("VIII", "G")),
    ("I",       ("I",    None)),
    (None,      (None,   None)),
    ("",        (None,   None)),
    ("ir",      ("ir",   None)),    # no leading digit or uppercase roman → passthrough
])
def test_split_mode_and_var(inp, expected):
    assert gs.split_mode_and_var(inp) == expected


# ---------------------------------------------------------------------------
# parse_chant_page_meta  (real 889.html)
# ---------------------------------------------------------------------------

def test_parse_chant_page_meta_incipit():
    meta = gs.parse_chant_page_meta(HTML_889)
    assert meta["incipit"] == "Qui habitat"

def test_parse_chant_page_meta_version():
    meta = gs.parse_chant_page_meta(HTML_889)
    assert meta["version"] == "Solesmes"

def test_parse_chant_page_meta_usage():
    meta = gs.parse_chant_page_meta(HTML_889)
    assert meta["usage"] == "Tractus"

def test_parse_chant_page_meta_transcriber():
    meta = gs.parse_chant_page_meta(HTML_889)
    assert meta["transcriber"] == "Andrew Hinkley"

def test_parse_chant_page_meta_missing_sections():
    # Minimal HTML: only incipit, no sections at all
    html = "<html><body><h3>Kyrie</h3></body></html>"
    meta = gs.parse_chant_page_meta(html)
    assert meta["incipit"] == "Kyrie"
    assert meta["version"] is None
    assert meta["usage"] is None
    assert meta["remarks"] is None
    assert meta["commentary"] is None
    assert meta["transcriber"] is None

def test_parse_chant_page_meta_no_h3():
    meta = gs.parse_chant_page_meta("<html><body></body></html>")
    assert meta["incipit"] is None

def test_parse_chant_page_meta_transcriber_not_gobbled_by_history():
    """
    Regression: when the History <ul> has many <li> items on one line,
    the transcriber regex must not swallow the entire history tail.
    VARCHAR(128) on the DB column — so any match longer than that is a data error.
    """
    html = textwrap.dedent("""\
        <html><body>
          <h3>Confitebor</h3>
          <h4>History</h4>
          <ul>
            <li>May 06, 2021: Added to the database (Jacques Perriere)</li>
            <li>May 06, 2021: Same as score 584 without Alleluia. (Jacques Perriere)</li>
            <li>May 06, 2021:  (Jacques Perriere)</li>
            <li>Original transcriber: Andrew Hinkley</li>
          </ul>
        </body></html>
    """)
    meta = gs.parse_chant_page_meta(html)
    assert meta["transcriber"] == "Andrew Hinkley"
    assert len(meta["transcriber"]) < 128


# ---------------------------------------------------------------------------
# extract_download_links  (real 889.html)
# ---------------------------------------------------------------------------

def test_extract_download_links_gabc_full():
    links = gs.extract_download_links(HTML_889)
    assert "gabc_full" in links
    assert links["gabc_full"].startswith("https://gregobase.selapa.net")
    assert "889" in links["gabc_full"]
    assert "gabc" in links["gabc_full"]

def test_extract_download_links_no_1verse_for_889():
    # 889 only has one GABC link (no separate 1-verse download)
    links = gs.extract_download_links(HTML_889)
    assert "gabc_1verse" not in links

def test_extract_download_links_with_1verse():
    html = textwrap.dedent("""\
        <ul>
          <li><a href="/download.php?id=1&format=gabc">GABC</a></li>
          <li><a href="/download.php?id=1&format=gabc&elem=1">GABC (1st verse only)</a></li>
        </ul>
    """)
    links = gs.extract_download_links(html)
    assert links["gabc_full"] == "https://gregobase.selapa.net/download.php?id=1&format=gabc"
    assert links["gabc_1verse"] == "https://gregobase.selapa.net/download.php?id=1&format=gabc&elem=1"

def test_extract_download_links_empty():
    links = gs.extract_download_links("<html><body></body></html>")
    assert links == {}


# ---------------------------------------------------------------------------
# fetch_chant_payloads  (mocked HTTP, using real fixtures)
# ---------------------------------------------------------------------------

def _make_fetch_side_effect(chant_html, gabc_full_text, gabc_1verse_text=None):
    """Return a side_effect list for http_get_text matching call order."""
    effects = [chant_html, gabc_full_text]
    if gabc_1verse_text is not None:
        effects.append(gabc_1verse_text)
    return effects

def test_fetch_chant_payloads_no_gabc_link():
    html_no_link = "<html><body><h3>Kyrie</h3></body></html>"
    with patch("gregobase_scrape.http_get_text", return_value=html_no_link):
        payload = gs.fetch_chant_payloads(999)
    assert payload["gabc_body"] is None
    assert payload["headers_text"] is None
    assert payload["gabc_verses_tail"] is None
    assert payload["meta"]["incipit"] == "Kyrie"

def test_fetch_chant_payloads_single_gabc_link():
    # 889-style: one GABC link, no separate 1-verse
    with patch("gregobase_scrape.http_get_text", side_effect=[HTML_889, GABC_889]):
        payload = gs.fetch_chant_payloads(889)

    assert payload["meta"]["incipit"] == "Qui habitat"
    assert payload["meta"]["transcriber"] == "Andrew Hinkley"
    head, body = gs.split_gabc_header_body(GABC_889)
    assert payload["headers_text"] == head
    assert payload["gabc_body"] == body
    assert payload["gabc_verses_tail"] is None
    assert payload["header_kv"]["mode"] == "2"
    assert payload["header_kv"]["office-part"] == "Tractus"

def test_fetch_chant_payloads_with_verses_split():
    # Chant where full body = 1verse body + tail verses
    one_verse_body = "(f3) Ky(f)ri(g)e(h)"
    tail = "\n<sp>V/</sp>. Se(f)cun(g)da(h)"
    full_gabc = f"name:Kyrie;\noffice-part:of;\nmode:1;\n%%\n{one_verse_body}{tail}"
    one_gabc  = f"name:Kyrie;\noffice-part:of;\nmode:1;\n%%\n{one_verse_body}"

    chant_html = textwrap.dedent("""\
        <h3>Kyrie</h3>
        <ul>
          <li><a href="/download.php?id=1&format=gabc">GABC</a></li>
          <li><a href="/download.php?id=1&format=gabc&elem=1">GABC (1st verse only)</a></li>
        </ul>
    """)

    with patch("gregobase_scrape.http_get_text",
               side_effect=[chant_html, full_gabc, one_gabc]):
        payload = gs.fetch_chant_payloads(1)

    assert payload["gabc_body"] == one_verse_body
    assert payload["gabc_verses_tail"] == tail.lstrip()

def test_fetch_chant_payloads_identical_bodies():
    # When full == 1verse (single-verse chant), verses_tail stays None
    gabc = "name:Kyrie;\nmode:1;\n%%\n(f3) Ky(f)ri(g)e(h)"
    chant_html = textwrap.dedent("""\
        <h3>Kyrie</h3>
        <ul>
          <li><a href="/download.php?id=2&format=gabc">GABC</a></li>
          <li><a href="/download.php?id=2&format=gabc&elem=1">GABC (1st verse only)</a></li>
        </ul>
    """)
    with patch("gregobase_scrape.http_get_text",
               side_effect=[chant_html, gabc, gabc]):
        payload = gs.fetch_chant_payloads(2)

    assert payload["gabc_verses_tail"] is None


# ---------------------------------------------------------------------------
# build_row_dict  (mocked, verifies final field mapping)
# ---------------------------------------------------------------------------

def test_build_row_dict_fields_889():
    gs.USAGE_MAP = {}  # no usage remapping
    with patch("gregobase_scrape.http_get_text", side_effect=[HTML_889, GABC_889]):
        row = gs.build_row_dict(889)

    assert row["id"] == 889
    assert row["incipit"] == "Qui habitat"
    assert row["mode"] == "2"
    assert row["mode_var"] is None
    assert row["transcriber"] == "Andrew Hinkley"
    assert row["office-part"] == "Tractus"
    assert row["gabc"] is not None and row["gabc"].startswith("(f3)")
    assert row["gabc_verses"] is None
    assert row["copyrighted"] == 0
    assert row["initial"] == 1
    assert row["duplicateof"] is None

def test_build_row_dict_incipit_fallback_to_header():
    # If h3 is missing, fall back to GABC header 'name' field
    gabc = "name:Fallback Name;\nmode:1;\n%%\n(f3) notes"
    html = textwrap.dedent("""\
        <html><body>
          <ul><li><a href="/download.php?id=3&format=gabc">GABC</a></li></ul>
        </body></html>
    """)
    gs.USAGE_MAP = {}
    with patch("gregobase_scrape.http_get_text", side_effect=[html, gabc]):
        row = gs.build_row_dict(3)
    assert row["incipit"] == "Fallback Name"

def test_build_row_dict_incipit_final_fallback():
    # No h3, no name in header → "(id=N)"
    gabc = "mode:1;\n%%\n(f3) notes"
    html = textwrap.dedent("""\
        <html><body>
          <ul><li><a href="/download.php?id=4&format=gabc">GABC</a></li></ul>
        </body></html>
    """)
    gs.USAGE_MAP = {}
    with patch("gregobase_scrape.http_get_text", side_effect=[html, gabc]):
        row = gs.build_row_dict(4)
    assert row["incipit"] == "(id=4)"

def test_build_row_dict_usage_map_applied():
    gs.USAGE_MAP = {"Tractus": "tr"}
    with patch("gregobase_scrape.http_get_text", side_effect=[HTML_889, GABC_889]):
        row = gs.build_row_dict(889)
    assert row["office-part"] == "tr"


# ---------------------------------------------------------------------------
# Helpers to read expected values from gregobase_online.sql
# ---------------------------------------------------------------------------

SQL_FILE = FIXTURE_DIR / "gregobase_online.sql"

def _parse_sql_row(chant_id: int) -> dict:
    """
    Extract a gregobase_chants row from the SQL dump by chant id.
    Returns a dict with column names as keys, Python-native values (None/int/str).
    """
    sql = SQL_FILE.read_text(encoding="utf-8")

    prefix = f"({chant_id}, "
    idx = sql.index(f"\n{prefix}") + 1
    # find end: next row at same level or end of INSERT
    end = len(sql)
    for pat in (f"\n({chant_id}, ", "\n("):
        try:
            candidate = sql.index(pat, idx + len(prefix))
            # make sure it's a different row
            if pat == f"\n({chant_id}, " :
                # same id but different content (e.g. source rows) — skip
                pass
            end = min(end, candidate)
            break
        except ValueError:
            pass
    # simpler: find the next VALUES row
    rest = sql[idx:]
    m_end = rest.find("\n(")
    row_sql = rest[:m_end].rstrip(",\n") if m_end != -1 else rest.rstrip(",\n")

    inner = row_sql[1:-1]  # strip outer parens

    # tokenise, respecting SQL single-quoted strings and backslash escapes
    fields = []
    buf = []
    in_str = False
    esc = False
    for ch in inner:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            buf.append(ch)
            esc = True
        elif ch == "'" and not in_str:
            in_str = True
            buf.append(ch)
        elif ch == "'" and in_str:
            in_str = False
            buf.append(ch)
        elif ch == "," and not in_str:
            fields.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf).strip())

    cols = [
        "id", "cantusid", "version", "incipit", "initial",
        "office-part", "mode", "mode_var", "transcriber", "commentary",
        "headers", "gabc", "gabc_verses", "tex_verses", "remarks",
        "copyrighted", "duplicateof",
    ]

    def _unquote(v: str):
        if v == "NULL":
            return None
        if v.startswith("'") and v.endswith("'"):
            s = v[1:-1]
            # unescape: \' → ', \" → ", \\ → \ (process pairs)
            out = []
            i = 0
            while i < len(s):
                if s[i] == "\\" and i + 1 < len(s):
                    nxt = s[i + 1]
                    if nxt in ("'", '"', "\\"):
                        out.append(nxt)   # \' → '  \" → "  \\ → \
                    else:
                        out.append("\\")
                        out.append(nxt)   # \r → \r  \u → \u  (keep as JSON sequences)
                    i += 2
                else:
                    out.append(s[i])
                    i += 1
            return "".join(out)
        try:
            return int(v)
        except ValueError:
            return v

    return {col: _unquote(val) for col, val in zip(cols, fields)}


def _extract_gabc_body_from_sql(row: dict) -> str | None:
    """Parse the [[tex,...],[gabc,...]] JSON stored in the gabc column."""
    raw = row.get("gabc")
    if not raw:
        return None
    data = json.loads(raw)
    for entry in data:
        if entry[0] == "gabc":
            return entry[1]
    return None


# ---------------------------------------------------------------------------
# Regression tests against gregobase_online.sql  (chant 889 as ground truth)
# ---------------------------------------------------------------------------

# Expected values for chant 889 from gregobase_online.sql
_SQL_889 = _parse_sql_row(889)


def test_sql_fixture_scalar_fields_889():
    """Sanity-check that our SQL parser extracted sensible values."""
    assert _SQL_889["id"] == 889
    assert _SQL_889["incipit"] == "Qui habitat"
    assert _SQL_889["version"] == "Solesmes"
    assert _SQL_889["mode"] == "2"
    assert _SQL_889["mode_var"] is None
    assert _SQL_889["office-part"] == "tr"
    assert _SQL_889["transcriber"] == "Andrew Hinkley"
    assert _SQL_889["copyrighted"] == 0
    assert _SQL_889["duplicateof"] is None
    assert _SQL_889["gabc_verses"] is None


@pytest.fixture
def row_889():
    """Run build_row_dict(889) with mocked HTTP, USAGE_MAP matching the DB code."""
    gs.USAGE_MAP = {"Tractus": "tr"}
    with patch("gregobase_scrape.http_get_text", side_effect=[HTML_889, GABC_889]):
        return gs.build_row_dict(889)


def test_db_id(row_889):
    assert row_889["id"] == _SQL_889["id"]

def test_db_incipit(row_889):
    assert row_889["incipit"] == _SQL_889["incipit"]

def test_db_version(row_889):
    assert row_889["version"] == _SQL_889["version"]

def test_db_mode(row_889):
    assert row_889["mode"] == _SQL_889["mode"]

def test_db_mode_var(row_889):
    assert row_889["mode_var"] == _SQL_889["mode_var"]

def test_db_office_part(row_889):
    assert row_889["office-part"] == _SQL_889["office-part"]

def test_db_transcriber(row_889):
    assert row_889["transcriber"] == _SQL_889["transcriber"]

def test_db_copyrighted(row_889):
    assert row_889["copyrighted"] == _SQL_889["copyrighted"]

def test_db_duplicateof(row_889):
    assert row_889["duplicateof"] == _SQL_889["duplicateof"]

def test_db_gabc_verses(row_889):
    assert row_889["gabc_verses"] == _SQL_889["gabc_verses"]

def test_db_initial(row_889):
    assert row_889["initial"] == _SQL_889["initial"]

def test_db_gabc_body_matches_sql(row_889):
    """
    The scraped raw GABC body should match the gabc body stored in the
    [[tex,...],[gabc,...]] JSON in the SQL dump (normalising Unicode escapes).
    """
    db_body = _extract_gabc_body_from_sql(_SQL_889)
    assert db_body is not None, "could not extract gabc body from SQL fixture"
    scraper_body = row_889["gabc"]
    assert scraper_body is not None

    # Normalise line endings — SQL dump stores \r\n, downloaded .gabc uses \n.
    assert scraper_body.strip().replace("\r\n", "\n") == db_body.strip().replace("\r\n", "\n")

def test_db_commentary_discrepancy(row_889):
    """
    DB stores '' for missing commentary; scraper currently returns None.
    This test documents the known discrepancy — fix if upsert behaviour matters.
    """
    db_commentary = _SQL_889["commentary"]   # ''
    assert db_commentary == ""
    assert row_889["commentary"] is None     # scraper returns None, not ''
