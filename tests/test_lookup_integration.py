"""
Integration tests for the day-parts lookup + calendar resolver wiring.

These tests prove that ``parts_for_date`` follows the calendar resolver
(``lit_observance_resolved``) when choosing the observed epoch, so that
"what chant is assigned on date X" accounts for saints and transfers, not
just the temporal calendar.

Setup / teardown contract
-------------------------
``lit_part_assignment`` MUST be left exactly as found. The suite records the
baseline row count (expected 80), inserts test assignments inside a
module-scoped fixture, asserts against them, then deletes exactly those rows
and re-checks the count. Resolver materialization (``materialize_year``) is
idempotent and is only (re)run for the test years; it is not torn down.

Data facts these tests rely on (UNIVERSAL jurisdiction, resolver years
2027-2028 currently materialized):
  - 2027-08-15  Assumption celebrated IN PLACE (temporal day = OT-OT-20-1).
  - 2028-03-20  St Joseph TRANSFERRED IN (nominal 2028-03-19 = TQ-LENT-03-1,
                a Lenten feria that wins on the nominal date).
  - 2028-04-20  Pure temporal: Thursday in the Easter octave (PASC-OCT-01-5),
                which already has baseline assignments.
"""

from __future__ import annotations

import datetime

import keyring
import pytest
from sqlalchemy import create_engine, text

from liturgio_tools.liturgical_calendar.lookup import parts_for_date
from liturgio_tools.liturgical_calendar.resolver import materialize_year


JURISDICTION = "UNIVERSAL"
SERVICE_CODE = "MASS"
BASELINE_ROWS = 80

# A service part we hijack for the saint tests. We pick a part that has no
# baseline assignment on the saint epochs so the test row is unambiguous.
# part_id 9 = 'co' (communion); part_id 1 = 'in' (introit).
TEST_PART_INTROIT = 1
TEST_PART_COMMUNION = 9

# Sentinel chant_group_ids. These must satisfy the chant_group FK, so we use
# real ids (1, 2) that are NOT referenced by any baseline assignment, keeping
# the test rows unambiguous (baseline assignments use ids like 1332, 1129...).
CG_ASSUMPTION = 1
CG_ST_JOSEPH = 2


@pytest.fixture(scope="module")
def engine():
    password = keyring.get_password("liturgio-mysql", "jcost")
    assert password is not None, "No keyring password for jcost@liturgio"
    eng = create_engine(
        f"mysql+mysqlconnector://jcost:{password}@localhost:3306/liturgio",
        future=True,
    )
    yield eng
    eng.dispose()


@pytest.fixture(scope="module", autouse=True)
def resolver_materialized(engine):
    """Ensure the resolver is materialized for the test years (idempotent)."""
    materialize_year(engine, JURISDICTION, 2027)
    materialize_year(engine, JURISDICTION, 2028)


@pytest.fixture(scope="module")
def seeded_assignments(engine):
    """
    Insert test assignments for saint epochs, assert baseline first, and
    remove exactly the inserted rows afterwards (restoring the 80-row state).
    """
    # --- Baseline guard ----------------------------------------------------
    with engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM lit_part_assignment")
        ).scalar()
    assert before == BASELINE_ROWS, (
        f"Expected {BASELINE_ROWS} baseline rows in lit_part_assignment, "
        f"found {before}. Aborting to avoid corrupting data."
    )

    insert_sql = text("""
        INSERT INTO lit_part_assignment
            (jurisdiction, part_id, lit_epoch_slug, chant_group_id,
             assignment_authority_code, notes)
        VALUES
            (:jur, :part_id, :slug, :cg, 'CUSTOM', :notes)
    """)

    inserted_ids: list[int] = []
    with engine.begin() as conn:
        for part_id, slug, cg, notes in [
            (TEST_PART_INTROIT, "assumption", CG_ASSUMPTION,
             "test:assumption-in-place"),
            (TEST_PART_COMMUNION, "st-joseph", CG_ST_JOSEPH,
             "test:st-joseph-transfer"),
        ]:
            res = conn.execute(insert_sql, {
                "jur": JURISDICTION, "part_id": part_id, "slug": slug,
                "cg": cg, "notes": notes,
            })
            inserted_ids.append(int(res.lastrowid))

    yield {
        "assumption_id": inserted_ids[0],
        "st_joseph_id": inserted_ids[1],
    }

    # --- Teardown: delete exactly what we inserted -------------------------
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM lit_part_assignment WHERE assignment_id IN "
                 "(:a, :b)"),
            {"a": inserted_ids[0], "b": inserted_ids[1]},
        )
    with engine.connect() as conn:
        after = conn.execute(
            text("SELECT COUNT(*) FROM lit_part_assignment")
        ).scalar()
    assert after == BASELINE_ROWS, (
        f"Teardown failed: lit_part_assignment has {after} rows, "
        f"expected {BASELINE_ROWS}."
    )


