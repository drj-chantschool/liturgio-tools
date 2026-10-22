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

def test_parse_header_kv_skips_blank_and_colonless_lines():
    kv = gs.parse_header_kv("a:1;\n\n ;\nbadpart;\nb:2;")
    assert kv == {"a": "1", "b": "2"}

def test_parse_header_kv_keeps_semicolons_inside_values():
    # Regression: splitting on ';' cut commentary at its first semicolon.
    kv = gs.parse_header_kv("commentary:Joan. 13, 34; Ps. 118, 1;\nmode:8;")
    assert kv["commentary"] == "Joan. 13, 34; Ps. 118, 1"
    assert kv["mode"] == "8"

def test_parse_header_kv_first_key_wins():
    # GregoBase's generated header precedes any header left inside the stored gabc.
    kv = gs.parse_header_kv("office-part:Antiphona;\n%%\noffice-part:Laudes Ant E;")
    assert kv["office-part"] == "Antiphona"


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


@pytest.mark.parametrize("inp,expected", [
    ("8",         ("8", None)),
    ("8 G",       ("8", "G")),
    ("2 *-4a",    ("2", "*-4a")),
    ("T. pereg.", ("p", None)),
    ("E",         ("e", None)),
    ("D a",       ("d", "a")),
    (None,        (None, None)),
])
def test_parse_mode_annotation(inp, expected):
    assert gs.parse_mode_annotation(inp) == expected


def test_parse_illuminare_fragment_full():
    frag = "commentary: Joan. 13, 34; Ps. 118, 1\nannotation: Ant\nannotation: 8 G\n%%\n(c4) A(f)"
    assert gs.parse_illuminare_fragment(frag, "an") == {
        "commentary": "Joan. 13, 34; Ps. 118, 1", "mode": "8", "mode_var": "G"}

def test_parse_illuminare_fragment_no_usage_abbrev():
    # 'al' has an empty usage_s, so the only annotation is the mode.
    frag = "annotation: 1\n%%\n(c4) A(f)"
    assert gs.parse_illuminare_fragment(frag, "al") == {"commentary": None, "mode": "1", "mode_var": None}

def test_parse_illuminare_fragment_modeless():
    assert gs.parse_illuminare_fragment("annotation: Ant\n%%\n(c4)", "an")["mode"] is None
    assert gs.parse_illuminare_fragment("%%\n(c4)", "ps")["mode"] is None


@pytest.mark.parametrize("elements,neumz,expected", [
    (["(c4) A(f)"], "(c4) A(f)\n\n2. B(g)", (True, "2. B(g)")),
    (["(c4) A(f)"], "(c4) A(f)\n\n", (True, None)),
    (["(c4) A(f)", "(c4) B(g)"], "(c4) A(f)\n(c4) B(g)\n\n3. C(h)", (True, "3. C(h)")),
    (["(c4) A(f)"], "(c4) X(f)\n\n", (False, None)),
    (["(c4) A(f)"], None, (False, None)),
])
def test_split_neumz_verses(elements, neumz, expected):
    assert gs.split_neumz_verses(neumz, elements) == expected


def test_merge_gabc_elements_keeps_tex_when_counts_match():
    old = json.dumps([["tex", "\\grechangedim", {}], ["gabc", "OLD1"], ["tex", "rubric", {}], ["gabc", "OLD2"]])
    merged = json.loads(gs.merge_gabc_elements(["NEW1", "NEW2"], old))
    assert merged == [["tex", "\\grechangedim", {}], ["gabc", "NEW1"], ["tex", "rubric", {}], ["gabc", "NEW2"]]

def test_merge_gabc_elements_rebuilds_when_counts_differ():
    old = json.dumps([["tex", "x", {}], ["gabc", "OLD1"]])
    assert json.loads(gs.merge_gabc_elements(["A", "B"], old)) == [["gabc", "A"], ["gabc", "B"]]


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

def test_parse_chant_page_meta_office_part_code_and_mode():
    meta = gs.parse_chant_page_meta(HTML_889)
    assert meta["office_part"] == "tr"
    assert (meta["mode"], meta["mode_var"]) == ("2", None)
    assert meta["has_editor_links"] is True
    assert meta["neumz_gabc"].startswith("(f3) QUI(f_)")

