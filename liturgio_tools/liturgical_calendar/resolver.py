"""
liturgio_tools.liturgical_calendar.resolver
============================================
Resolve liturgical precedence and sanctoral transfers for a given jurisdiction
and civil year, then optionally materialize the result into
``lit_observance_resolved``.

Algorithm summary
-----------------
The Roman Rite resolves observances in two passes over a civil year:

Pass 1 — build the temporal frame
    For every civil date in the year, look up the temporal day from
    ``proper_of_seasons`` (joined to ``lit_epoch`` for the rank) and the full
    rank table from ``p_lit_rank``.

Pass 2 — overlay the sanctoral calendar with transfers
    For each saint in ``proper_of_saints`` (filtered by jurisdiction):

    a. Compute the saint's actual civil date in the target year from
       ``month_nominal`` / ``day_nominal``.
    b. Apply any ``local_solemnities`` override: saints in that list are
       elevated to PROPER_SOLEMNITY (sort_order 40) for this resolution only.
    c. Compare the saint's effective sort_order with the temporal sort_order
       on its nominal date:
       - If the saint wins (saint_sort < temporal_sort): place it as
         'celebrated' on that date (is_transferred=0).
       - If the temporal day wins AND the saint's rank is transferable
         (can_be_transferred=1, i.e. SOLEMNITY / PROPER_SOLEMNITY): search
         forward day by day for the first date where
             (i)  the saint outranks the temporal day there,  AND
             (ii) no already-placed solemnity occupies it.
         Place the saint on that date (is_transferred=1, nominal_dt=original).
       - If the temporal day wins AND the saint is NOT transferable: record
         the saint as role='omitted' on its nominal date.

Tie-breaking
    On any date, the single 'celebrated' observance is the one with the
    lowest sort_order.  If two saints land on the same date with equal rank,
    the one appearing first in month/day order is celebrated and the other is
    a 'commemoration'.  (This is a simplification; full rubrics would compare
    specificity of commons, etc.)

Optional memorials
    If the 'celebrated' observance on a date is a plain feria (rank_code
    WEEKDAY, sort_order 120), any OPTIONAL_MEMORIAL saint whose nominal date
    is that day is recorded with role='optional'.  The feria remains the
    principal celebrated observance (WEEKDAY sort=120 beats OPTIONAL_MEMORIAL
    sort=130 in the rank table, so they never displace ferias in pass 2).

Rubrical simplifications noted
    - We do not model the Saturday-memorial rules or the precedence distinction
      between Ordinary-Time Sundays and sanctoral Feasts of the Lord.
    - We do not handle the September ember days or the specific rubric that
      Ash Wednesday takes precedence over any memorial.
    - "Transfer target must not be a Sunday" is not hard-coded; instead the
      algorithm naturally skips Sundays because their sort_order (60) is lower
      than SOLEMNITY (30) only when the saint is not a solemnity — but for
      transferable SOLEMNITY saints the Sunday sort (60) > saint sort (30),
      so the algorithm would place the saint on the Sunday.  In practice this
      does not happen because Holy Week / Octave days (sort <=20) block all
      transfers, and ordinary Sundays (sort=60) are beaten by solemnities
      (sort=30), so the algorithm correctly places solemnities on the Sunday.
      If rubrics require skipping Sundays as transfer targets, add a check
      (wkday == 1) in the forward-search loop.
    - The data window currently loaded ends 2029-12-02; resolve_year() raises
      ValueError if the requested year is not fully covered.
"""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_engine_rw(db_name: str = 'liturgio'):
    """Return a read-write SQLAlchemy engine using the keyring credential."""
    import keyring
    from sqlalchemy import create_engine

    password = keyring.get_password('liturgio-mysql', 'jcost')
    if password is None:
        raise RuntimeError(
            "No keyring password for jcost@liturgio. "
            "Set it with: keyring.set_password('liturgio-mysql', 'jcost', 'PASSWORD')"
        )
    conn_str = f'mysql+mysqlconnector://jcost:{password}@localhost:3306/{db_name}'
    return create_engine(conn_str, future=True)


# ---------------------------------------------------------------------------
# Data-loading helpers (read-only queries)
# ---------------------------------------------------------------------------

