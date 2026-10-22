"""
Approximate a GregoBase chant's ``tex_verses`` from its rendered PDFs.

GregoBase never exposes the TeX source of ``tex_verses``; it only renders it
below the score in the chant's PDF. chant_img.php renders two PDFs from the
same template: ``download.php?id=N&format=pdf`` (score + verses) and
``…&1verse=1`` (score only). Both are cropped to their content, so the score
occupies the same region in each, and everything in the full PDF below the
1st-verse PDF's height is the verse block.

The result is a reconstruction, not GregoBase's source: words, verse/line
breaks, bold/italic (the pointing), ℣/℟ and † are recovered; layout commands
(tabular, \\item lists, \\setlength, colours, ties) are not. Two-column verse
tables are read left column, then right column (verse-number order).

Returns None, rather than a guess, when the verse block can't be isolated:
- the 1st-verse PDF is not shorter (the chant renders gabc_verses instead, or
  is a multi-part chant whose verses live inside the score);
- the score parts of the two PDFs differ (they were rendered at different times);
- there is music below the score (the "verses" are notation, not text).
"""
from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass, field

import fitz  # PyMuPDF

# Gregorio's greextra font draws the versicle/response signs.
# Gregorio's greextra font draws the bar of ℟/℣ (see _apply_bar_glyphs).
_GREEXTRA = {"=": "℟", ">": "℣"}
_TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                 "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}",
                 "℟": r"\Rbar{}", "℣": r"\Vbar{}"}
# Width of the text block in GregoBase's PDFs, in points (measured: justified lines
# end at x=310.5 with the crop's left edge at x=1.0, across old and new renders).
TEXT_WIDTH = 309.5
# A verse number ("2." or "2 ") or a versicle/response sign starts a new source line.
_VERSE_START = re.compile(r"^\s*(\d+\.?\s|\d+\.|\\Vbar|\\Rbar|[℣℟])")


@dataclass
class _Span:
    text: str
    style: str | None          # None, 'bf', 'it' or 'bfit'
    x0: float
    x1: float


@dataclass
class _Line:
    y0: float
    y1: float
    spans: list[_Span] = field(default_factory=list)

    @property
    def x0(self) -> float:
        return self.spans[0].x0

    @property
    def x1(self) -> float:
        return self.spans[-1].x1

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


def _style(font: str) -> str | None:
    f = font.lower()
    # LibertinusSerif-Bold/-Italic, or Linux Libertine's LinLibertineOB/-OI in older renders.
    bold = "bold" in f or f.endswith("ob")
    italic = "italic" in f or "oblique" in f or f.endswith("oi")
    if bold and italic:
        return "bfit"
    return "bf" if bold else "it" if italic else None


def _escape(text: str, font: str = "") -> str:
    return "".join(_TEX_SPECIALS.get(c, c) for c in text)


def _page_lines(page) -> list[tuple[float, float, list[dict]]]:
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            spans = []
            for s in l["spans"]:
                s["text"] = "".join(c["c"] for c in s["chars"])
                if s["text"]:
                    spans.append(s)
            if spans:
                out.append((l["bbox"][1], l["bbox"][3], spans))
    return sorted(out, key=lambda t: (round(t[0], 1), t[2][0]["bbox"][0]))


def _apply_bar_glyphs(spans: list[dict]) -> list[dict]:
    """
    gregoriotex draws ℣/℟ as a text-font V/R with a zero-width greextra bar
    (``>``/``=``) over it. Replace the letter under each bar with \\Vbar/\\Rbar
    and drop the bar glyphs.
    """
    bars = [(c["origin"][0], _GREEXTRA[c["c"]]) for s in spans if s["font"].startswith("greextra")
            for c in s["chars"] if c["c"] in _GREEXTRA]
    for s in spans:
        if s["font"].startswith("greextra"):
            s["chars"] = [c for c in s["chars"] if c["c"] not in _GREEXTRA]
    text_spans = [s for s in spans if s["chars"]]
    for x, cmd in bars:
        for s in text_spans:
            for k, c in enumerate(s["chars"]):
                if c["c"] in "RV" and c["bbox"][0] - 1 <= x <= c["bbox"][2] + 1:
                    s["chars"][k] = dict(c, c=cmd)
                    break
            else:
                continue
            break
    for s in text_spans:
        s["text"] = "".join(c["c"] for c in s["chars"])
    return text_spans


