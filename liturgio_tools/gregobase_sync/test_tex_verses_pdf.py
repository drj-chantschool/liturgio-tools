"""
Tests for tex_verses_pdf.py on small synthetic PDFs laid out like GregoBase's:
a "score" at the top (same in both PDFs), verses below it in the full PDF only.
Accuracy on real chants is measured by eval_tex_verses_pdf.py.
Run with: pytest test_tex_verses_pdf.py -v
"""
import fitz

import tex_verses_pdf as tv

WIDTH = 1.0 + tv.TEXT_WIDTH + 1      # cropped page: 1pt left edge, text block, 1pt
SCORE_H = 60


def _pdf(height, lines, score="(score)"):
    """lines: [(x, y, text, fontname)] placed below the score."""
    doc = fitz.open()
    page = doc.new_page(width=WIDTH, height=height)
    page.insert_text((1, 20), score, fontname="helv", fontsize=10)
    for x, y, text, font in lines:
        page.insert_text((x, y), text, fontname=font, fontsize=10)
    return doc.tobytes()


def _pair(lines, height=200):
    return _pdf(height, lines), _pdf(SCORE_H, [])


def test_declines_when_one_verse_pdf_is_not_shorter():
    one = _pdf(SCORE_H, [])
    assert tv.tex_verses_from_pdfs(one, one) is None

def test_declines_when_the_scores_differ():
    full = _pdf(200, [(1, 90, "2. Text", "helv")], score="(score A)")
    one = _pdf(SCORE_H, [], score="(score B)")
    assert tv.tex_verses_from_pdfs(full, one) is None

def test_hymn_lines_stay_separate_and_stanzas_get_a_gap():
    full, one = _pair([
        (1, 80, "2. Te lucis ante terminum,", "helv"),
        (12, 92, "rerum creator, poscimus,", "helv"),
        (1, 125, "3. Procul recedant somnia", "helv"),
    ])
    assert tv.tex_verses_from_pdfs(full, one) == (
        "2. Te lucis ante terminum,\\\\\nrerum creator, poscimus,\\\\\n\\\\\n3. Procul recedant somnia")

def test_justified_wrap_and_hyphen_are_joined():
    long_line = "2 Quoniam Deus magnus Dominus, * et rex magnus super omnes"
    # Stretch the first line to the text width, as TeX justification does.
    size = 10 * tv.TEXT_WIDTH / fitz.get_text_length(long_line, fontname="helv", fontsize=10)
    doc = fitz.open()
    page = doc.new_page(width=WIDTH, height=200)
    page.insert_text((1, 20), "(score)", fontname="helv", fontsize=10)
    page.insert_text((1, 80), long_line, fontname="helv", fontsize=size)
    page.insert_text((1, 80 + 1.2 * size), "deos. Ant. Ve-", fontname="helv", fontsize=10)
    page.insert_text((1, 80 + 2.4 * size), "nite.", fontname="helv", fontsize=10)
    out = tv.tex_verses_from_pdfs(doc.tobytes(), _pdf(SCORE_H, []))
    assert out == long_line + " deos. Ant. Venite."

def test_bold_and_italic_spans():
    full, one = _pair([
        (1, 80, "2 Quia fecit mihi magna qui ", "helv"),
        (1 + fitz.get_text_length("2 Quia fecit mihi magna qui ", fontname="helv", fontsize=10), 80, "po", "hebo"),
        (1 + fitz.get_text_length("2 Quia fecit mihi magna qui po", fontname="helv", fontsize=10), 80, "tens", "heit"),
    ])
    assert tv.tex_verses_from_pdfs(full, one) == r"2 Quia fecit mihi magna qui {\bf po}{\it tens}"

def test_tex_specials_are_escaped():
    full, one = _pair([(1, 80, "50% & more_", "helv")])
    assert tv.tex_verses_from_pdfs(full, one) == r"50\% \& more\_"

def test_two_column_table_reads_left_column_then_right():
    rows = [("2. Spes et salus", "5. Quae ut mater"),
            ("Audi nos, Maria!", "Audi nos, o Maria!"),
            ("3. Tibi, Virgo,", "6. Praeelecta sola"),
            ("Audi nos, Maria!", "Audi nos, o Maria!")]
    lines = []
    for k, (l, r) in enumerate(rows):
        y = 80 + 12 * k
        lines += [(1, y, l, "helv"), (170, y, r, "helv")]
    full, one = _pair(lines)
    out = tv.tex_verses_from_pdfs(full, one)
    assert out.split("\\\\\n") == [
        "2. Spes et salus", "Audi nos, Maria!", "3. Tibi, Virgo,", "Audi nos, Maria!", "",
        "5. Quae ut mater", "Audi nos, o Maria!", "6. Praeelecta sola", "Audi nos, o Maria!"]

def test_gap_between_cells_becomes_a_space():
    full, one = _pair([(1, 80, "2.", "helv"), (14, 80, "Spes et salus", "helv")])
    assert tv.tex_verses_from_pdfs(full, one) == "2. Spes et salus"
