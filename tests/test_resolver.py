"""
tests/test_resolver.py
======================
Tests for liturgio_tools.liturgical_calendar.resolver.

Run with:
    C:\\Users\\johna\\liturgio\\.venv\\Scripts\\python.exe -m pytest tests/test_resolver.py -v
    -- or --
    C:\\Users\\johna\\liturgio\\.venv\\Scripts\\python.exe tests/test_resolver.py

All tests operate against the live liturgio database.

Date range in proper_of_seasons: 2025-11-30 to 2029-12-02 (UNIVERSAL).

Test scenarios
--------------
1. St. Joseph transfer (2028)
     Mar 19 2028 = 3rd Sunday of Lent (PRINCIPAL_TEMPORAL sort=20).
     St Joseph is SOLEMNITY (sort=30) -> outranked -> transfer forward.
     First free day: Mar 20 2028 (Monday of 3rd week of Lent, PRIVILEGED_WEEKDAY sort=90).
     Assert: St Joseph is NOT celebrated Mar 19; IS celebrated Mar 20 (is_transferred=1).

2. Annunciation transfer (2027)
     Mar 25 2027 = Holy Thursday (PRINCIPAL_TEMPORAL sort=20).
     Annunciation is SOLEMNITY (sort=30) -> outranked -> transfer forward.
     Holy Week + Easter Octave block until Apr 5 (WEEKDAY sort=120 > 30).
     Assert: Annunciation not celebrated Mar 25; IS celebrated Apr 5 (is_transferred=1).

3. St. Mark as local solemnity vs. feast (2027)
     Apr 25 2027 = 5th Sunday of Easter (PRINCIPAL_TEMPORAL sort=20).
     a) As FEAST (sort=70): outranked, NOT transferable -> role='omitted'.
     b) As PROPER_SOLEMNITY (sort=40, via local_solemnities=['st-mark']):
        still outranked by PRINCIPAL_TEMPORAL (20 < 40) -> transferable -> transfers
        to first free day with temporal sort > 40. Next day Apr 26 (WEEKDAY sort=120).

4. No-collision sanity (assumption, 2026)
     Aug 15 2026 = check what temporal day it is.
     Assumption is SOLEMNITY (sort=30). If it wins on nominal date, assert
     is_transferred=0 and role='celebrated' on Aug 15.

5. Materialize round-trip (2028)
     Call materialize_year(engine, 'UNIVERSAL', 2028) and confirm rows appear
     in lit_observance_resolved matching the resolve_year() output.
"""

import datetime
import sys
import os

import pytest

# Ensure the liturgio_tools package is importable when run directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import keyring
from sqlalchemy import create_engine, text

from liturgio_tools.liturgical_calendar.resolver import (
    resolve_year,
    resolve_date,
    materialize_year,
)


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------

def get_engine():
    password = keyring.get_password('liturgio-mysql', 'jcost')
    if not password:
        raise RuntimeError('No keyring password for jcost@liturgio')
    return create_engine(
        f'mysql+mysqlconnector://jcost:{password}@localhost:3306/liturgio',
        future=True,
    )


@pytest.fixture(scope="session")
def engine():
    return get_engine()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def rows_for(resolved: list[dict], dt: datetime.date) -> list[dict]:
    return [r for r in resolved if r['dt'] == dt]


def celebrated_on(resolved: list[dict], dt: datetime.date) -> dict | None:
    for r in rows_for(resolved, dt):
        if r['role'] == 'celebrated':
            return r
    return None


def find_slug(resolved: list[dict], slug: str) -> list[dict]:
    return [r for r in resolved if r['epoch_slug'] == slug]


def assert_slug_celebrated_on(resolved, slug, expected_dt, is_transferred, nominal_dt=None):
    matches = [r for r in resolved if r['epoch_slug'] == slug and r['role'] == 'celebrated']
    assert matches, (
        f"FAIL: {slug!r} is not 'celebrated' anywhere in resolved output.\n"
        f"  All rows for {slug!r}: {find_slug(resolved, slug)}"
    )
    row = matches[0]
    assert row['dt'] == expected_dt, (
        f"FAIL: {slug!r} is celebrated on {row['dt']} but expected {expected_dt}.\n"
        f"  Row: {row}"
    )
    assert row['is_transferred'] == int(is_transferred), (
        f"FAIL: {slug!r} is_transferred={row['is_transferred']} but expected {int(is_transferred)}.\n"
        f"  Row: {row}"
    )
    if nominal_dt is not None:
        assert row['nominal_dt'] == nominal_dt, (
            f"FAIL: {slug!r} nominal_dt={row['nominal_dt']} but expected {nominal_dt}.\n"
            f"  Row: {row}"
        )
    return row


