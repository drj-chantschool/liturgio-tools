# Handoff: lit_part_texts Loading — Saints Session

> **Note (roadmap step 5):** the `lit_part_texts` table was renamed to
> `lit_part_sources` and gained a `status` flag and source-page provenance
> (`book`/`pdf_page_num`/`bbox`, FK to the new `books` table). References to
> `lit_part_texts` below are historical; the current table is `lit_part_sources`.

## What was done

Parsed and loaded ICEL Mass propers (entrance + communion antiphons, Latin + English) from liturgies.net into the `lit_part_texts` table for four seasons:

| Season | Code | Rows |
|--------|------|------|
| Advent | ADV | 58 |
| Christmas/Epiphany | NAT | 44 |
| Lent + Holy Week | TQ | 90 |
| Ordinary Time | OT | 71 |
| Easter (prior session) | PASC | 109 |
| **Total** | | **372** |

## Scripts

- `translations/tools/parse_propers.py` — fetches from liturgies.net, outputs JSON
- `translations/load_propers.py` — inserts JSON into `lit_part_texts`
- `translations/tools/parse_easter.py` — equivalent parser for Easter (already run)
- `translations/load_lit_part_texts.py` — equivalent loader for Easter (already run)

JSON files already exist in `translations/`: `advent_propers.json`, `christmas_propers.json`, `lent_propers.json`, `ordinary_propers.json`, `easter_propers.json`.

## What's next: Saints

The index page `https://www.liturgies.net/Liturgies/Catholic/roman_missal/index.htm` has 200+ saint feast links. These are not in the four seasonal pages — they live at individual URLs throughout the site (e.g. `/saints/[name]/mass.htm`, `/Pentecost/TrinitySunday/mass.htm`, etc.).

Saints are **not** keyed by season/week/wkday in `lit_part_texts` — they'll need a different column or approach. The `lit_part_texts` table has no `feast_id` or `lit_day_id` column yet, so that may need a schema change or a separate table. Confirm approach with user before writing code.

## lit_part_texts schema (relevant columns)

```
season, subseason, wknum, wkday, cycle_sun, cycle_wkday,
service_part (in/co), original_text (Latin), vernacular_text (English),
text_src (citation), assignment_authority_code (MISSAL),
translation_source_code (ROMAN_MISSAL_2010_ICEL)
```

## Known gaps (no antiphons on liturgies.net)

- Jan 2–7 before Epiphany (use the weekday-of-Christmas-time antiphons instead)
- Good Friday (no Mass)
- Holy Saturday (no Mass; Easter Vigil is under PASC)

## HTML structure of liturgies.net pages

Antiphons follow a consistent pattern:
- **Latin**: `<font size="-1">text</font>` (≥10 chars)
- **Citation**: `<i>Cf. Ps ...</i>` before the Latin block
- **English**: plain text after the Latin block, before the next heading
- Alternatives marked with `<i>Or:</i>` or `Optional for Year A/B/C:`
- Section boundaries: `<a name="anchor">` tags