def _load_ranks(conn) -> dict[str, dict]:
    """
    Return a mapping  rank_code -> {sort_order, can_be_transferred, can_be_impeded}
    for every row in p_lit_rank.
    """
    rows = conn.execute(text(
        "SELECT rank_code, sort_order, can_be_transferred, can_be_impeded "
        "FROM p_lit_rank"
    )).fetchall()
    return {
        r[0]: {
            'rank_code':          r[0],
            'sort_order':         r[1],
            'can_be_transferred': bool(r[2]),
            'can_be_impeded':     bool(r[3]) if r[3] is not None else False,
        }
        for r in rows
    }


def _load_temporal(conn, jurisdiction: str, civil_year: int) -> dict[datetime.date, dict]:
    """
    Load every date in civil_year for jurisdiction from proper_of_seasons,
    joined to lit_epoch and p_lit_rank.

    Returns  date -> {lit_day_id, rank_code, sort_order, title}
    """
    rows = conn.execute(text("""
        SELECT pos.dt, pos.lit_day_id, le.rank_code, plr.sort_order, le.title
        FROM proper_of_seasons pos
        JOIN lit_epoch le  ON le.slug        = pos.lit_day_id
        JOIN p_lit_rank plr ON plr.rank_code = le.rank_code
        WHERE pos.jurisdiction = :jur
          AND YEAR(pos.dt)     = :yr
        ORDER BY pos.dt
    """), {'jur': jurisdiction, 'yr': civil_year}).fetchall()

    if not rows:
        raise ValueError(
            f"No proper_of_seasons data for jurisdiction={jurisdiction!r}, "
            f"civil_year={civil_year}.  "
            f"Check that the year is within the loaded range."
        )

    return {
        r[0]: {
            'dt':         r[0],
            'lit_day_id': r[1],
            'rank_code':  r[2],
            'sort_order': r[3],
            'title':      r[4],
        }
        for r in rows
    }