def _signature(lines) -> list[tuple[float, str]]:
    return [(round(y0, 1), "".join(s["text"] for s in spans)) for y0, _, spans in lines]


def isolate_verse_spans(full_pdf: bytes, one_verse_pdf: bytes) -> list[tuple[float, float, list[dict]]] | None:
    """The PDF text lines making up the verse block, or None if it can't be isolated."""
    full = fitz.open(stream=full_pdf, filetype="pdf")
    one = fitz.open(stream=one_verse_pdf, filetype="pdf")
    if full.page_count != 1 or one.page_count != 1:
        return None
    cut = one[0].rect.height
    if full[0].rect.height - cut <= 5:
        return None
    full_lines, one_lines = _page_lines(full[0]), _page_lines(one[0])
    if _signature(full_lines[:len(one_lines)]) != _signature(one_lines):
        return None
    below = [l for l in full_lines if l[0] > cut - 2]
    if not below or any(s["font"].startswith("gre") and not s["font"].startswith("greextra")
                        for _, _, spans in below for s in spans):
        return None
    return below


def _rows(raw) -> list[_Line]:
    """Merge PDF lines sharing a baseline into rows of spans, left to right."""
    grouped: list[tuple[float, float, list[dict]]] = []
    for y0, y1, spans in sorted(raw, key=lambda t: t[1]):
        if grouped and abs(grouped[-1][1] - y1) < 2:
            grouped[-1][2].extend(spans)
            grouped[-1] = (min(grouped[-1][0], y0), grouped[-1][1], grouped[-1][2])
        else:
            grouped.append((y0, y1, list(spans)))
    rows: list[_Line] = []
    for y0, y1, spans in grouped:
        conv = [_Span(_escape(s["text"], s["font"]), _style(s["font"]), s["bbox"][0], s["bbox"][2])
                for s in _apply_bar_glyphs(spans) if s["text"]]
        conv.sort(key=lambda s: s.x0)
        # Table cells / tab stops leave a visible gap with no space glyph.
        for a, b in zip(conv, conv[1:]):
            if b.x0 - a.x1 > 2 and not a.text.endswith(" ") and not b.text.startswith(" "):
                a.text += " "
        if conv:
            rows.append(_Line(y0, y1, conv))
    return rows