def _chant_group_for_part(rows, part_id):
    for r in rows:
        if r["part_id"] == part_id:
            return r["chant_group_id"]
    return None


# ---------------------------------------------------------------------------
# Test A — saint celebrated in place (Assumption, 2027-08-15)
# ---------------------------------------------------------------------------

def test_saint_celebrated_in_place(engine, seeded_assignments):
    """
    On 2027-08-15 the Assumption is celebrated (temporal day is OT-OT-20-1).
    Our test assignment keys the introit to the 'assumption' epoch. The lookup
    must follow the resolver to the saint epoch and return that chant — proving
    it did NOT use the temporal feria.
    """
    rows = parts_for_date(
        engine, JURISDICTION, "2027-08-15", SERVICE_CODE, winner_only=True
    )
    cg = _chant_group_for_part(rows, TEST_PART_INTROIT)
    assert cg == CG_ASSUMPTION, (
        f"Expected Assumption introit chant_group {CG_ASSUMPTION} on "
        f"2027-08-15, got {cg}. Rows: {rows}"
    )


# ---------------------------------------------------------------------------
# Test B — transferred solemnity (St Joseph: nominal 2028-03-19 -> 2028-03-20)
# ---------------------------------------------------------------------------

def test_transferred_solemnity_surfaces_on_transferred_date(
    engine, seeded_assignments
):
    """
    St Joseph (nominal 2028-03-19) is transferred to 2028-03-20 because the
    Lenten feria wins on the 19th. Our test assignment keys the communion to
    'st-joseph'. It must surface on the TRANSFERRED date (the 20th).
    """
    rows = parts_for_date(
        engine, JURISDICTION, "2028-03-20", SERVICE_CODE, winner_only=True
    )
    cg = _chant_group_for_part(rows, TEST_PART_COMMUNION)
    assert cg == CG_ST_JOSEPH, (
        f"Expected St Joseph communion chant_group {CG_ST_JOSEPH} on the "
        f"transferred date 2028-03-20, got {cg}. Rows: {rows}"
    )


def test_transferred_solemnity_absent_on_nominal_date(
    engine, seeded_assignments
):
    """
    On the impeded nominal date 2028-03-19 the celebrated observance is the
    Lenten feria (TQ-LENT-03-1), so St Joseph's chant must NOT surface there.
    """
    rows = parts_for_date(
        engine, JURISDICTION, "2028-03-19", SERVICE_CODE, winner_only=True
    )
    cg = _chant_group_for_part(rows, TEST_PART_COMMUNION)
    assert cg != CG_ST_JOSEPH, (
        f"St Joseph communion chant_group {CG_ST_JOSEPH} unexpectedly "
        f"surfaced on the IMPEDED nominal date 2028-03-19. Rows: {rows}"
    )


# ---------------------------------------------------------------------------
# Test C — temporal regression guard (Easter octave, 2028-04-20)
# ---------------------------------------------------------------------------

def test_temporal_day_still_resolves(engine, seeded_assignments):
    """
    Regression guard: a date with no overriding saint must still resolve its
    temporal assignments. 2028-04-20 is Thursday in the Easter octave
    (PASC-OCT-01-5), which has baseline assignments. Confirm the introit
    resolves to its baseline chant and is NOT polluted by any saint epoch.
    """
    rows = parts_for_date(
        engine, JURISDICTION, "2028-04-20", SERVICE_CODE, winner_only=True
    )
    assert rows, "Expected temporal assignments on 2028-04-20, got none."

    cg = _chant_group_for_part(rows, TEST_PART_INTROIT)
    # Baseline introit for PASC-OCT-01-5 is chant_group 1332 (assignment_id 2).
    assert cg == 1332, (
        f"Expected baseline Easter-octave introit chant_group 1332 on "
        f"2028-04-20, got {cg}. Rows: {rows}"
    )
    # And definitely not the saint sentinels.
    assert cg not in (CG_ASSUMPTION, CG_ST_JOSEPH)