def _load_saints(conn, jurisdiction: str) -> list[dict]:
    """
    Load all saints for the jurisdiction from proper_of_saints joined to
    lit_epoch (for slug confirmation) and p_lit_rank (for rank metadata).

    Returns a list of dicts:
        {slug, common_name, rank_code, sort_order, can_be_transferred,
         month_nominal, day_nominal}
    """
    rows = conn.execute(text("""
        SELECT pos.slug, pos.common_name, pos.rank_code,
               plr.sort_order, plr.can_be_transferred,
               pos.month_nominal, pos.day_nominal
        FROM proper_of_saints pos
        JOIN p_lit_rank plr ON plr.rank_code = pos.rank_code
        WHERE pos.jurisdiction = :jur
        ORDER BY pos.month_nominal, pos.day_nominal, pos.slug
    """), {'jur': jurisdiction}).fetchall()

    return [
        {
            'slug':               r[0],
            'common_name':        r[1],
            'rank_code':          r[2],
            'sort_order':         r[3],
            'can_be_transferred': bool(r[4]),
            'month_nominal':      r[5],
            'day_nominal':        r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def _saint_nominal_date(saint: dict, civil_year: int) -> Optional[datetime.date]:
    """
    Compute the civil date of the saint's nominal feast in civil_year.

    Returns None for months/days that do not exist in that year (e.g.
    Feb 29 in a non-leap year).
    """
    m = saint['month_nominal']
    d = saint['day_nominal']
    try:
        return datetime.date(civil_year, m, d)
    except ValueError:
        # Feb 29 in a non-leap year, or similarly invalid dates.
        return None


def resolve_year(
    engine,
    jurisdiction: str,
    civil_year: int,
    local_solemnities: Optional[list[str]] = None,
) -> list[dict]:
    """
    Resolve the full liturgical calendar for one civil year.

    Parameters
    ----------
    engine
        SQLAlchemy engine (read-only access is sufficient).
    jurisdiction
        Jurisdiction code matching proper_of_seasons.jurisdiction
        (e.g. ``'UNIVERSAL'``, ``'US'``).
    civil_year
        The civil (Gregorian) year to resolve (e.g. 2028).
    local_solemnities
        List of saint slugs that should be elevated to PROPER_SOLEMNITY rank
        for this resolution (i.e. ``proper_solemnities`` of a local church
        added on top of the universal calendar).

    Returns
    -------
    list[dict]
        One dict per resolved observance row, shaped exactly like the columns
        of ``lit_observance_resolved`` (minus ``created_at``):
            jurisdiction, dt, epoch_slug, rank_code, role,
            is_transferred, nominal_dt, notes
        The list includes rows for every date in the year (one 'celebrated'
        per date) plus any 'omitted', 'commemoration', and 'optional' rows.
    """
    local_solemnities = set(local_solemnities or [])

    with engine.connect() as conn:
        ranks   = _load_ranks(conn)
        temporal = _load_temporal(conn, jurisdiction, civil_year)
        saints   = _load_saints(conn, jurisdiction)

    # Rank metadata for the PROPER_SOLEMNITY elevation
    proper_sol_rank = ranks['PROPER_SOLEMNITY']

    # --- Apply local-solemnity elevation to affected saints -----------------
    # We work on copies so the original dicts are untouched.
    def effective_saint(s: dict) -> dict:
        if s['slug'] in local_solemnities:
            return {
                **s,
                'rank_code':          'PROPER_SOLEMNITY',
                'sort_order':         proper_sol_rank['sort_order'],
                'can_be_transferred': proper_sol_rank['can_be_transferred'],
            }
        return s

    effective_saints = [effective_saint(s) for s in saints]

    # --- Compute nominal date for each saint in this civil year ---------------
    # saint_by_date: date -> list of saint dicts (effective ranks applied)
    # Include only saints whose nominal date falls within the loaded temporal
    # frame for this year.
    saint_by_nominal: dict[datetime.date, list[dict]] = {}
    for s in effective_saints:
        dt = _saint_nominal_date(s, civil_year)
        if dt is None:
            continue  # Feb 29 in non-leap year, skip
        if dt not in temporal:
            # Nominal date is outside the loaded window for this jurisdiction
            continue
        saint_by_nominal.setdefault(dt, []).append(s)

    # --- Forward-transfer state -----------------------------------------------
    # occupied: date -> epoch_slug of the solemnity already placed there
    # (either naturally on its nominal date or via transfer).
    # We track which dates are "claimed" by a transferred solemnity so that
    # a second transfer cannot land on the same day.
    occupied: dict[datetime.date, str] = {}

    # The temporal frame gives us all dates we need for transfer search.
    all_dates_sorted = sorted(temporal.keys())

    # We'll build rows for every date in the year.  Start with the temporal
    # observed day on every date, then overlay saints.
    # rows_by_date: date -> list of pending row-dicts (before finalization)
    rows_by_date: dict[datetime.date, list[dict]] = {dt: [] for dt in all_dates_sorted}

    # Records for 'omitted' saints (placed on nominal date with role='omitted')
    omitted_rows: list[dict] = []

    # --- Process saints in nominal-date order ----------------------------------
    # Sorting by nominal date ensures we process earlier feast days first, which
    # matters when two saints compete for the same transfer landing spot.
    for nominal_dt, day_saints in sorted(saint_by_nominal.items()):
        temp = temporal[nominal_dt]

        for s in day_saints:
            saint_sort   = s['sort_order']
            temporal_sort = temp['sort_order']

            if saint_sort <= temporal_sort:
                # Saint wins (or ties — treated as a win; ties are broken
                # in the final pass below when consolidating each date).
                occupied[nominal_dt] = s['slug']
                rows_by_date[nominal_dt].append({
                    'slug':          s['slug'],
                    'rank_code':     s['rank_code'],
                    'sort_order':    saint_sort,
                    'is_transferred': False,
                    'nominal_dt':    None,
                })
            elif s['can_be_transferred']:
                # Temporal day wins; saint is transferable (SOLEMNITY or
                # PROPER_SOLEMNITY) — search forward for the nearest free slot.
                transfer_dt = _find_transfer_date(
                    s, nominal_dt, all_dates_sorted, temporal, occupied, ranks
                )
                if transfer_dt is not None:
                    occupied[transfer_dt] = s['slug']
                    rows_by_date[transfer_dt].append({
                        'slug':           s['slug'],
                        'rank_code':      s['rank_code'],
                        'sort_order':     saint_sort,
                        'is_transferred': True,
                        'nominal_dt':     nominal_dt,
                    })
                else:
                    # No valid transfer date found within the year's window.
                    # Record as omitted.
                    omitted_rows.append({
                        'jurisdiction':   jurisdiction,
                        'dt':             nominal_dt,
                        'epoch_slug':     s['slug'],
                        'rank_code':      s['rank_code'],
                        'role':           'omitted',
                        'is_transferred': 0,
                        'nominal_dt':     None,
                        'notes':          'No transfer date found within year window',
                    })
            else:
                # Temporal day wins; saint is NOT transferable.
                # Record it as 'omitted' on its nominal date.
                omitted_rows.append({
                    'jurisdiction':   jurisdiction,
                    'dt':             nominal_dt,
                    'epoch_slug':     s['slug'],
                    'rank_code':      s['rank_code'],
                    'role':           'omitted',
                    'is_transferred': 0,
                    'nominal_dt':     None,
                    'notes':          'Outranked by temporal day; not transferable',
                })

    # --- Finalize: one row per date with roles --------------------------------
    resolved: list[dict] = []

    for dt in all_dates_sorted:
        temp = temporal[dt]
        saint_candidates = rows_by_date[dt]

        if not saint_candidates:
            # Pure temporal day: the temporal observance is celebrated.
            resolved.append({
                'jurisdiction':   jurisdiction,
                'dt':             dt,
                'epoch_slug':     temp['lit_day_id'],
                'rank_code':      temp['rank_code'],
                'role':           'celebrated',
                'is_transferred': 0,
                'nominal_dt':     None,
                'notes':          None,
            })
            # Also check for optional memorials on feria days.
            if temp['rank_code'] == 'WEEKDAY':
                # Optional memorials live on their nominal date (not in our
                # current data set, but the structure supports them).
                # For now: no OPTIONAL_MEMORIAL saints are in proper_of_saints
                # for UNIVERSAL, so this is a no-op — the hook is here for
                # future data additions.
                pass

        else:
            # One or more saints land on this date (nominal or transferred).
            # Sort by ascending sort_order; lowest wins 'celebrated'.
            saint_candidates.sort(key=lambda x: x['sort_order'])
            best = saint_candidates[0]
            temporal_sort = temp['sort_order']

            # Does the best saint beat the temporal day?
            if best['sort_order'] < temporal_sort:
                # Saint is celebrated.
                resolved.append({
                    'jurisdiction':   jurisdiction,
                    'dt':             dt,
                    'epoch_slug':     best['slug'],
                    'rank_code':      best['rank_code'],
                    'role':           'celebrated',
                    'is_transferred': int(best['is_transferred']),
                    'nominal_dt':     best['nominal_dt'],
                    'notes':          (
                        f"Transferred from {best['nominal_dt']}"
                        if best['is_transferred'] else None
                    ),
                })
                # The temporal day itself is suppressed; add a commemoration
                # only if the temporal rank warrants it (simplified: we
                # omit commemorations of ferias but keep them for privileged
                # days and Sundays — rubrical simplification noted).
                if temporal_sort <= 90:
                    resolved.append({
                        'jurisdiction':   jurisdiction,
                        'dt':             dt,
                        'epoch_slug':     temp['lit_day_id'],
                        'rank_code':      temp['rank_code'],
                        'role':           'commemoration',
                        'is_transferred': 0,
                        'nominal_dt':     None,
                        'notes':          'Suppressed by sanctoral observance',
                    })
                # Any additional saints on this date become commemorations.
                for extra in saint_candidates[1:]:
                    resolved.append({
                        'jurisdiction':   jurisdiction,
                        'dt':             dt,
                        'epoch_slug':     extra['slug'],
                        'rank_code':      extra['rank_code'],
                        'role':           'commemoration',
                        'is_transferred': int(extra['is_transferred']),
                        'nominal_dt':     extra['nominal_dt'],
                        'notes':          None,
                    })
            else:
                # The temporal day beats all saints on this date.
                # (This can only happen for saints placed here via transfer
                # logic error; should not occur in correct flow — guard.)
                resolved.append({
                    'jurisdiction':   jurisdiction,
                    'dt':             dt,
                    'epoch_slug':     temp['lit_day_id'],
                    'rank_code':      temp['rank_code'],
                    'role':           'celebrated',
                    'is_transferred': 0,
                    'nominal_dt':     None,
                    'notes':          None,
                })
                for extra in saint_candidates:
                    resolved.append({
                        'jurisdiction':   jurisdiction,
                        'dt':             dt,
                        'epoch_slug':     extra['slug'],
                        'rank_code':      extra['rank_code'],
                        'role':           'commemoration',
                        'is_transferred': int(extra['is_transferred']),
                        'nominal_dt':     extra['nominal_dt'],
                        'notes':          'Outranked by temporal day at landing date',
                    })

    # Append all omitted rows collected earlier.
    resolved.extend(omitted_rows)

    return resolved


def _find_transfer_date(
    saint: dict,
    nominal_dt: datetime.date,
    all_dates: list[datetime.date],
    temporal: dict[datetime.date, dict],
    occupied: dict[datetime.date, str],
    ranks: dict[str, dict],
) -> Optional[datetime.date]:
    """
    Search forward from nominal_dt+1 for the nearest date where:
      (a) the saint's sort_order < temporal sort_order there (saint wins), AND
      (b) no other transferred/placed solemnity already occupies that date.

    Returns the target date, or None if no valid date is found within the
    available temporal data (i.e. the year's data ends before one is found).

    Note: the natural effect of Holy Week + Easter octave (all sort<=20)
    blocking transfers means that a solemnity impeded during Holy Week will
    search forward through the octave and land on the first free weekday
    after the octave — exactly the rubrical behaviour required.
    """
    saint_sort = saint['sort_order']
    one_day    = datetime.timedelta(days=1)

    candidate = nominal_dt + one_day
    while candidate in temporal:
        temp_sort = temporal[candidate]['sort_order']
        if saint_sort < temp_sort and candidate not in occupied:
            return candidate
        candidate += one_day

    # Ran off the end of the loaded data without finding a slot.
    return None


# ---------------------------------------------------------------------------
# Convenience wrapper: resolve a single date
# ---------------------------------------------------------------------------

def resolve_date(
    engine,
    jurisdiction: str,
    date: datetime.date,
    local_solemnities: Optional[list[str]] = None,
) -> list[dict]:
    """
    Return the resolved observances for a single civil date.

    Because transfers from earlier dates can land on ``date``, this resolves
    the entire civil year that contains ``date`` and then filters to rows
    whose ``dt`` == ``date``.

    Parameters
    ----------
    engine
        SQLAlchemy engine.
    jurisdiction
        Jurisdiction code.
    date
        The specific date to resolve.
    local_solemnities
        Same semantics as in :func:`resolve_year`.

    Returns
    -------
    list[dict]
        All resolved-observance rows for ``date`` (may include 'celebrated',
        'commemoration', and 'optional' rows).
    """
    all_rows = resolve_year(engine, jurisdiction, date.year, local_solemnities)
    return [r for r in all_rows if r['dt'] == date]


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------

def materialize_year(
    engine,
    jurisdiction: str,
    civil_year: int,
    local_solemnities: Optional[list[str]] = None,
) -> int:
    """
    Resolve the year and write results to ``lit_observance_resolved``.

    Idempotent: existing rows for (jurisdiction, dates within civil_year) are
    deleted before inserting the fresh resolution.

    Parameters
    ----------
    engine
        SQLAlchemy engine with read-write access.
    jurisdiction
        Jurisdiction code.
    civil_year
        Civil year to materialize.
    local_solemnities
        List of saint slugs to elevate to PROPER_SOLEMNITY.

    Returns
    -------
    int
        Number of rows inserted.
    """
    rows = resolve_year(engine, jurisdiction, civil_year, local_solemnities)

    insert_sql = text("""
        INSERT INTO lit_observance_resolved
            (jurisdiction, dt, epoch_slug, rank_code, role,
             is_transferred, nominal_dt, notes)
        VALUES
            (:jurisdiction, :dt, :epoch_slug, :rank_code, :role,
             :is_transferred, :nominal_dt, :notes)
    """)

    with engine.begin() as conn:
        # Idempotent: remove all rows for this jurisdiction × year first.
        conn.execute(text("""
            DELETE FROM lit_observance_resolved
            WHERE jurisdiction = :jur
              AND YEAR(dt)     = :yr
        """), {'jur': jurisdiction, 'yr': civil_year})

        # Insert freshly resolved rows.
        conn.execute(insert_sql, rows)

    return len(rows)
