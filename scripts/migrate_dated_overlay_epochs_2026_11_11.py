#!/usr/bin/env python3
"""
Dec 17-24 redesign: dated overlay epochs (lit_epoch.kind='date') + weekday days.

Before: proper_of_seasons pointed Dec 17-24 at ADV-II-00-1..8 (seq = days since
Dec 16), so Advent IV had no epoch of its own (its parts sat on ADV-II wkday=1),
was ranked PRIVILEGED_WEEKDAY, and a Dec 17 Sunday lost Advent III entirely.

After:
  * Season days are weekday-based: ADV-II-01-1 = Advent IV (always, Dec 18-24),
    ADV-II-01-2..7 = the late-Advent ferias.  Dec 17 and Dec 24 share a day
    epoch when neither is a Sunday; a Dec 17 Sunday is Advent III (ADV-I-03-1).
  * Date-tied parts live on kind='date' epochs O-SAPIENTIA .. O-EMMANUEL and
    DEC-24, each with a proper_of_saints (month/day lookup) row.  get_day puts
    them in front of the temporal day's chain; when the day outranks them (a
    Sunday) only their wkday-explicit rows go in front, the rest fill in after
    the Sunday's own chain.
  * The OCO's "if this Sunday falls on Dec N ... de die currente" rubrics become
    wkday=1 xref rows on the date epoch (p34/p37 Dec 17, p46 Dec 21/23, p45
    Dec 24 invitatory + Lauds) instead of chantless option_txt rows.
  * ADV-II wkday=1 rows move to ADV-II-01-1 (wkday NULL); ADV-II wkday 2-7 and
    wkday-NULL rows stay, so Dec 17 and Dec 24 ferias draw the same parts.

lit_observance_resolved rows for the old slugs are deleted here; rematerialize
2027/2028 afterwards with the updated resolver.

Usage:  python scripts/migrate_dated_overlay_epochs_2026_11_11.py [--apply]
(default is a dry run that rolls back)
"""

import argparse
import sys

import keyring
from sqlalchemy import create_engine, text

DATE_EPOCHS = [  # (old slug, new slug, Dec day)
    ('ADV-II-00-1', 'O-SAPIENTIA', 17),
    ('ADV-II-00-2', 'O-ADONAI', 18),
    ('ADV-II-00-3', 'O-RADIX-JESSE', 19),
    ('ADV-II-00-4', 'O-CLAVIS-DAVID', 20),
    ('ADV-II-00-5', 'O-ORIENS', 21),
    ('ADV-II-00-6', 'O-REX-GENTIUM', 22),
    ('ADV-II-00-7', 'O-EMMANUEL', 23),
    ('ADV-II-00-8', 'DEC-24', 24),
]
WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
DAY_TITLES = {1: '4th Sunday of Advent',
              **{s: f'{WEEKDAYS[s - 1]} in Late Advent' for s in range(2, 8)}}


def engine():
    pw = keyring.get_password('liturgio-mysql', 'jcost')
    if pw is None:
        sys.exit('no keyring password for jcost')
    return create_engine(f'mysql+mysqlconnector://jcost:{pw}@localhost:3306/liturgio', future=True)


def one(c, sql, **p):
    rows = c.execute(text(sql), p).mappings().fetchall()
    assert len(rows) == 1, (sql, p, len(rows))
    return dict(rows[0])


def lps_cols(c):
    return [r[0] for r in c.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='lit_part_sources' AND EXTRA NOT LIKE '%GENERATED%' AND COLUMN_NAME<>'text_id' "
        "ORDER BY ORDINAL_POSITION"))]


def date_row(c, slug, service, part):
    """The OCO row a Sunday rubric points at (the date's own, wkday-generic)."""
    return one(c, """
        SELECT lps.* FROM lit_part_sources lps JOIN service_part sp USING (part_id)
        WHERE lps.lit_epoch_slug=:s AND sp.service_code=:svc AND sp.part_code=:pc
          AND lps.book='OCO' AND lps.wkday IS NULL""", s=slug, svc=service, pc=part)


def sunday_xref(c, cols, src, title, page, note):
    """Insert a wkday=1 copy of date row `src`, cited from Sunday page `page`."""
    row = {k: src[k] for k in cols}
    row.update(wkday=1, option_txt=None, printed_page_num=page, ref_book='OCO',
               ref_page_num=int(src['ref_page_num'] or src['printed_page_num']),
               bbox=None, review_status='draft', notes=note,
               display_name=f"{title} — {(src['display_name'] or '').split(' — ', 1)[-1]}")
    c.execute(text(f"INSERT INTO lit_part_sources ({', '.join(cols)}) "
                   f"VALUES ({', '.join(':' + k for k in cols)})"), row)