def assert_slug_not_celebrated_on(resolved, slug, forbidden_dt):
    for r in rows_for(resolved, forbidden_dt):
        if r['epoch_slug'] == slug and r['role'] == 'celebrated':
            raise AssertionError(
                f"FAIL: {slug!r} should NOT be 'celebrated' on {forbidden_dt} but it is.\n"
                f"  Row: {r}"
            )


# ---------------------------------------------------------------------------
# Test 1 — St. Joseph transfer, 2028
# ---------------------------------------------------------------------------

def test_st_joseph_transfer(engine):
    """
    2028: Mar 19 = 3rd Sunday of Lent (PRINCIPAL_TEMPORAL sort=20).
    St Joseph SOLEMNITY (sort=30) is outranked -> transfer to Mar 20
    (Monday of 3rd week of Lent, PRIVILEGED_WEEKDAY sort=90 > 30).
    """
    print("\n=== Test 1: St. Joseph transfer (2028) ===")
    resolved = resolve_year(engine, 'UNIVERSAL', 2028)

    nominal   = datetime.date(2028, 3, 19)
    expected  = datetime.date(2028, 3, 20)

    # St Joseph must NOT be celebrated on its nominal date.
    assert_slug_not_celebrated_on(resolved, 'st-joseph', nominal)

    # Whoever IS celebrated on Mar 19 should be the temporal Sunday.
    cel = celebrated_on(resolved, nominal)
    assert cel is not None, "FAIL: No celebrated observance on 2028-03-19"
    assert cel['epoch_slug'] != 'st-joseph', (
        f"FAIL: st-joseph is celebrated on {nominal} (should be temporal Sunday)"
    )
    print(f"  Celebrated on {nominal}: {cel['epoch_slug']!r} ({cel['rank_code']})")

    # St Joseph must be celebrated on the transfer date with is_transferred=1.
    row = assert_slug_celebrated_on(resolved, 'st-joseph', expected,
                                    is_transferred=True, nominal_dt=nominal)
    print(f"  St Joseph transferred to {expected}: PASS (is_transferred={row['is_transferred']}, "
          f"nominal_dt={row['nominal_dt']})")
    print("  PASS")


# ---------------------------------------------------------------------------
# Test 2 — Annunciation transfer, 2027
# ---------------------------------------------------------------------------

def test_annunciation_transfer(engine):
    """
    2027: Mar 25 = Holy Thursday (PRINCIPAL_TEMPORAL sort=20).
    Annunciation is SOLEMNITY (sort=30) -> outranked -> transfer.
    Holy Week (Mar 25-27) + Easter Sunday (Mar 28) + Easter Octave (Mar 29 - Apr 3)
    all have sort <= 20 + Divine Mercy Sunday Apr 4 (sort=20) block the transfer.
    First free slot: Apr 5 (WEEKDAY sort=120 > 30).
    """
    print("\n=== Test 2: Annunciation transfer (2027) ===")
    resolved = resolve_year(engine, 'UNIVERSAL', 2027)

    nominal  = datetime.date(2027, 3, 25)
    expected = datetime.date(2027, 4, 5)   # Monday of 2nd week of Easter

    assert_slug_not_celebrated_on(resolved, 'annunciation', nominal)

    cel_nom = celebrated_on(resolved, nominal)
    print(f"  Celebrated on {nominal}: {cel_nom['epoch_slug']!r} ({cel_nom['rank_code']})")

    row = assert_slug_celebrated_on(resolved, 'annunciation', expected,
                                    is_transferred=True, nominal_dt=nominal)
    print(f"  Annunciation transferred to {expected}: PASS "
          f"(is_transferred={row['is_transferred']}, nominal_dt={row['nominal_dt']})")
    print("  PASS")


# ---------------------------------------------------------------------------
# Test 3a — St. Mark omitted as FEAST (2027)
# ---------------------------------------------------------------------------