@pytest.mark.parametrize("h3,expected", [
    # format_incipit() wraps these in <i>; the spaces outside the tags must survive.
    ("Omnes gentes <i>(Comm.)</i>", "Omnes gentes (Comm.)"),
    ("Cibavit <i>sine</i> Alleluia", "Cibavit sine Alleluia"),
    ("Rorate... <i>Ps.</i> Caeli enarrant", "Rorate... Ps. Caeli enarrant"),
    ('Kyrie <span id="push2"> <a href="#">!</a></span> <span class="edit"><a>Edit</a></span>', "Kyrie"),
    ("\u2591\u2591123\u2591\u2591", None),
])
def test_parse_chant_page_meta_incipit_keeps_spaces(h3, expected):
    assert gs.parse_chant_page_meta(f"<h3>{h3}</h3>")["incipit"] == expected

def test_parse_chant_page_meta_cantusid_and_copyright():
    html = ('<div id="score"><br />This tune is still under copyright.<br /></div>'
            '<h3>X</h3><h4>Cantus ID</h4><ul><li><a href="#">g00489</a></li></ul>')
    meta = gs.parse_chant_page_meta(html)
    assert meta["cantusid"] == "g00489"
    assert meta["copyrighted"] is True
    assert meta["has_editor_links"] is False

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

def test_extract_download_links_889_is_a_single_element():
    # 889 is stored upstream as a [tex, gabc] list, so its only link is &elem=1.
    links = gs.extract_download_links(HTML_889)
    assert links == {"gabc_elems": ["https://gregobase.selapa.net/download.php?id=889&format=gabc&elem=1"]}
    assert not gs.has_verse_links(HTML_889)

def test_extract_download_links_plain_with_verses():
    html = textwrap.dedent("""\
        <ul>
          <li><a href="download.php?id=1&amp;format=gabc">GABC</a></li>
          <li><a href="download.php?id=1&amp;format=pdf">PDF</a></li>
        </ul><ul>
          <li><a href="download.php?id=1&amp;format=gabc&amp;1verse=1">GABC (1st verse only)</a></li>
        </ul>
    """)
    links = gs.extract_download_links(html)
    assert links == {
        "gabc_full": "https://gregobase.selapa.net/download.php?id=1&format=gabc",
        "gabc_1verse": "https://gregobase.selapa.net/download.php?id=1&format=gabc&1verse=1",
    }
    assert gs.has_verse_links(html)

def test_extract_download_links_multi_element_in_order():
    html = ('<a href="download.php?id=5&amp;format=gabc&amp;elem=2">Element 2</a>'
            '<a href="download.php?id=5&amp;format=gabc&amp;elem=1">Element 1</a>')
    assert gs.extract_download_links(html)["gabc_elems"] == [
        "https://gregobase.selapa.net/download.php?id=5&format=gabc&elem=1",
        "https://gregobase.selapa.net/download.php?id=5&format=gabc&elem=2",
    ]

def test_extract_download_links_empty():
    links = gs.extract_download_links("<html><body></body></html>")
    assert links == {}


# ---------------------------------------------------------------------------
# fetch_chant_payloads / build_row_dict  (mocked HTTP)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text, url="https://gregobase.selapa.net/chant.php?id=0", history=()):
        self.text, self.url, self.history = text, url, list(history)

def _mock(page_html, *downloads, url=None, history=()):
    """Patch the page fetch (http_get) and the GABC downloads (http_get_text)."""
    kw = {"url": url} if url else {}
    return (patch("gregobase_scrape.http_get", return_value=_Resp(page_html, history=history, **kw)),
            patch("gregobase_scrape.http_get_text", side_effect=list(downloads)))

def _neumz(gabc):
    from urllib.parse import quote
    return f'<a href="https://scrib.io/#q={quote(gabc, safe="")}">Neumz</a>'

def _illuminare(head):
    from urllib.parse import quote
    return f'<a href="https://editor.sourceandsummit.com/legacy/#{quote(head, safe="")}">Illuminare</a>'


