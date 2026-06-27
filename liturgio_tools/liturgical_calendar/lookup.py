"""
liturgio_tools.liturgical_calendar.lookup
=========================================
Day-parts lookup: "what chant is assigned on civil date X?"

This module is the read side that sits on top of two pieces:

  1. The temporal calendar (``proper_of_seasons`` + ``lit_epoch`` /
     ``lit_epoch_tree``), which gives each civil date a liturgical day and its
     ancestor chain (week -> subseason -> season).

  2. The calendar **resolver** (``lit_observance_resolved``, materialized by
     :mod:`liturgio_tools.liturgical_calendar.resolver`), which overlays the
     sanctoral calendar with precedence and transfers and records the single
     CELEBRATED observance for each date.

:func:`parts_for_date` runs ``sql/query-daily-mass-parts.sql``, whose ``ctx``
CTE now derives the observed epoch from the resolver's celebrated observance
(``COALESCE(resolver.epoch_slug, temporal.lit_day_id)``). As a result the
lookup transparently follows saints and transfers:

  - On a date where a solemnity is celebrated in place (e.g. the Assumption on
    a weekday), assignments keyed to the saint's epoch slug surface instead of
    the temporal feria's.
  - On a date where a solemnity was transferred IN (e.g. St Joseph moved out of
    a Lenten Sunday), the saint's assignments surface on the *transferred* date,
    and not on the now-impeded nominal date.

Saint epochs are currently tree roots, so they resolve at depth 0 (direct
assignments only); inheriting from a saint's Common is future work.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Union

from sqlalchemy import text

# Path to the canonical lookup query, resolved relative to the repo layout:
#   liturgio-tools/liturgio_tools/liturgical_calendar/lookup.py
#   liturgio-tools/sql/query-daily-mass-parts.sql
_SQL_PATH = (
    Path(__file__).resolve().parents[2] / "sql" / "query-daily-mass-parts.sql"
)


def _load_query() -> str:
    """Read the day-parts SQL from disk (single source of truth)."""
    return _SQL_PATH.read_text(encoding="utf-8")


def parts_for_date(
    engine,
    jurisdiction: str,
    dt: Union[datetime.date, str],
    service_code: str,
    winner_only: bool = True,
) -> list[dict]:
    """
    Return the assigned chant parts for a single civil date.

    The lookup resolves ``(dt, jurisdiction, service_code)`` to assigned chants
    using the day-parts query, which derives the observed epoch from the
    calendar resolver (``lit_observance_resolved``). It therefore accounts for
    saints and transfers, not just the bare temporal day: if a solemnity is
    celebrated (in place or transferred in) on ``dt``, assignments keyed to that
    saint's epoch slug are returned.

    Parameters
    ----------
    engine
        SQLAlchemy engine (read-only access is sufficient).
    jurisdiction
        Jurisdiction code (e.g. ``'US'``, ``'UNIVERSAL'``). The query prefers
        rows for this jurisdiction and falls back to ``'UNIVERSAL'``.
    dt
        The civil date to look up. Accepts a ``datetime.date`` or an
        ISO ``'YYYY-MM-DD'`` string.
    service_code
        Service whose parts to return (e.g. ``'MASS'``).
    winner_only
        When True (default), return only the winning assignment per part
        (the ``rn = 1`` row). When False, return every candidate row, ordered
        by ``display_order`` then ``rn`` — useful for debugging precedence.

    Returns
    -------
    list[dict]
        One dict per result row, keyed by the query's output columns:
        ``title, part_id, service_code, part_code, display_order,
        text_id, chant_uuid, chant_group_id, assignment_authority_code,
        assignment_jurisdiction, notes``. (The internal ``rn`` ranking column is included
        only when ``winner_only=False`` is requested via the SQL; here we filter
        in Python so the public shape is stable.)

    Notes
    -----
    For the lookup to reflect saints/transfers, the resolver must have been
    materialized for the year containing ``dt`` (see
    :func:`liturgio_tools.liturgical_calendar.resolver.materialize_year`). When
    no resolver row exists for the date, the query falls back to the temporal
    day, so the lookup degrades gracefully to pre-resolver behaviour.
    """
    if isinstance(dt, str):
        dt = datetime.date.fromisoformat(dt)

    base_sql = _load_query()

    # The .sql file ships with `-- WHERE rn = 1` commented out so the raw query
    # returns all candidates. We emit the winner-only filter by wrapping the
    # full query as a subselect and filtering on rn, which keeps the .sql file
    # as the single source of truth for the resolution logic.
    if winner_only:
        # Strip the trailing semicolon (if any) so we can wrap it.
        wrapped = base_sql.rstrip().rstrip(";")
        sql = (
            "SELECT * FROM (\n"
            + wrapped
            + "\n) AS _q WHERE _q.rn = 1 ORDER BY _q.display_order"
        )
    else:
        sql = base_sql

    params = {
        "dt": dt,
        "jurisdiction": jurisdiction,
        "service_code": service_code,
    }

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        cols = result.keys()
        rows = [dict(zip(cols, r)) for r in result.fetchall()]

    # Keep the public column shape stable regardless of winner_only by dropping
    # the internal ranking column when present.
    for row in rows:
        row.pop("rn", None)

    return rows