def _split_columns(rows: list[_Line]) -> list[list[_Line]]:
    """Split a two-column verse table into its columns; otherwise one column."""
    if len(rows) < 2:
        return [rows]
    gaps = []
    for r in rows:
        for a, b in zip(r.spans, r.spans[1:]):
            if b.x0 - a.x1 > 15:
                gaps.append((a.x1 + b.x0) / 2)
    # A real table has a big gap on many rows at about the same x.
    if len(gaps) < max(2, len(rows) // 3):
        return [rows]
    split = statistics.median(gaps)
    left, right = [], []
    for r in rows:
        l = [s for s in r.spans if s.x1 <= split]
        rt = [s for s in r.spans if s.x0 >= split]
        mid = [s for s in r.spans if s.x1 > split and s.x0 < split]
        if mid:  # a full-width line (heading/rubric) inside the table
            return [rows]
        if l:
            left.append(_Line(r.y0, r.y1, l))
        if rt:
            right.append(_Line(r.y0, r.y1, rt))
    return [left, right] if left and right else [rows]


def _render_spans(spans: list[_Span]) -> str:
    merged: list[list] = []
    for s in spans:
        if merged and merged[-1][1] == s.style:
            merged[-1][0] += s.text
        else:
            merged.append([s.text, s.style])
    out = []
    for txt, style in merged:
        if not style or not txt.strip():
            out.append(txt)
            continue
        lead = txt[:len(txt) - len(txt.lstrip())]
        trail = txt[len(txt.rstrip()):]
        cmd = {"bf": r"\bf", "it": r"\it", "bfit": r"\bf\it"}[style]
        out.append(f"{lead}{{{cmd} {txt.strip()}}}{trail}")
    return "".join(out)


def _paragraphs(rows: list[_Line], right: float) -> list[tuple[str, bool]]:
    """
    Join wrapped rows into source lines. A row continues the previous one when the
    previous row ends with a TeX hyphen (removed) or reaches ``right``, the text
    width (justified text), and this row does not start a new verse. Short lines
    (hymn stanzas, table cells) never reach it. Returns (text, gap_before).
    """
    if not rows:
        return []
    # Baseline step of consecutive lines; a clearly bigger step is a vertical gap.
    heights = sorted(b.y0 - a.y0 for a, b in zip(rows, rows[1:]) if b.y0 > a.y0)
    step = heights[len(heights) // 4] if heights else 0
    paras: list[tuple[list[_Span], bool]] = []
    prev = None
    for r in rows:
        gap = bool(prev and step and r.y0 - prev.y0 > 1.4 * step)
        first = r.text.lstrip()
        hyphenated = prev is not None and prev.text.rstrip().endswith("-")
        # Capitalisation is no signal: modern hymn editions start lines in lowercase.
        wraps = (prev is not None and not gap and not _VERSE_START.match(first)
                 and (hyphenated or prev.x1 >= right))
        if wraps:
            spans = paras[-1][0]
            last = spans[-1]
            if last.text.endswith("-"):
                last.text = last.text[:-1]
            elif not last.text.endswith(" "):
                spans.append(_Span(" ", None, last.x1, last.x1))
            spans.extend(r.spans)
        else:
            paras.append((list(r.spans), gap))
        prev = r
    out = []
    for spans, gap in paras:
        text = re.sub(r"[ \t]+", " ", _render_spans(spans)).strip()
        if text:
            out.append((text, gap))
    return out


def tex_verses_from_pdfs(full_pdf: bytes, one_verse_pdf: bytes) -> str | None:
    """
    Reconstruct tex_verses from the full and 1st-verse PDFs of one chant, in
    GregoBase's convention: one source line per verse/line, lines separated by
    ``\\\\`` + newline, an extra ``\\\\`` line for a vertical gap.
    """
    raw = isolate_verse_spans(full_pdf, one_verse_pdf)
    if raw is None:
        return None
    # chant_img.php's template has a fixed text width (16cm paper, fullpage), so a
    # justified (wrapped) line ends TEXT_WIDTH after the cropped left edge. The page
    # width itself varies: neumes can stick out past the text block.
    left = min(s["bbox"][0] for _, _, spans in raw for s in spans)
    page_width = fitz.open(stream=full_pdf, filetype="pdf")[0].rect.width
    right = min(left + TEXT_WIDTH, page_width) - 1
    parts: list[str] = []
    for column in _split_columns(_rows(raw)):
        for i, (text, gap) in enumerate(_paragraphs(column, right)):
            if parts and (gap or i == 0):
                parts.append("")
            parts.append(text)
    return "\\\\\n".join(parts) if parts else None


def fetch_tex_verses(chant_id: int, get=None) -> str | None:
    """Download both PDFs for a chant and reconstruct its tex_verses (read-only)."""
    if get is None:
        from gregobase_scrape import BASE, http_get
        get = lambda url: http_get(url).content  # noqa: E731
    else:
        from gregobase_scrape import BASE
    full = get(f"{BASE}/download.php?id={chant_id}&format=pdf")
    one = get(f"{BASE}/download.php?id={chant_id}&format=pdf&1verse=1")
    if not full or not one:
        return None
    return tex_verses_from_pdfs(full, one)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        print(f"===== {arg}")
        print(fetch_tex_verses(int(arg)))
