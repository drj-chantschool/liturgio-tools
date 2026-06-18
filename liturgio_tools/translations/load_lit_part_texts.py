#!/usr/bin/env python3
"""
Create lit_part_sources table and populate it from easter_propers.json.

(Formerly lit_part_texts; renamed in roadmap step 5 to the repository for all
liturgical texts from all sources, with a review-status flag and source-page
provenance.)

Usage:
    python translations/load_lit_part_texts.py [--drop] [--dry-run]
"""

import json
import sys
import argparse
from pathlib import Path

# ---------------------------------------------------------------------------
# DB connection (same pattern as liturgio_tools.py)
# ---------------------------------------------------------------------------

def _get_engine(user: str, db_name: str = 'liturgio'):
    import keyring
    from sqlalchemy import create_engine, text, exc as sa_exc

    password = keyring.get_password('liturgio-mysql', user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}@{db_name}.')

    conn_str = f'mysql+mysqlconnector://{user}:{password}@localhost:3306/{db_name}'
    engine = create_engine(conn_str, future=True)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return engine


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

# books must exist before lit_part_sources (composite FK fk_lps_book).
CREATE_BOOKS = """
CREATE TABLE IF NOT EXISTS books (
    book             VARCHAR(40)  NOT NULL,
    pdf_page_num     INT          NOT NULL,
    printed_page_num VARCHAR(16)  NULL,
    image_path       VARCHAR(512) NULL,
    image_blob       LONGBLOB     NULL,
    notes            VARCHAR(500) NULL,
    created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (book, pdf_page_num)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Full lit_part_sources schema (roadmap step 5). A fresh load produces the
# complete table: feast-day columns (month/day_of_month/feast_title/common_of,
# formerly added by load_saints.py), the review-status flag, and source-page
# provenance (book/pdf_page_num/bbox + composite FK to books). The dropped
# 'reference_assets' role is folded in: extracted text + translation live in
# original_text/vernacular_text, and the page is identified by book/pdf_page_num.
# Note: original_text and season are NULLable to allow feast/common-only rows.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS lit_part_sources (
    text_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    wkday              TINYINT UNSIGNED NULL,
    cycle_sun          TINYINT UNSIGNED NULL,
    cycle_wkday        TINYINT UNSIGNED NULL,
    service_part       VARCHAR(4)      NOT NULL,
    original_text      TEXT            NULL,
    vernacular_text    TEXT            NULL,
    text_src           VARCHAR(100)    NULL,
    original_lang      VARCHAR(10)     NOT NULL DEFAULT 'la',
    vernacular_lang    VARCHAR(10)     NULL,
    assignment_authority_code   VARCHAR(20) NULL,
    translation_source_code     VARCHAR(50) NULL,
    page_num           SMALLINT        NULL,
    month              TINYINT UNSIGNED NULL,
    day_of_month       TINYINT UNSIGNED NULL,
    feast_title        VARCHAR(150)    NULL,
    common_of          VARCHAR(200)    NULL,
    status             VARCHAR(20)     NOT NULL DEFAULT 'draft',
    book               VARCHAR(40)     NULL,
    pdf_page_num       INT             NULL,
    bbox               VARCHAR(64)     NULL,
    lit_epoch_slug     VARCHAR(64)     NULL,
    created_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_lps_book FOREIGN KEY (book, pdf_page_num)
        REFERENCES books(book, pdf_page_num),
    CONSTRAINT fk_lps_epoch FOREIGN KEY (lit_epoch_slug)
        REFERENCES lit_epoch(slug) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

WEEKDAY_NUM = {
    'Sunday': 1, 'Monday': 2, 'Tuesday': 3, 'Wednesday': 4,
    'Thursday': 5, 'Friday': 6, 'Saturday': 7,
}

# cycle_sun: Year A=1, Year B=2, Year C=0 (per CLAUDE.md)
YEAR_CYCLE = {'Year A': 1, 'Year B': 2, 'Year C': 0}

SERVICE_PART = {
    'entrance_antiphon': 'in',
    'communion_antiphon': 'co',
}


def classify_day(entry):
    """
    Return (subseason, wknum, seq) given a JSON day entry.

    Subseason logic from liturgical_day table:
      OCT      – week 1 (all), week 2 Sunday
      AD_ASC   – week 2 Mon–Sat; weeks 3–6 Sun–Wed,Thu
      ASC      – Ascension (vigil + day)
      POST_ASC – week 6 Fri–Sat; all of week 7
    """
    anchor  = entry['anchor']
    week    = entry.get('week')
    weekday = entry.get('weekday', '')

    if anchor in ('ascensionvigil', 'ascensionday'):
        return ('ASC', 6, 5)   # Thursday of week 6

    seq = WEEKDAY_NUM.get(weekday)

    if week == 1:
        return ('OCT', 1, seq)
    elif week == 2:
        if weekday == 'Sunday':
            return ('OCT', 2, 1)
        else:
            return ('AD_ASC', 2, seq)
    elif week in (3, 4, 5):
        return ('AD_ASC', week, seq)
    elif week == 6:
        if weekday in ('Friday', 'Saturday'):
            return ('POST_ASC', 6, seq)
        else:
            return ('AD_ASC', 6, seq)
    elif week == 7:
        return ('POST_ASC', 7, seq)
    else:
        return (None, week, seq)


def _epoch_slug(season, subseason, wknum, seq):
    """Compute lit_epoch slug from seasonal columns (see load_propers._epoch_slug)."""
    if not season:
        return None
    if not subseason:
        return season
    if seq is not None and wknum is not None:
        return f"{season}-{subseason}-{wknum:02d}-{seq}"
    if wknum and wknum > 0:
        return f"{season}-{subseason}-{wknum:02d}"
    return f"{season}-{subseason}"


def build_rows(entry):
    """
    Expand one JSON day entry into a list of row dicts for lit_part_sources.
    Multiple antiphon options become separate rows.
    """
    subseason, wknum, seq = classify_day(entry)
    season = 'PASC'
    rows = []

    for field, part_code in SERVICE_PART.items():
        for antiphon in entry.get(field, []):
            year_label = antiphon.get('year')   # e.g. "Year B"
            cycle_sun  = YEAR_CYCLE.get(year_label) if year_label else None

            rows.append({
                'lit_epoch_slug':            _epoch_slug(season, subseason, wknum, seq),
                'wkday':                     seq,
                'cycle_sun':                 cycle_sun,
                'cycle_wkday':               None,
                'service_part':              part_code,
                'original_text':             antiphon.get('latin', ''),
                'vernacular_text':           antiphon.get('english'),
                'text_src':                  antiphon.get('citation'),
                'original_lang':             'la',
                'vernacular_lang':           'en',
                'assignment_authority_code': 'MISSAL',
                'translation_source_code':   'ROMAN_MISSAL_2010_ICEL',
            })

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--drop', action='store_true',
                        help='Drop and recreate the table before loading')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build rows without touching the database')
    args = parser.parse_args()

    json_path = Path(__file__).parent / 'easter_propers.json'
    with open(json_path, encoding='utf-8') as f:
        days = json.load(f)

    all_rows = []
    for entry in days:
        all_rows.extend(build_rows(entry))

    if args.dry_run:
        print(f'DRY RUN: built {len(all_rows)} rows for lit_part_sources.')
        for r in all_rows[:5]:
            print(' ', r)
        return

    engine = _get_engine('jcost')

    from sqlalchemy import text
    with engine.begin() as conn:
        if args.drop:
            conn.execute(text('DROP TABLE IF EXISTS lit_part_sources'))
            print('Dropped existing lit_part_sources table.')

        conn.execute(text(CREATE_BOOKS))
        conn.execute(text(CREATE_TABLE))
        print('Table lit_part_sources ready.')

        if all_rows:
            conn.execute(
                text("""
                    INSERT INTO lit_part_sources
                        (lit_epoch_slug, wkday, cycle_sun, cycle_wkday,
                         service_part, original_text, vernacular_text, text_src,
                         original_lang, vernacular_lang,
                         assignment_authority_code, translation_source_code)
                    VALUES
                        (:lit_epoch_slug, :wkday, :cycle_sun, :cycle_wkday,
                         :service_part, :original_text, :vernacular_text, :text_src,
                         :original_lang, :vernacular_lang,
                         :assignment_authority_code, :translation_source_code)
                """),
                all_rows
            )
            print(f'Inserted {len(all_rows)} rows.')

        # Quick verification
        count = conn.execute(text('SELECT COUNT(*) FROM lit_part_sources')).scalar()
        print(f'Total rows in lit_part_sources: {count}')

        sample = conn.execute(text(
            "SELECT lit_epoch_slug, service_part, text_src "
            "FROM lit_part_sources ORDER BY lit_epoch_slug, service_part LIMIT 10"
        )).fetchall()
        print('\nSample rows:')
        for r in sample:
            print(' ', dict(r._mapping))


if __name__ == '__main__':
    main()