def test_st_mark_omitted_as_feast(engine):
    """
    2027: Apr 25 = 5th Sunday of Easter (PRINCIPAL_TEMPORAL sort=20).
    St Mark is FEAST (sort=70): outranked and NOT transferable -> role='omitted'.
    """
    print("\n=== Test 3a: St. Mark omitted as FEAST (2027) ===")
    resolved = resolve_year(engine, 'UNIVERSAL', 2027)

    nominal = datetime.date(2027, 4, 25)

    # St Mark should NOT be celebrated anywhere.
    celebrated = [r for r in resolved if r['epoch_slug'] == 'st-mark' and r['role'] == 'celebrated']
    assert not celebrated, (
        f"FAIL: st-mark appears as 'celebrated' in 2027 but should be omitted.\n"
        f"  Rows: {find_slug(resolved, 'st-mark')}"
    )

    # St Mark should be recorded with role='omitted' on its nominal date.
    omitted = [r for r in resolved if r['epoch_slug'] == 'st-mark' and r['role'] == 'omitted']
    assert omitted, (
        f"FAIL: st-mark has no 'omitted' row for 2027.\n"
        f"  All st-mark rows: {find_slug(resolved, 'st-mark')}"
    )
    assert omitted[0]['dt'] == nominal, (
        f"FAIL: st-mark omitted row has dt={omitted[0]['dt']} but expected {nominal}"
    )
    print(f"  St Mark (FEAST) correctly omitted on {nominal}: PASS")
    print("  PASS")


# ---------------------------------------------------------------------------
# Test 3b — St. Mark transfers as local PROPER_SOLEMNITY (2027)
# ---------------------------------------------------------------------------

def test_st_mark_transfers_as_proper_solemnity(engine):
    """
    2027: Apr 25 = 5th Sunday of Easter (PRINCIPAL_TEMPORAL sort=20).
    With local_solemnities=['st-mark'], St Mark is elevated to PROPER_SOLEMNITY
    (sort=40): still outranked by PRINCIPAL_TEMPORAL (sort=20), but now
    transferable -> transfers to Apr 26 (WEEKDAY sort=120 > 40).
    """
    print("\n=== Test 3b: St. Mark as local PROPER_SOLEMNITY (2027) ===")
    resolved = resolve_year(engine, 'UNIVERSAL', 2027, local_solemnities=['st-mark'])

    nominal  = datetime.date(2027, 4, 25)
    expected = datetime.date(2027, 4, 26)  # Monday of 5th week of Easter (WEEKDAY)

    assert_slug_not_celebrated_on(resolved, 'st-mark', nominal)

    cel_nom = celebrated_on(resolved, nominal)
    print(f"  Celebrated on {nominal}: {cel_nom['epoch_slug']!r} ({cel_nom['rank_code']})")

    row = assert_slug_celebrated_on(resolved, 'st-mark', expected,
                                    is_transferred=True, nominal_dt=nominal)
    assert row['rank_code'] == 'PROPER_SOLEMNITY', (
        f"FAIL: st-mark rank_code={row['rank_code']!r} but expected 'PROPER_SOLEMNITY'"
    )
    print(f"  St Mark (PROPER_SOLEMNITY) transferred to {expected}: PASS "
          f"(is_transferred={row['is_transferred']}, rank_code={row['rank_code']})")
    print("  PASS")


# ---------------------------------------------------------------------------
# Test 4 — No-collision sanity: Assumption 2026
# ---------------------------------------------------------------------------

def test_assumption_no_collision(engine):
    """
    Assumption (Aug 15) 2026: check temporal day.
    The Assumption is a SOLEMNITY (sort=30). On a plain weekday (sort=120 > 30),
    the saint wins in place: is_transferred=0, role='celebrated'.
    """
    print("\n=== Test 4: Assumption in place (2026) ===")
    resolved = resolve_year(engine, 'UNIVERSAL', 2026)

    nominal = datetime.date(2026, 8, 15)

    # Find what temporal day Aug 15 2026 is.
    cel = celebrated_on(resolved, nominal)
    assert cel is not None, "FAIL: No celebrated observance on 2026-08-15"

    # The Assumption (SOLEMNITY sort=30) should beat any ordinary weekday (sort=120).
    # Verify it's celebrated in place.
    assumption_rows = find_slug(resolved, 'assumption')
    print(f"  All assumption rows: {assumption_rows}")

    celebrated_rows = [r for r in assumption_rows if r['role'] == 'celebrated']
    assert celebrated_rows, (
        f"FAIL: 'assumption' has no 'celebrated' row in 2026.\n"
        f"  All rows: {assumption_rows}"
    )
    row = celebrated_rows[0]
    assert row['dt'] == nominal, (
        f"FAIL: assumption celebrated on {row['dt']} but expected {nominal}"
    )
    assert row['is_transferred'] == 0, (
        f"FAIL: assumption is_transferred={row['is_transferred']} but expected 0"
    )
    print(f"  Assumption celebrated in place on {nominal}: PASS "
          f"(rank={row['rank_code']}, is_transferred={row['is_transferred']})")
    print("  PASS")


