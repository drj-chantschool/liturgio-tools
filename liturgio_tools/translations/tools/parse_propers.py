#!/usr/bin/env python3
"""
Fetch and parse Mass propers (Entrance & Communion antiphons) for Advent,
Christmas, Lent/Holy Week, and Ordinary Time from liturgies.net.

Outputs JSON files: advent_propers.json, christmas_propers.json,
lent_propers.json, ordinary_propers.json in the translations/ directory.

Usage:
    python translations/tools/parse_propers.py [outdir]
    outdir defaults to translations/
"""

import re
import json
import sys
import urllib.request
from html import unescape
from pathlib import Path


# ── Shared utilities ───────────────────────────────────────────────────────

def fetch_html(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode('utf-8', errors='replace')

def strip_tags(html):
    return unescape(re.sub(r'<[^>]+>', '', html))

def clean(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


# ── Antiphon parsing (same logic as parse_easter.py) ──────────────────────

# Match the antiphon header (small-caps style: E<FONT>NTRANCE</font> A<FONT>NTIPHON</font>)
ENT_SIG = re.compile(r'NTRANCE</font>.*?NTIPHON</font>.*?</b>', re.I | re.S)
COM_SIG = re.compile(r'OMMUNION</font>.*?NTIPHON</font>.*?</b>', re.I | re.S)

# End-of-block markers
ENT_END = re.compile(r'<b>(?:<a[^>]*>)?[GC]<font[^>]*>(?:LORIA|OLLECT|REDO)', re.I)
COM_END = re.compile(r'<b>.*?RAYER.*?FTER', re.I | re.S)

# Latin text block: <font size="-1"> with ≥10 chars
LATIN_RE = re.compile(r'<font size="-1">(.{10,}?)</font>', re.S | re.I)
# Scriptural citation: <i>text</i> with no inner tags
CITATION_RE = re.compile(r'<i>([^<]{2,})</i>')
# Antiphon alternative delimiter
DELIM_RE = re.compile(
    r'<i>\s*Or\s*:'
    r'|Optional\s+for\s+Year\s+([A-C])\s*:',
    re.I
)


def get_antiphon_block(section_html, kind):
    """Return the raw HTML of the antiphon block, or None."""
    sig = ENT_SIG if kind == 'entrance' else COM_SIG
    end_re = ENT_END if kind == 'entrance' else COM_END
    m = sig.search(section_html)
    if not m:
        return None
    block_start = m.end()
    end_m = end_re.search(section_html, block_start)
    return section_html[block_start: end_m.start() if end_m else len(section_html)]


def parse_antiphon_block(block_html):
    """Parse one antiphon block into a list of option dicts."""
    if not block_html:
        return []
    delims = list(DELIM_RE.finditer(block_html))
    bounds = [0] + [d.start() for d in delims] + [len(block_html)]
    chunk_years = [None] + [
        ('Year ' + d.group(1) if d.group(1) else None) for d in delims
    ]
    results = []
    for i, (cs, ce) in enumerate(zip(bounds, bounds[1:])):
        chunk = block_html[cs:ce]
        year = chunk_years[i]
        lat_m = LATIN_RE.search(chunk)
        if not lat_m:
            continue
        latin = clean(strip_tags(lat_m.group(1)))
        if not latin:
            continue
        pre = chunk[:lat_m.start()]
        citation = None
        for c in reversed(CITATION_RE.findall(pre)):
            c = clean(c)
            if not re.match(r'or\s*:', c, re.I) and not c.lower().startswith('optional'):
                citation = c or None
                break
        if citation is None:
            om = re.search(r'<i>\s*Or\s*:?\s*(?:&nbsp;|\s)+([^<]{2,})</i>', pre, re.I)
            if om:
                citation = clean(om.group(1)) or None
        # Strip italic rubric notes (e.g. "The Gloria … is not said.") from
        # the English portion; citations appear before the Latin, not after.
        english_html = re.sub(r'<i>[^<]*</i>', '', chunk[lat_m.end():], flags=re.I)
        english = clean(strip_tags(english_html))
        entry = {'citation': citation, 'latin': latin, 'english': english}
        if year:
            entry['year'] = year
        results.append(entry)
    return results


def parse_page(html, classifiers):
    """
    Extract antiphons for all anchors listed in classifiers dict.
    Section boundaries: from this relevant anchor to the next relevant anchor,
    so reading tables with sub-anchors in between are included in the section
    (the antiphon parser will still find the correct blocks).

    classifiers: {anchor_name: meta_dict}
    Returns list of day dicts.
    """
    anchor_re = re.compile(r'<a\s+name="([^"]+)"', re.I)
    all_anchors = list(anchor_re.finditer(html))

    relevant = [(m.start(), m.group(1)) for m in all_anchors if m.group(1) in classifiers]

    days = []
    for j, (pos, name) in enumerate(relevant):
        sec_start = pos
        sec_end = relevant[j + 1][0] if j + 1 < len(relevant) else len(html)
        section = html[sec_start:sec_end]
        ent_block = get_antiphon_block(section, 'entrance')
        com_block = get_antiphon_block(section, 'communion')
        entry = {
            'anchor': name,
            **classifiers[name],
            'entrance_antiphon': parse_antiphon_block(ent_block),
            'communion_antiphon': parse_antiphon_block(com_block),
        }
        days.append(entry)
    return days


# ── Season-specific classifiers ────────────────────────────────────────────

# wkday: 1=Sunday, 2=Monday, ..., 7=Saturday (matches liturgical_day.seq)
WEEKDAY_NUM = {
    'sunday': 1, 'monday': 2, 'tuesday': 3, 'wednesday': 4,
    'thursday': 5, 'friday': 6, 'saturday': 7,
}


def _ordinal(n):
    suffixes = {1: '1st', 2: '2nd', 3: '3rd'}
    return suffixes.get(n, f'{n}th')


def advent_classifiers():
    """
    ADV/I: weeks 1–3 (all days), plus 4th Sunday.
    ADV/II: Dec 17–24 (wkday = seq offset, 1=Dec17 … 8=Dec24).

    Note: the Advent page omits 3rd Saturday (the Dec 17 period begins there);
    the classifier entry for it simply won't match any anchor.
    """
    cls = {}
    for wk in range(1, 4):
        for day, num in WEEKDAY_NUM.items():
            anchor = f'{wk}{day}'
            cls[anchor] = {
                'season': 'ADV', 'subseason': 'I',
                'wknum': wk, 'wkday': num,
                'title': f'{_ordinal(wk)} {day.capitalize()} of Advent',
            }
    # 4th Sunday (always falls in Dec 17-23 range but has its own propers)
    cls['4sunday'] = {
        'season': 'ADV', 'subseason': 'I',
        'wknum': 4, 'wkday': 1,
        'title': 'Fourth Sunday of Advent',
    }
    # Dec 17-24 (ADV/II; wkday used as sequential offset: 1=Dec17 … 8=Dec24)
    for i, day_num in enumerate(range(17, 25)):
        cls[str(day_num)] = {
            'season': 'ADV', 'subseason': 'II',
            'wknum': 0, 'wkday': i + 1,
            'title': f'December {day_num}',
        }
    return cls


def christmas_classifiers():
    """
    NAT season.
    For the four Christmas Masses (Vigil/Night/Dawn/Day), wkday encodes
    mass order (0=Vigil, 1=Night, 2=Dawn, 3=Day) since Christmas has no
    meaningful weekday.  Same encoding for Epiphany (0=Vigil, 1=Day).
    """
    return {
        # Christmas
        'vigilmass':     {'season':'NAT','subseason':'DAY','wknum':0,'wkday':0,'title':'Christmas: Vigil Mass'},
        'massatnight':   {'season':'NAT','subseason':'DAY','wknum':0,'wkday':1,'title':'Christmas: Mass at Night'},
        'massatdawn':    {'season':'NAT','subseason':'DAY','wknum':0,'wkday':2,'title':'Christmas: Mass at Dawn'},
        'massday':       {'season':'NAT','subseason':'DAY','wknum':0,'wkday':3,'title':'Christmas: Mass during the Day'},
        # Within octave (fixed feasts; wkday = sequential day within octave)
        'stephen':       {'season':'NAT','subseason':'IO','wknum':1,'wkday':2,'title':'St. Stephen (Dec 26)'},
        'john':          {'season':'NAT','subseason':'IO','wknum':1,'wkday':3,'title':'St. John the Apostle (Dec 27)'},
        'innocents':     {'season':'NAT','subseason':'IO','wknum':1,'wkday':4,'title':'Holy Innocents (Dec 28)'},
        'holyfamily':    {'season':'NAT','subseason':'IO','wknum':1,'wkday':0,'title':'Holy Family'},
        'dec29':         {'season':'NAT','subseason':'IO','wknum':1,'wkday':5,'title':'December 29'},
        'dec30':         {'season':'NAT','subseason':'IO','wknum':1,'wkday':6,'title':'December 30'},
        'dec31':         {'season':'NAT','subseason':'IO','wknum':1,'wkday':7,'title':'December 31'},
        # New Year / Jan 1
        'mary':          {'season':'NAT','subseason':'OCT','wknum':2,'wkday':1,'title':'Solemnity of Mary (Jan 1)'},
        # Second Sunday of Christmas
        '2ndsunday':     {'season':'NAT','subseason':'PO','wknum':2,'wkday':1,'title':'Second Sunday of Christmas'},
        # Jan 2–7 before Epiphany (when Epiphany falls on Jan 6+)
        'jan2before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':2,'title':'Jan 2 (before Epiphany)'},
        'jan3before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':3,'title':'Jan 3 (before Epiphany)'},
        'jan4before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':4,'title':'Jan 4 (before Epiphany)'},
        'jan5before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':5,'title':'Jan 5 (before Epiphany)'},
        'jan6before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':6,'title':'Jan 6 (before Epiphany)'},
        'jan7before':    {'season':'NAT','subseason':'PO','wknum':2,'wkday':7,'title':'Jan 7 (before Epiphany)'},
        # Epiphany
        'epiphanyvigil': {'season':'NAT','subseason':'EPI','wknum':0,'wkday':0,'title':'Epiphany: Vigil'},
        'epiphanyday':   {'season':'NAT','subseason':'EPI','wknum':0,'wkday':1,'title':'Epiphany of the Lord'},
        # Weekdays after Epiphany (before Baptism)
        'monday':        {'season':'NAT','subseason':'EPI','wknum':0,'wkday':2,'title':'Monday after Epiphany'},
        'tuesday':       {'season':'NAT','subseason':'EPI','wknum':0,'wkday':3,'title':'Tuesday after Epiphany'},
        'wednesday':     {'season':'NAT','subseason':'EPI','wknum':0,'wkday':4,'title':'Wednesday after Epiphany'},
        'thursday':      {'season':'NAT','subseason':'EPI','wknum':0,'wkday':5,'title':'Thursday after Epiphany'},
        'friday':        {'season':'NAT','subseason':'EPI','wknum':0,'wkday':6,'title':'Friday after Epiphany'},
        'saturday':      {'season':'NAT','subseason':'EPI','wknum':0,'wkday':7,'title':'Saturday after Epiphany'},
        # Baptism of the Lord
        'baptism':       {'season':'NAT','subseason':'BAPT','wknum':1,'wkday':1,'title':'Baptism of the Lord'},
    }


