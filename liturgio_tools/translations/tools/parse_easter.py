#!/usr/bin/env python3
"""
Parse Easter Mass Propers from an MHT file into JSON.
Extracts entrance and communion antiphons with Latin and English text.
"""

import re
import json
import sys
from html import unescape


def extract_html_from_mht(path):
    """Extract the main HTML body from a MIME HTML archive (.mht)."""
    with open(path, errors='replace') as f:
        content = f.read()
    start = content.find('<!DOCTYPE html')
    if start == -1:
        start = content.find('<html')
    if start == -1:
        raise ValueError("No HTML found in MHT file")
    m = re.search(r'\n------MultipartBoundary', content[start:])
    end = start + m.start() if m else len(content)
    return content[start:end]


def strip_tags(html):
    return unescape(re.sub(r'<[^>]+>', '', html))


def clean(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


# ── Anchor classification ──────────────────────────────────────────────────

WEEKDAY_RE = re.compile(
    r'^(sunday|monday|tuesday|wednesday|thursday|friday|saturday)(\d+)$', re.I
)
WEEKDAY_MAP = {
    'sunday': 'Sunday', 'monday': 'Monday', 'tuesday': 'Tuesday',
    'wednesday': 'Wednesday', 'thursday': 'Thursday',
    'friday': 'Friday', 'saturday': 'Saturday',
}
SKIP_ANCHORS = {'ascension', 'pentecost'}
SPECIAL_ANCHORS = {
    'ascensionvigil': dict(season='Easter', week=None, weekday='Ascension Thursday',
                           note='Vigil Mass'),
    'ascensionday':   dict(season='Easter', week=None, weekday='Ascension Thursday',
                           note='Mass During the Day'),
}


def classify_anchor(name):
    n = name.lower()
    if n in SKIP_ANCHORS:
        return None
    if n in SPECIAL_ANCHORS:
        return dict(SPECIAL_ANCHORS[n])
    m = WEEKDAY_RE.match(n)
    if m:
        return dict(season='Easter', week=int(m.group(2)),
                    weekday=WEEKDAY_MAP[m.group(1)])
    return None  # unknown anchors skipped


# ── Section title ──────────────────────────────────────────────────────────

def get_title(section_html):
    """Extract the day title, looking only before the entrance antiphon."""
    ent_pos = section_html.find('NTRANCE</font>')
    pre = section_html[:ent_pos] if ent_pos > 0 else section_html[:2000]

    # Strategy 1: <div align="center"> — strip nav links and small-font notes
    m = re.search(r'<div align="center">(.*?)</div>', pre, re.I | re.S)
    if m:
        div = m.group(1)
        div = re.sub(r'<a\s+href=[^>]+>.*?</a>', '', div, flags=re.S | re.I)
        div = re.sub(r'<font size="-1">.*?</font>', '', div, flags=re.S | re.I)
        t = clean(strip_tags(div))
        if len(t) > 3:
            return t

    # Strategy 2: first <u>...</u> block in pre-antiphon area
    m = re.search(r'<u>(.*?)</u>', pre, re.I | re.S)
    if m:
        t = clean(strip_tags(m.group(1)))
        if 3 < len(t) < 200:
            return t

    # Strategy 3 (fallback for cases where <div>/<u> opened before the anchor):
    # take text right after </a> up to the first <br>
    anc_end = pre.find('</a>')
    if anc_end != -1:
        after = pre[anc_end + 4:]
        br_pos = after.find('<br')
        t = clean(strip_tags(after[:br_pos if br_pos != -1 else len(after)]))
        if len(t) > 3:
            return t

    return ''


# ── Antiphon block extraction ──────────────────────────────────────────────

# Match the antiphon header up to (and including) its closing </b>
ENT_SIG = re.compile(r'NTRANCE</font>.*?NTIPHON</font>.*?</b>', re.I | re.S)
COM_SIG = re.compile(r'OMMUNION</font>.*?NTIPHON</font>.*?</b>', re.I | re.S)

# End-of-block markers
# Entrance antiphon ends just before GLORIA or COLLECT header
ENT_END = re.compile(r'<b>(?:<a[^>]*>)?[GC]<font[^>]*>(?:LORIA|OLLECT)', re.I)
# Communion antiphon ends just before PRAYER AFTER COMMUNION
COM_END = re.compile(r'<b>.*?RAYER.*?FTER', re.I | re.S)

# Latin text: <font size="-1"> with at least 10 characters
LATIN_RE = re.compile(r'<font size="-1">(.{10,}?)</font>', re.S | re.I)
# Citation: <i>text</i> where text has no inner tags
CITATION_RE = re.compile(r'<i>([^<]{2,})</i>')

# Markers that introduce antiphon alternatives.
# Handles: <i>Or:</i>  <i>Or: </i>  <i>Or:&nbsp;...citation...</i>
DELIM_RE = re.compile(
    r'<i>\s*Or\s*:'                             # <i>Or: (any form)
    r'|Optional\s+for\s+Year\s+([A-C])\s*:',   # Optional for Year A/B/C:
    re.I
)


def get_antiphon_block(section_html, kind):
    """Return the raw HTML of the antiphon block, or None if not found."""
    sig = ENT_SIG if kind == 'entrance' else COM_SIG
    end = ENT_END if kind == 'entrance' else COM_END

    m = sig.search(section_html)
    if not m:
        return None

    block_start = m.end()
    end_m = end.search(section_html, block_start)
    return section_html[block_start: end_m.start() if end_m else len(section_html)]


def parse_antiphon_block(block_html):
    """
    Parse one antiphon block into a list of options.
    Each option has: citation, latin, english, and optionally year.
    """
    if not block_html:
        return []

    delims = list(DELIM_RE.finditer(block_html))
    # Chunk boundaries: [0, delim0.start, delim1.start, ..., len]
    bounds = [0] + [d.start() for d in delims] + [len(block_html)]
    # Year for each chunk: chunk 0 has no year, chunk i+1 gets year from delims[i]
    chunk_years = [None] + [
        ('Year ' + d.group(1) if d.group(1) else None) for d in delims
    ]

    results = []
    for i, (cs, ce) in enumerate(zip(bounds, bounds[1:])):
        chunk = block_html[cs:ce]
        year  = chunk_years[i]

        # Find the Latin text (first substantial <font size="-1"> block)
        lat_m = LATIN_RE.search(chunk)
        if not lat_m:
            continue

        latin = clean(strip_tags(lat_m.group(1)))
        if not latin:
            continue

        # Find citation: last <i>...</i> before the Latin that isn't "Or:" etc.
        pre = chunk[:lat_m.start()]
        citation = None
        for c in reversed(CITATION_RE.findall(pre)):
            c = clean(c)
            if not re.match(r'or\s*:', c, re.I) and not c.lower().startswith('optional'):
                citation = c or None
                break
        # Fallback: citation embedded in <i>Or:&nbsp;...CITATION</i>
        if citation is None:
            m = re.search(r'<i>\s*Or\s*:?\s*(?:&nbsp;|\s)+([^<]{2,})</i>', pre, re.I)
            if m:
                citation = clean(m.group(1)) or None

        # English text: everything after the Latin block
        english = clean(strip_tags(chunk[lat_m.end():]))

        entry = {'citation': citation, 'latin': latin, 'english': english}
        if year:
            entry['year'] = year
        results.append(entry)

    return results


# ── Section parser ─────────────────────────────────────────────────────────

def parse_section(name, section_html):
    info  = classify_anchor(name)
    title = get_title(section_html)

    ent_block = get_antiphon_block(section_html, 'entrance')
    com_block = get_antiphon_block(section_html, 'communion')

    return {
        'anchor': name,
        **info,
        'title':             title,
        'entrance_antiphon': parse_antiphon_block(ent_block),
        'communion_antiphon': parse_antiphon_block(com_block),
    }


# ── Main ───────────────────────────────────────────────────────────────────

def parse_easter_mht(mht_path):
    html = extract_html_from_mht(mht_path)
    anchor_re = re.compile(r'<a\s+name="([^"]+)"', re.I)
    anchors   = list(anchor_re.finditer(html))

    days = []
    for i, anc in enumerate(anchors):
        name = anc.group(1)
        if classify_anchor(name) is None:
            continue
        sec_start = anc.start()
        sec_end   = anchors[i + 1].start() if i + 1 < len(anchors) else len(html)
        days.append(parse_section(name, html[sec_start:sec_end]))

    return days


def main():
    mht_path = sys.argv[1] if len(sys.argv) > 1 else 'Mass Propers for Easter.mht'
    out_path  = sys.argv[2] if len(sys.argv) > 2 else 'easter_propers.json'

    days = parse_easter_mht(mht_path)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(days, f, indent=2, ensure_ascii=False)

    print(f"Parsed {len(days)} days → {out_path}")
    for d in days:
        e = len(d['entrance_antiphon'])
        c = len(d['communion_antiphon'])
        print(f"  {d['anchor']:20s}  E:{e}  C:{c}  {d.get('title','')[:50]}")


if __name__ == '__main__':
    main()
