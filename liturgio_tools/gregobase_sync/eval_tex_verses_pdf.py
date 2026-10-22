"""
Measure tex_verses_pdf against the real tex_verses held in gregobase_chants.

Downloads each chant's full and 1st-verse PDFs once into --cache (read-only
requests to GregoBase), reconstructs tex_verses from them, and scores the result
against the stored TeX after reducing both to comparable plain text:

- text:    character similarity of the plain text
- words:   word-multiset F1
- emphasis: similarity of the bold/italic letters, in order
- lines:   share of the source's lines that appear as a line in ours

Usage:
    python eval_tex_verses_pdf.py [--cache DIR] [--show ID ...]
"""
import argparse
import collections
import difflib
import re
import statistics
import sys
import tempfile
import unicodedata
from pathlib import Path

import keyring
from sqlalchemy import create_engine, text

import tex_verses_pdf as tv

BS = chr(92)


def plain(tex: str) -> str:
    s = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})?", " ", tex)
    s = re.sub(r"\\(setlength|setcounter)\{[^}]*\}\{[^}]*\}", " ", s)
    s = re.sub(r"\\(vspace|hspace\*?|hyphenation|textcolor|color)\{[^}]*\}", " ", s)
    s = s.replace(r"\Vbar", "℣").replace(r"\Rbar", "℟").replace(r"\dag", "†").replace(r"\ae", "æ")
    s = re.sub(r"\\'\{?(\w)\}?", lambda m: m.group(1) + "\u0301", s)
    s = re.sub(r"\\(textbf|textit|bf|it|em|textsc|noindent|hfill)\b\s?", "", s)
    s = re.sub(r"\\[A-Za-z]+\*?", " ", s)
    s = s.replace(BS * 2, " ").replace("&", " ").replace("~", " ").replace("{", "").replace("}", "").replace(BS, "")
    s = s.replace("æ", "ae").replace("ǽ", "ae").replace("œ", "oe")
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip()


def emphasised(tex: str) -> str:
    out = []
    for m in re.finditer(r"\{\\(?:bf|it)(?:\\(?:bf|it))?\s+([^{}]*)\}|\\text(?:bf|it)\{([^{}]*)\}", tex):
        out.append(re.sub(r"\W", "", m.group(1) or m.group(2) or "").lower())
    return "".join(out)


def source_lines(tex: str) -> list[str]:
    parts = re.split(r"\\\\|\\item\b|\\par\b|\\newline\b|\n\s*\n", tex)
    return [p for p in (re.sub(r"\W", "", plain(x)).lower() for x in parts) if p]


def score(truth: str, ours: str) -> dict:
    pt, po = plain(truth), plain(ours)
    wt = collections.Counter(re.findall(r"\w+", pt.lower()))
    wo = collections.Counter(re.findall(r"\w+", po.lower()))
    et, eo = emphasised(truth), emphasised(ours)
    lt, lo = source_lines(truth), set(source_lines(ours))
    return {
        "text": difflib.SequenceMatcher(None, pt, po, autojunk=False).ratio(),
        "words": 2 * sum((wt & wo).values()) / max(1, sum(wt.values()) + sum(wo.values())),
        "emphasis": difflib.SequenceMatcher(None, et, eo, autojunk=False).ratio() if et or eo else 1.0,
        "lines": sum(x in lo for x in lt) / len(lt) if lt else 1.0,
    }


def cached_get(cache: Path):
    from gregobase_scrape import http_get

    def get(url: str) -> bytes:
        name = re.sub(r"\W+", "_", url.split("?", 1)[1]) + ".pdf"
        path = cache / name
        if not path.exists():
            path.write_bytes(http_get(url).content)
        return path.read_bytes()
    return get


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "gregobase_pdf_cache")
    ap.add_argument("--show", type=int, nargs="*", default=[])
    args = ap.parse_args()
    args.cache.mkdir(exist_ok=True)

    pw = keyring.get_password("liturgio-mysql", "liturgio_ro")
    engine = create_engine(f"mysql+mysqlconnector://liturgio_ro:{pw}@localhost:3306/liturgio")
    with engine.connect() as conn:
        truth = dict(conn.execute(text("SELECT id, tex_verses FROM gregobase_chants WHERE tex_verses <> ''")).all())

    get = cached_get(args.cache)
    results, declined = {}, []
    for i in sorted(truth):
        out = tv.fetch_tex_verses(i, get)
        if out is None:
            declined.append(i)
        else:
            results[i] = score(truth[i], out)
        if i in args.show:
            print(f"===== {i} TRUTH\n{truth[i]}\n===== {i} OURS\n{out}\n")

    print(f"reconstructed {len(results)}/{len(truth)}; declined {len(declined)}")
    for metric in ("text", "words", "emphasis", "lines"):
        vals = sorted(r[metric] for r in results.values())
        print(f"  {metric:9} median {statistics.median(vals):.3f}  min {vals[0]:.3f}  "
              f">=0.98: {sum(v >= 0.98 for v in vals)}  <0.9: {sum(v < 0.9 for v in vals)}")
        worst = sorted(results, key=lambda i: results[i][metric])[:5]
        print(f"            worst: {[(i, round(results[i][metric], 2)) for i in worst]}")


if __name__ == "__main__":
    main()