def lent_classifiers():
    """
    TQ season: Ash Wednesday week (wknum=0), Lent weeks 1–5, Holy Week.
    Holy Thursday: thursdayofthelordssupper (not thursday6, which is Chrism Mass rubrics).
    Good Friday: friday6 section contains the full liturgy including communion.
    saturday6 is included only as a section boundary (Easter Vigil, no standalone antiphons).
    """
    cls = {
        # Ash Wednesday week (wkday = seq value in liturgical_day)
        'ashwednesday': {'season':'TQ','subseason':'LENT','wknum':0,'wkday':4,'title':'Ash Wednesday'},
        'ashthursday':  {'season':'TQ','subseason':'LENT','wknum':0,'wkday':5,'title':'Thursday after Ash Wednesday'},
        'ashfriday':    {'season':'TQ','subseason':'LENT','wknum':0,'wkday':6,'title':'Friday after Ash Wednesday'},
        'ashsaturday':  {'season':'TQ','subseason':'LENT','wknum':0,'wkday':7,'title':'Saturday after Ash Wednesday'},
    }
    # Lent weeks 1–5 (anchor format: {dayname}{weeknum})
    for wk in range(1, 6):
        for day, num in WEEKDAY_NUM.items():
            anchor = f'{day}{wk}'
            cls[anchor] = {
                'season': 'TQ', 'subseason': 'LENT',
                'wknum': wk, 'wkday': num,
                'title': f'{_ordinal(wk)} {day.capitalize()} of Lent',
            }
    # Holy Week
    cls.update({
        'sunday6':                  {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':1,'title':'Palm Sunday'},
        'monday6':                  {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':2,'title':'Monday of Holy Week'},
        'tuesday6':                 {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':3,'title':'Tuesday of Holy Week'},
        'wednesday6':               {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':4,'title':'Wednesday of Holy Week'},
        'thursdayofthelordssupper': {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':5,'title':"Mass of the Lord's Supper"},
        'friday6':                  {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':6,'title':'Good Friday'},
        # Included only as a section boundary; Easter Vigil antiphons are in PASC data
        'saturday6':                {'season':'TQ','subseason':'HOLYWEEK','wknum':0,'wkday':7,'title':'Holy Saturday'},
    })
    return cls


def ordinary_classifiers():
    """
    OT season: weeks 1–34 (one antiphon set per week, wkday=None = all days).
    34weekdays is a separate weekday-specific entry for week 34.
    """
    cls = {}
    for wk in range(1, 35):
        cls[f'week{wk}'] = {
            'season': 'OT', 'subseason': 'OT',
            'wknum': wk, 'wkday': None,
            'title': f'{_ordinal(wk)} Week in Ordinary Time',
        }
    cls['34weekdays'] = {
        'season': 'OT', 'subseason': 'OT',
        'wknum': 34, 'wkday': None,
        'title': 'Weekdays of the 34th Week in Ordinary Time',
        'note': 'weekdays-only variant',
    }
    return cls


# ── Main ───────────────────────────────────────────────────────────────────

BASE_URL = 'https://www.liturgies.net/Liturgies/Catholic/roman_missal'

SEASONS = [
    ('advent',    f'{BASE_URL}/adventmass.htm',    advent_classifiers),
    ('christmas', f'{BASE_URL}/christmasmass.htm', christmas_classifiers),
    ('lent',      f'{BASE_URL}/lentmass.htm',      lent_classifiers),
    ('ordinary',  f'{BASE_URL}/ordinarymass.htm',  ordinary_classifiers),
]

# Feasts whose antiphons live on their own page (no anchor-based slicing needed).
# Merged into the named season's JSON output.
STANDALONE_ENTRIES = {
    'christmas': [
        {
            'url': 'https://www.liturgies.net/Epiphany/Baptism/massreadings.htm',
            'meta': {
                'anchor': 'baptism',
                'season': 'NAT', 'subseason': 'BAPT', 'wknum': 1, 'wkday': 1,
                'title': 'Baptism of the Lord',
            },
        },
    ],
}


def parse_standalone(entry):
    """Fetch a single-page entry and extract its antiphons."""
    html = fetch_html(entry['url'])
    ent_block = get_antiphon_block(html, 'entrance')
    com_block = get_antiphon_block(html, 'communion')
    return {
        **entry['meta'],
        'entrance_antiphon': parse_antiphon_block(ent_block),
        'communion_antiphon': parse_antiphon_block(com_block),
    }


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, url, classifier_fn in SEASONS:
        classifiers = classifier_fn()
        print(f'Fetching {name} ({len(classifiers)} expected anchors) from {url} ...')
        html = fetch_html(url)
        days = parse_page(html, classifiers)

        # Merge standalone entries (replace placeholder empty entries)
        for se in STANDALONE_ENTRIES.get(name, []):
            anchor = se['meta']['anchor']
            # Replace the empty placeholder if present, otherwise append
            for i, d in enumerate(days):
                if d['anchor'] == anchor:
                    days[i] = parse_standalone(se)
                    break
            else:
                days.append(parse_standalone(se))

        out_path = out_dir / f'{name}_propers.json'
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(days, f, indent=2, ensure_ascii=False)

        total_e = sum(len(d['entrance_antiphon']) for d in days)
        total_c = sum(len(d['communion_antiphon']) for d in days)
        print(f'  -> {out_path.name}: {len(days)} entries, {total_e} entrance antiphons, {total_c} communion antiphons')
        for d in days:
            e = len(d['entrance_antiphon'])
            c = len(d['communion_antiphon'])
            mark = '' if (e and c) else '  *** MISSING ***'
            print(f"    {d['anchor']:30s}  E:{e}  C:{c}  {d.get('title','')[:40]}{mark}")
        print()


if __name__ == '__main__':
    main()