def test_fetch_chant_payloads_no_gabc_link():
    p1, p2 = _mock("<html><body><h3>Kyrie</h3></body></html>")
    with p1, p2:
        payload = gs.fetch_chant_payloads(999)
    assert payload["gabc_body"] is None
    assert payload["headers_text"] is None
    assert payload["gabc_verses"] is None
    assert payload["meta"]["incipit"] == "Kyrie"

def test_fetch_chant_payloads_889():
    p1, p2 = _mock(HTML_889, GABC_889)
    with p1, p2:
        payload = gs.fetch_chant_payloads(889)
    head, body = gs.split_gabc_header_body(GABC_889)
    assert payload["headers_text"] == head
    assert payload["gabc_body"] == body
    assert payload["gabc_verses"] is None
    assert payload["header_kv"]["office-part"] == "Tractus"

def test_fetch_chant_payloads_gabc_verses_from_neumz_link():
    content = "(c4) Ve(f)ni(g)"
    verses = "2. Qui(f) di(g)ce(h)ris\n(::)"
    html = ('<h3>Veni</h3>'
            '<a href="download.php?id=7&amp;format=gabc">GABC</a>'
            '<a href="download.php?id=7&amp;format=gabc&amp;1verse=1">GABC (1st verse only)</a>'
            + _illuminare("annotation: Hymn\nannotation: 8\n%%\n" + content + "\n\n" + verses)
            + _neumz(content + "\n\n" + verses))
    # Only the 1st-verse download is fetched: it is exactly the stored gabc.
    p1, p2 = _mock(html, "name:Veni;\nmode:8;\n%%\n" + content)
    with p1, p2:
        payload = gs.fetch_chant_payloads(7)
    assert payload["gabc_body"] == content
    assert payload["gabc_verses"] == verses
    assert payload["verses_unmatched"] is False

def test_fetch_chant_payloads_multi_element_verses():
    e1, e2 = "(c4) A(f)", "(c4) B(g)"
    html = ('<h3>Multi</h3>'
            '<a href="download.php?id=8&amp;format=gabc&amp;elem=1">Element 1</a>'
            '<a href="download.php?id=8&amp;format=gabc&amp;elem=2">Element 2</a>'
            + _neumz(e1 + "\n" + e2 + "\n\n" + "3. C(h)"))
    p1, p2 = _mock(html, "name:Multi;\n%%\n" + e1, "name:Multi;\n%%\n" + e2)
    with p1, p2:
        payload = gs.fetch_chant_payloads(8)
    assert payload["elements"] == [e1, e2]
    assert payload["gabc_verses"] == "3. C(h)"

def test_fetch_chant_payloads_duplicate_redirect():
    p1, p2 = _mock("", url="https://gregobase.selapa.net/chant.php?id=1247", history=[object()])
    with p1, p2:
        assert gs.fetch_chant_payloads(2091) == {"duplicateof": 1247}

def test_fetch_chant_payloads_retired_redirect():
    p1, p2 = _mock("", url="https://gregobase.selapa.net/scores.php", history=[object()])
    with p1, p2:
        assert gs.fetch_chant_payloads(5) == {"duplicateof": -1}


def test_build_row_dict_fields_889():
    p1, p2 = _mock(HTML_889, GABC_889)
    with p1, p2:
        row = gs.build_row_dict(889)

    assert row["id"] == 889
    assert row["incipit"] == "Qui habitat"
    assert row["mode"] == "2"
    assert row["mode_var"] is None
    assert row["transcriber"] == "Andrew Hinkley"
    assert row["office-part"] == "tr"
    assert row["gabc"] is not None and row["gabc"].startswith("(f3)")
    assert row["gabc_verses"] is None
    assert row["copyrighted"] == 0
    assert row["duplicateof"] is None
    # Not exposed by the site, so never written by the scraper.
    assert "initial" not in row and "tex_verses" not in row

def test_build_row_dict_multi_element_merges_into_existing_list():
    e1, e2 = "(c4) A(f)", "(c4) B(g)"
    html = ('<h3>Multi</h3>'
            '<a href="download.php?id=8&amp;format=gabc&amp;elem=1">Element 1</a>'
            '<a href="download.php?id=8&amp;format=gabc&amp;elem=2">Element 2</a>'
            + _neumz(e1 + "\n" + e2 + "\n\n"))
    existing = {"gabc": json.dumps([["gabc", "old"], ["tex", "rubric", {}], ["gabc", "old"]])}
    p1, p2 = _mock(html, "name:Multi;\n%%\n" + e1, "name:Multi;\n%%\n" + e2)
    with p1, p2:
        row = gs.build_row_dict(8, existing)
    assert json.loads(row["gabc"]) == [["gabc", e1], ["tex", "rubric", {}], ["gabc", e2]]