def convert_to_sunday_xref(c, text_id, src, title):
    """Turn an 'if this Sunday falls on Dec N' option_txt row on the Sunday into
    a wkday=1 xref on the date epoch (keeping its chant, else the date row's)."""
    n = c.execute(text("""
        UPDATE lit_part_sources
        SET lit_epoch_slug=:slug, wkday=1, option_txt=NULL,
            chant_uuid=COALESCE(chant_uuid, :cu), chant_group_id=COALESCE(chant_group_id, :cg),
            display_name=:dn,
            notes=CONCAT(notes, ' | 2026-11-11: modelled as ', :slug, ' wkday=1 (was option_txt on the Sunday)')
        WHERE text_id=:tid AND option_txt LIKE 'Si h%dominica%decembris'"""), dict(
        slug=src['lit_epoch_slug'], cu=src['chant_uuid'], cg=src['chant_group_id'], tid=text_id,
        dn=f"{title} — {(src['display_name'] or '').split(' — ', 1)[-1]}")).rowcount
    assert n == 1, ('convert', text_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    with engine().connect() as c:
        tx = c.begin()
        assert not c.execute(text(
            "SELECT 1 FROM lit_epoch WHERE slug LIKE 'ADV-II-01%' OR slug IN :s"
        ).bindparams(__import__('sqlalchemy').bindparam('s', expanding=True)),
            {'s': [n for _, n, _ in DATE_EPOCHS]}).fetchone(), 'already migrated'

        old = {r['slug']: r for r in c.execute(text(
            "SELECT * FROM lit_epoch WHERE slug LIKE 'ADV-II-00%'")).mappings()}
        assert len(old) == 9, sorted(old)

        # 1. epochs -------------------------------------------------------
        c.execute(text("""INSERT INTO lit_epoch (slug, kind, title, season, subseason, wknum, sort_order)
                          VALUES ('ADV-II-01', 'week', 'O Antiphons', 'ADV', 'II', 1, :so)"""),
                  {'so': old['ADV-II-00']['sort_order']})
        for seq in range(1, 8):
            c.execute(text("""
                INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, sort_order)
                VALUES (:slug, 'day', :t, :rk, 'ADV', 'II', 1, :seq, :so)"""), dict(
                slug=f'ADV-II-01-{seq}', t=DAY_TITLES[seq], seq=seq,
                rk='PRINCIPAL_TEMPORAL' if seq == 1 else 'PRIVILEGED_WEEKDAY',
                so=old['ADV-II-00']['sort_order'] + seq - 1))
            c.execute(text("INSERT INTO lit_epoch_tree VALUES ('ADV-II-01', :c)"), {'c': f'ADV-II-01-{seq}'})
        c.execute(text("INSERT INTO lit_epoch_tree VALUES ('ADV-II', 'ADV-II-01')"))
        for o, n, day in DATE_EPOCHS:
            c.execute(text("""
                INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, sort_order, notes)
                VALUES (:n, 'date', :t, 'PRIVILEGED_WEEKDAY', 'ADV', 'II', :so,
                        'Dated overlay on the temporal day (2026-11-11): parts tied to the civil date, not the weekday.')"""),
                      dict(n=n, t=old[o]['title'], so=old[o]['sort_order']))
            c.execute(text("INSERT INTO lit_epoch_tree VALUES ('ADV-II', :n)"), {'n': n})
            c.execute(text("""
                INSERT INTO proper_of_saints (jurisdiction, slug, common_name, rank_code, month_nominal, day_nominal, notes)
                VALUES ('UNIVERSAL', :n, :t, 'PRIVILEGED_WEEKDAY', 12, :d, 'lit_epoch.kind=date: dated overlay, not an observance')"""),
                      dict(n=n, t=old[o]['title'], d=day))

        # 2. calendar -----------------------------------------------------
        n = c.execute(text("""
            UPDATE proper_of_seasons ps
            JOIN lit_epoch le ON le.kind='day' AND le.season='ADV'
             AND le.subseason = IF(DAY(ps.dt)=17 AND ps.wkday=1, 'I', 'II')
             AND le.wknum     = IF(DAY(ps.dt)=17 AND ps.wkday=1, 3, 1)
             AND le.seq       = ps.wkday
            SET ps.lit_day_id = le.slug
            WHERE MONTH(ps.dt)=12 AND DAY(ps.dt) BETWEEN 17 AND 24""")).rowcount
        print('proper_of_seasons rows updated:', n)
        assert not c.execute(text(
            "SELECT 1 FROM proper_of_seasons WHERE lit_day_id LIKE 'ADV-II-00%'")).fetchone()

        # 3. lit_part_sources ----------------------------------------------
        for o, n_, _ in DATE_EPOCHS:
            k = c.execute(text("UPDATE lit_part_sources SET lit_epoch_slug=:n WHERE lit_epoch_slug=:o"),
                          {'n': n_, 'o': o}).rowcount
            print(f'  {o} -> {n_}: {k} rows')
        # p45: "Si haec dominica incidat die 24 decembris, sumitur ... Hodie scietis, de eodem die"
        dec24_inv = date_row(c, 'DEC-24', 'INVITATORY', 'invan')
        convert_to_sunday_xref(c, 11860, dec24_inv, 'December 24')
        k = c.execute(text("""
            UPDATE lit_part_sources
            SET lit_epoch_slug='ADV-II-01-1', wkday=NULL,
                display_name = IF(display_name LIKE 'O Antiphons — %',
                                  CONCAT('4th Sunday of Advent — ', SUBSTRING(display_name, 15)), display_name)
            WHERE lit_epoch_slug='ADV-II' AND wkday=1""")).rowcount
        print('  ADV-II wkday=1 -> ADV-II-01-1:', k, 'rows')

        # 4. Sunday rubrics -> wkday=1 xrefs on the date epoch -------------
        cols = lps_cols(c)
        # p34 / p37: Advent III on Dec 17 takes Scitote (Benedictus) and O Sapientia
        convert_to_sunday_xref(c, 11945, date_row(c, 'O-SAPIENTIA', 'LAUDS', 'antB'), 'O, Holy Wisdom')
        convert_to_sunday_xref(c, 11790, date_row(c, 'O-SAPIENTIA', 'VESPERS', 'antM'), 'O, Holy Wisdom')
        # p46: "Die 21 vel 23 decembris antiphona de die currente, ut infra. Secus cantatur:"
        for slug, title in (('O-ORIENS', 'O, Rising Sun'), ('O-EMMANUEL', 'O, Emmanuel')):
            src = date_row(c, slug, 'LAUDS', 'antB')
            sunday_xref(c, cols, src, title, '46',
                        'OCO p46: Die 21 vel 23 decembris antiphona de die currente, ut infra. Secus cantatur: '
                        f'| 2026-11-11: Advent IV on this date takes the date\'s Benedictus antiphon (p{src["printed_page_num"]})')
        # p45: "Si haec dominica incidat die 24 decembris, omnia sumuntur de eodem die." (Lauds)
        for part in ('ant1', 'ant2', 'ant3', 'antB', 'resp'):
            src = date_row(c, 'DEC-24', 'LAUDS', part)
            sunday_xref(c, cols, src, 'December 24', '45',
                        'OCO p45: Si hæc dominica incidat die 24 decembris, omnia sumuntur de eodem die. '
                        f'| 2026-11-11: Advent IV on Dec 24 takes Lauds from Dec 24 (p{src["printed_page_num"]})')

        # 5. resolver output, title overrides, generator source ------------
        k = c.execute(text("DELETE FROM lit_observance_resolved WHERE epoch_slug LIKE 'ADV-II-00%'")).rowcount
        print('lit_observance_resolved rows deleted (rematerialize 2027/2028):', k)
        c.execute(text("DELETE FROM p_liturgical_day_slug_overrides WHERE lit_day_id LIKE 'ADV-II-00-%'"))
        for seq, t in DAY_TITLES.items():
            c.execute(text("INSERT INTO p_liturgical_day_slug_overrides (title_ovr, lit_day_id) "
                           "VALUES (:t, :d)"), {'t': t, 'd': f'ADV-II-01-{seq}'})
        c.execute(text("UPDATE p_liturgical_subseasons SET minwk=1, maxwk=1, minseq=1, maxseq=7 "
                       "WHERE season='ADV' AND subseason='II'"))

        # 6. drop the old nodes (tree edges cascade) -----------------------
        left = c.execute(text(
            "SELECT COUNT(*) FROM lit_part_sources WHERE lit_epoch_slug LIKE 'ADV-II-00%'")).scalar()
        assert left == 0, left
        c.execute(text("DELETE FROM lit_epoch WHERE slug LIKE 'ADV-II-00-%'"))
        c.execute(text("DELETE FROM lit_epoch WHERE slug = 'ADV-II-00'"))

        for r in c.execute(text("""
                SELECT lit_epoch_slug, wkday, COUNT(*) FROM lit_part_sources
                WHERE lit_epoch_slug LIKE 'ADV-II%' OR lit_epoch_slug IN ('O-SAPIENTIA','O-ADONAI','O-RADIX-JESSE',
                      'O-CLAVIS-DAVID','O-ORIENS','O-REX-GENTIUM','O-EMMANUEL','DEC-24')
                GROUP BY 1, 2 ORDER BY 1, 2""")):
            print('   ', tuple(r))
        if args.apply:
            tx.commit()
            print('APPLIED')
        else:
            tx.rollback()
            print('dry run: rolled back (use --apply)')


if __name__ == '__main__':
    main()