# ---------------------------------------------------------------------------
# Test 5 — Materialize round-trip (2028)
# ---------------------------------------------------------------------------

def test_materialize_round_trip(engine):
    """
    Materialize 2028 into lit_observance_resolved; query it back and confirm
    the rows match resolve_year() output.
    """
    print("\n=== Test 5: Materialize round-trip (2028) ===")

    # Resolve in-memory first (for comparison).
    resolved = resolve_year(engine, 'UNIVERSAL', 2028)
    n_expected = len(resolved)

    # Materialize (idempotent).
    n_inserted = materialize_year(engine, 'UNIVERSAL', 2028)
    print(f"  materialize_year returned: {n_inserted} rows")
    assert n_inserted == n_expected, (
        f"FAIL: materialize_year returned {n_inserted} but resolve_year produced {n_expected}"
    )

    # Query back from DB.
    with engine.connect() as conn:
        db_rows = conn.execute(text("""
            SELECT jurisdiction, dt, epoch_slug, rank_code, role,
                   is_transferred, nominal_dt, notes
            FROM lit_observance_resolved
            WHERE jurisdiction = 'UNIVERSAL' AND YEAR(dt) = 2028
            ORDER BY dt, role, epoch_slug
        """)).fetchall()

    n_db = len(db_rows)
    print(f"  Rows in lit_observance_resolved for 2028: {n_db}")
    assert n_db == n_expected, (
        f"FAIL: {n_db} rows in DB but expected {n_expected}"
    )

    # Check a specific landmark: St Joseph on 2028-03-20 (transferred).
    joseph_db = [r for r in db_rows
                 if r[2] == 'st-joseph' and r[4] == 'celebrated']
    assert joseph_db, "FAIL: st-joseph 'celebrated' not found in DB after materialize"
    j = joseph_db[0]
    assert str(j[1]) == '2028-03-20', (
        f"FAIL: st-joseph in DB on {j[1]} but expected 2028-03-20"
    )
    assert j[5] == 1, f"FAIL: st-joseph is_transferred={j[5]} in DB but expected 1"
    print(f"  st-joseph in DB: dt={j[1]}, is_transferred={j[5]}, nominal_dt={j[6]} — PASS")

    # Second call (idempotency): should produce the same count without duplicates.
    n2 = materialize_year(engine, 'UNIVERSAL', 2028)
    with engine.connect() as conn:
        n_db2 = conn.execute(text("""
            SELECT COUNT(*) FROM lit_observance_resolved
            WHERE jurisdiction='UNIVERSAL' AND YEAR(dt)=2028
        """)).scalar()
    assert n_db2 == n_expected, (
        f"FAIL: After second materialize, {n_db2} rows in DB but expected {n_expected} "
        f"(idempotency broken)"
    )
    print(f"  Idempotency check (2nd materialize): {n_db2} rows — PASS")
    print("  PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    engine = get_engine()
    failures = []

    tests = [
        test_st_joseph_transfer,
        test_annunciation_transfer,
        test_st_mark_omitted_as_feast,
        test_st_mark_transfers_as_proper_solemnity,
        test_assumption_no_collision,
        test_materialize_round_trip,
    ]

    for t in tests:
        try:
            t(engine)
        except AssertionError as e:
            print(f"  *** ASSERTION ERROR: {e}")
            failures.append(t.__name__)
        except Exception as e:
            print(f"  *** UNEXPECTED ERROR: {type(e).__name__}: {e}")
            failures.append(t.__name__)

    print()
    if failures:
        print(f"FAILED: {len(failures)} test(s): {', '.join(failures)}")
        sys.exit(1)
    else:
        print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == '__main__':
    run_all()