def test_build_row_dict_duplicate_is_partial():
    p1, p2 = _mock("", url="https://gregobase.selapa.net/chant.php?id=1247", history=[object()])
    with p1, p2:
        assert gs.build_row_dict(2091) == {"id": 2091, "duplicateof": 1247}

def test_build_row_dict_incipit_fallback_to_header():
    # If h3 is missing, fall back to GABC header 'name' field
    html = '<ul><li><a href="/download.php?id=3&format=gabc">GABC</a></li></ul>'
    p1, p2 = _mock(html, "name:Fallback Name;\nmode:1;\n%%\n(f3) notes")
    with p1, p2:
        row = gs.build_row_dict(3)
    assert row["incipit"] == "Fallback Name"
    assert row["mode"] == "1"   # no editor links -> mode from the download header

def test_build_row_dict_incipit_final_fallback():
    # No h3, no name in header → "(id=N)"
    html = '<ul><li><a href="/download.php?id=4&format=gabc">GABC</a></li></ul>'
    p1, p2 = _mock(html, "mode:1;\n%%\n(f3) notes")
    with p1, p2:
        row = gs.build_row_dict(4)
    assert row["incipit"] == "(id=4)"

def test_build_row_dict_usage_name_fallback():
    # No <span class="usage CODE"> on the page: map the Usage name.
    html = ('<h3>X</h3><h4>Usage</h4><ul><li>Tractus</li></ul><h4>Download</h4>'
            '<a href="/download.php?id=6&format=gabc">GABC</a>')
    p1, p2 = _mock(html, "name:X;\n%%\n(f3) notes")
    with p1, p2:
        assert gs.build_row_dict(6)["office-part"] == "tr"

def test_build_row_dict_flags_uncapturable_tex_verses():
    # Verse links but no gabc_verses -> the verses are TeX the site doesn't expose.
    html = ('<h3>Ps</h3><a href="download.php?id=9&amp;format=gabc">GABC</a>'
            '<a href="download.php?id=9&amp;format=gabc&amp;1verse=1">GABC (1st verse only)</a>'
            + _neumz("(c4) A(f)\n\n"))
    rebuilt = patch("tex_verses_pdf.fetch_tex_verses", return_value="2.~rebuilt")
    p1, p2 = _mock(html, "name:Ps;\n%%\n(c4) A(f)")
    with p1, p2, rebuilt as fetch:
        row = gs.build_row_dict(9)
    assert row["_tex_verses_missing"] is True
    assert row["tex_verses_pdf"] == "2.~rebuilt"   # reconstruction goes to its own column ...
    assert "tex_verses" not in row                 # ... and never to the real one
    assert fetch.call_args[0][0] == 9

    p1, p2 = _mock(html, "name:Ps;\n%%\n(c4) A(f)")
    with p1, p2, patch("tex_verses_pdf.fetch_tex_verses") as fetch:
        row = gs.build_row_dict(9, {"tex_verses": "2.~..."})
    assert row["_tex_verses_missing"] is False
    assert "tex_verses_pdf" not in row and not fetch.called   # real TeX held: no PDF fetch


# ---------------------------------------------------------------------------
# Helpers to read expected values from the upstream SQL dump row (fixture)
# ---------------------------------------------------------------------------

SQL_FILE = FIXTURE_DIR / "gregobase_chant_889.sql"

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
# Regression tests against the upstream SQL dump  (chant 889 as ground truth)
# ---------------------------------------------------------------------------

# Expected values for chant 889 from the upstream SQL dump
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
    p1, p2 = _mock(HTML_889, GABC_889)
    with p1, p2:
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

def test_db_commentary(row_889):
    """The dump stores '' for no commentary; the scraper leaves it NULL."""
    assert _SQL_889["commentary"] == ""
    assert row_889["commentary"] is None
