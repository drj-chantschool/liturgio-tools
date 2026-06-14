#!/usr/bin/env python3
"""
Fetch and parse Mass propers (Entrance & Communion antiphons) for saints' feast days
from https://www.liturgies.net/saints/saints.htm.

Outputs: translations/saints_propers.json

Each JSON entry:
    {
        "month": 11,
        "day_of_month": 30,
        "title": "St. Andrew, Apostle",
        "page_url": "https://www.liturgies.net/saints/andrew/andrew.htm",
        "mass_url": "https://www.liturgies.net/saints/andrew/mass.htm",
        "entrance_antiphon": [...],
        "communion_antiphon": [...]
    }

Usage:
    python translations/tools/parse_saints.py [outdir]
    outdir defaults to translations/
"""

import re
import json
import sys
import time
import urllib.request
import urllib.error
from html import unescape
from pathlib import Path


# ── Shared utilities (mirrors parse_propers.py) ────────────────────────────

def fetch_html(url, retries=2, delay=1.5):
    """Fetch URL with simple retry logic."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # caller handles missing pages
            if attempt < retries:
                time.sleep(delay)
                continue
            raise
        except Exception:
            if attempt < retries:
                time.sleep(delay)
                continue
            raise
    return None


def strip_tags(html):
    return unescape(re.sub(r'<[^>]+>', '', html))


def clean(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


# ── Antiphon parsing (same as parse_propers.py) ────────────────────────────

# Three heading formats on liturgies.net:
#   Format A (two tags): E<font>NTRANCE</font> A<font>NTIPHON</font></b>
#   Format B (one tag):  E<font>NTRANCE ANTIPHON</a></font></b>
#   Format C (two tags, "Song"): E<font>NTRANCE</font> S<font>ONG</font></b>
ENT_SIG = re.compile(
    r'NTRANCE(?:</font>.*?(?:NTIPHON|ONG)</font>|[^<]*(?:NTIPHON|ONG)(?:</a>)?</font>).*?</b>',
    re.I | re.S)
COM_SIG = re.compile(
    r'OMMUNION(?:</font>.*?(?:NTIPHON|ONG)</font>|[^<]*(?:NTIPHON|ONG)(?:</a>)?</font>).*?</b>',
    re.I | re.S)

ENT_END = re.compile(r'<b>(?:<a[^>]*>)?[GC]<font[^>]*>(?:LORIA|OLLECT|REDO)', re.I)
COM_END = re.compile(r'<b>.*?RAYER.*?FTER', re.I | re.S)

LATIN_RE = re.compile(r'<font size="-1">(.{10,}?)</font>', re.S | re.I)
CITATION_RE = re.compile(r'<i>([^<]{2,})</i>')
DELIM_RE = re.compile(
    r'<i>\s*Or\s*:'
    r'|Optional\s+for\s+Year\s+([A-C])\s*:',
    re.I
)


def get_antiphon_block(html, kind):
    """Return the raw HTML of the antiphon block, or None."""
    sig = ENT_SIG if kind == 'entrance' else COM_SIG
    end_re = ENT_END if kind == 'entrance' else COM_END
    m = sig.search(html)
    if not m:
        return None
    block_start = m.end()
    end_m = end_re.search(html, block_start)
    return html[block_start: end_m.start() if end_m else len(html)]


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

        if lat_m:
            # Normal case: Latin text in <font size="-1"> block
            latin = clean(strip_tags(lat_m.group(1)))
            if not latin:
                continue
            pre = chunk[:lat_m.start()]
            citation = _extract_citation(pre)
            english_html = re.sub(r'<i>[^<]*</i>', '', chunk[lat_m.end():], flags=re.I)
            english = clean(strip_tags(english_html))
            entry = {'citation': citation, 'latin': latin, 'english': english}
        else:
            # English-only: no Latin block; capture plain text after any citation
            citation = _extract_citation(chunk)
            # Remove all tags and clean up
            text_only = re.sub(r'<i>[^<]*</i>', '', chunk, flags=re.I)
            english = clean(strip_tags(text_only))
            if not english or len(english) < 5:
                continue
            entry = {'citation': citation, 'latin': '', 'english': english,
                     'english_only': True}

        if year:
            entry['year'] = year
        results.append(entry)
    return results


def _extract_citation(html_pre):
    """Extract the first non-rubric italic citation from an HTML fragment."""
    citation = None
    for c in reversed(CITATION_RE.findall(html_pre)):
        c = clean(c)
        if not re.match(r'or\s*:', c, re.I) and not c.lower().startswith('optional'):
            citation = c or None
            break
    if citation is None:
        om = re.search(r'<i>\s*Or\s*:?\s*(?:&nbsp;|\s)+([^<]{2,})</i>', html_pre, re.I)
        if om:
            citation = clean(om.group(1)) or None
    return citation


# ── Saints index parsing ───────────────────────────────────────────────────

BASE_URL = 'https://www.liturgies.net'
INDEX_URL = 'https://www.liturgies.net/saints/saints.htm'

MONTH_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

# Matches "December 3", "November 30", etc.
DATE_RE = re.compile(
    r'\b(January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+(\d{1,2})\b',
    re.I
)

# Matches any <a href="...">text</a>
LINK_RE = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
                     re.I | re.S)

# Paths that indicate a saint/feast page (not navigation)
FEAST_PATH_RE = re.compile(
    r'^(https?://www\.liturgies\.net)?'
    r'/(saints/|Christmas/|Epiphany/|Transfiguration/|Pentecost/)',
    re.I
)

# Paths to exclude: movable feasts already handled in other parsers (Epiphany,
# Baptism of the Lord) or that lack fixed calendar dates.
EXCLUDE_PATH_RE = re.compile(r'/Epiphany/', re.I)


def derive_mass_url(page_url):
    """
    Given a saint's main page URL, return the likely mass page URL.
    Replaces the last path component with 'mass.htm'.

    /saints/andrew/andrew.htm -> /saints/andrew/mass.htm
    /Christmas/Holyname/holyname.htm -> /Christmas/Holyname/mass.htm
    """
    if '/' not in page_url:
        return None
    parent = page_url.rsplit('/', 1)[0]
    return parent + '/mass.htm'


# Link text patterns that indicate non-Catholic denomination pages to skip
NON_CATH_RE = re.compile(r'orthodox|episcopal|lutheran|anglican|protestant', re.I)

# Priority order for Catholic mass/readings links by text keyword.
# "Catholic Readings" requires both words; plain "Readings" is the fallback.
CATH_PRIORITY = [
    re.compile(r'\bmass\b', re.I),
    re.compile(r'\bcatholic\b.*\breading|\breading.*\bcatholic', re.I),
    re.compile(r'\breading', re.I),
]


def find_mass_url_from_page(page_url):
    """
    Fetch the saint's main page and return the URL for the Catholic
    mass/readings page, by following the explicit link there.

    Only considers RELATIVE hrefs (no leading '/') to avoid matching
    site-wide navigation links.  Priority: "Mass" > "Catholic Readings"
    > "Reading*", skipping Orthodox/Episcopal/Lutheran text.
    Returns None if no suitable link is found.
    """
    html = fetch_html(page_url)
    if not html:
        return None

    parent = page_url.rsplit('/', 1)[0]
    LINK_RE_LOCAL = re.compile(
        r'<a\s[^>]*href=["\']([^"\'#/][^"\']*\.htm)["\'][^>]*>(.*?)</a>',
        re.I | re.S
    )

    buckets = {i: None for i in range(len(CATH_PRIORITY))}
    for m in LINK_RE_LOCAL.finditer(html):
        href = m.group(1).strip()
        text = clean(strip_tags(m.group(2)))
        if NON_CATH_RE.search(text):
            continue
        url = parent + '/' + href
        for i, pat in enumerate(CATH_PRIORITY):
            if buckets[i] is None and pat.search(text):
                buckets[i] = url
                break

    for i in range(len(CATH_PRIORITY)):
        if buckets[i]:
            return buckets[i]
    return None


def parse_saints_index(html):
    """
    Parse saints.htm to extract (month, day, title, page_url) for each feast.
    Strategy: find all links to feast pages; for each link, use the most recent
    date mention that appears before it in the HTML.

    The page has two columns: a date-ordered list (left) and an alphabetical
    list with no dates (right).  Truncate at the alphabetical section header so
    undated alphabetical links don't inherit the last calendar date.

    Returns list of dicts, ordered as they appear on the page.
    """
    # Truncate at the alphabetical section to ignore undated links there
    alph_m = re.search(r'Alphabetically', html, re.I)
    if alph_m:
        html = html[:alph_m.start()]

    # Collect all date positions
    dates = [
        (m.start(), MONTH_NUM[m.group(1).lower()], int(m.group(2)))
        for m in DATE_RE.finditer(html)
    ]
    if not dates:
        raise ValueError('No dates found in saints index HTML')

    entries = []
    for lm in LINK_RE.finditer(html):
        href = lm.group(1).strip()
        title_html = lm.group(2)
        title = clean(strip_tags(title_html))
        if not title or len(title) < 2:
            continue

        # Normalize href to absolute URL
        if href.startswith('http://') or href.startswith('https://'):
            url = href
        elif href.startswith('/'):
            url = BASE_URL + href
        else:
            continue  # relative paths without leading / — skip

        # Only keep feast-page links
        if not FEAST_PATH_RE.match(url):
            continue

        # Exclude movable feasts handled elsewhere (Epiphany, Baptism of the Lord)
        if EXCLUDE_PATH_RE.search(url):
            continue

        # Must end with .htm
        if not url.lower().endswith('.htm'):
            continue

        # Find the most recent date that precedes this link
        link_pos = lm.start()
        month, day = None, None
        for (date_pos, m_num, d_num) in reversed(dates):
            if date_pos < link_pos:
                month, day = m_num, d_num
                break

        if month is None:
            continue  # no date found before this link

        # Derive mass URL
        mass_url = derive_mass_url(url)

        entries.append({
            'month': month,
            'day_of_month': day,
            'title': title,
            'page_url': url,
            'mass_url': mass_url,
        })

    # Deduplicate: keep first occurrence of each (month, day, page_url) triple
    seen = set()
    deduped = []
    for e in entries:
        key = (e['month'], e['day_of_month'], e['page_url'])
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped


# ── Common-of detection ────────────────────────────────────────────────────

# Links like href="...roman_missal_commons.htm#bvm" with text "From the Common of the BVM"
# Allow inner tags (e.g. <i>) inside link text; strip_tags handles them.
COMMON_LINK_RE = re.compile(
    r'<a\s[^>]*href=["\'][^"\']*commons[^"\']*["\'][^>]*>(.*?)</a>',
    re.I | re.S
)


def detect_common_of(html):
    """
    Return list of common-of reference strings found near the top of the page.
    E.g. ['From the Common of the Blessed Virgin Mary'] or
         ['For a Virgin Martyr', 'For One Virgin']
    """
    top = html[:3000]
    results = []
    for m in COMMON_LINK_RE.finditer(top):
        text = clean(strip_tags(m.group(1)))
        if len(text) >= 3:
            results.append(text)
    return results


# ── Mass page parsing ──────────────────────────────────────────────────────

def parse_mass_page(html):
    """Parse a mass page; return (entrance_antiphons, communion_antiphons, common_of_list)."""
    ent_block = get_antiphon_block(html, 'entrance')
    com_block = get_antiphon_block(html, 'communion')
    common_of = detect_common_of(html)
    return parse_antiphon_block(ent_block), parse_antiphon_block(com_block), common_of


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent

    print(f'Fetching saints index: {INDEX_URL}')
    index_html = fetch_html(INDEX_URL)
    entries = parse_saints_index(index_html)
    print(f'Found {len(entries)} feast entries in index.')

    # Fetch each unique mass URL once, cache results
    mass_cache = {}  # mass_url -> (entrance, communion)
    failed_urls = set()

    results = []
    for i, entry in enumerate(entries):
        title = entry['title']
        mo, da = entry['month'], entry['day_of_month']

        # Step 1: try mass.htm (fast path, works for most full-liturgy pages)
        mass_url = None
        cached = False
        fast_candidate = entry['page_url'].rsplit('/', 1)[0] + '/mass.htm'

        if fast_candidate in mass_cache:
            mass_url = fast_candidate
            cached = True
        elif fast_candidate not in failed_urls:
            print(f'  [{i+1}/{len(entries)}] Fetching {fast_candidate} ({title})')
            html = fetch_html(fast_candidate)
            if html is not None:
                mass_cache[fast_candidate] = parse_mass_page(html)
                mass_url = fast_candidate
            else:
                failed_urls.add(fast_candidate)

        # Step 2: follow the actual link from the saint's main page
        if mass_url is None:
            found_url = find_mass_url_from_page(entry['page_url'])
            if found_url and found_url != fast_candidate:
                if found_url in mass_cache:
                    mass_url = found_url
                    cached = True
                elif found_url not in failed_urls:
                    print(f'  [{i+1}/{len(entries)}] Fetching {found_url} ({title})')
                    html = fetch_html(found_url)
                    if html is not None:
                        mass_cache[found_url] = parse_mass_page(html)
                        mass_url = found_url
                    else:
                        failed_urls.add(found_url)

        if mass_url is None:
            print(f'  [{i+1}/{len(entries)}] No mass page found for {mo:02d}-{da:02d} {title}')
            continue

        if cached:
            print(f'  [{i+1}/{len(entries)}] Cache hit {mass_url.rsplit("/",1)[-1]} ({title})')

        entry['mass_url'] = mass_url

        ent, com, common_of = mass_cache[mass_url]
        result = {
            'month': mo,
            'day_of_month': da,
            'title': title,
            'page_url': entry['page_url'],
            'mass_url': mass_url,
            'entrance_antiphon': ent,
            'communion_antiphon': com,
            'common_of': common_of,
        }
        results.append(result)
        e_count = len(ent)
        c_count = len(com)
        co_str = f'  [common: {"; ".join(common_of)}]' if common_of else ''
        mark = '' if (e_count and c_count) else ('  *** MISSING ***' if not common_of else '')
        print(f'    -> E:{e_count}  C:{c_count}{co_str}{mark}')

    out_path = out_dir / 'saints_propers.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    total_e = sum(len(r['entrance_antiphon']) for r in results)
    total_c = sum(len(r['communion_antiphon']) for r in results)
    missing = [r for r in results if not r['entrance_antiphon'] or not r['communion_antiphon']]
    print()
    print(f'Output: {out_path}')
    print(f'Entries: {len(results)}, Entrance antiphons: {total_e}, Communion antiphons: {total_c}')
    if missing:
        print(f'Missing antiphons ({len(missing)}):')
        for r in missing:
            print(f'  {r["month"]:02d}-{r["day_of_month"]:02d}  {r["title"]}  E:{len(r["entrance_antiphon"])}  C:{len(r["communion_antiphon"])}')


if __name__ == '__main__':
    main()
