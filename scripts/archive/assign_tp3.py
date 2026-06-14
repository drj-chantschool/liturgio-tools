#!/usr/bin/env python
"""
assign_tp3.py — Record GR proper assignments for the Third Week of Easter.

Uses weekly defaults (wkday=NULL) for chants that apply to the whole week,
and day-specific overrides (wkday=N, 1=Sun...7=Sat) for exceptions.

Archived reference script (liturgio-tools `scripts/archive/`) — an example
of the `assign` pattern for the Third Week of Easter. Not meant to be
re-run; kept for reference.
"""
import argparse
from sqlalchemy import text

from liturgio_tools.cli import get_rw_engine

JURISDICTION = 'UNIVERSAL'
AUTHORITY = 'GRADUALE'
SEASON = 'PASC'
SUBSEASON = 'AD_ASC'
WKNUM = 3

# ── Resolved chant_group_ids (Solesmes version preferred) ────────────────────
# gregobase: https://gregobase.selapa.net/chant.php?id=<group_id>
CHANT_GROUPS = {
    'jubilate':  536,   # Jubilate Deo       in  mode 8  1961 GR p.265
    'replatur':  557,   # Repleatur os       in  mode 3  1961 GR p.302
    'lauda':     668,   # Lauda anima        of  mode 4  1961 GR p.267
    'cantate':   579,   # Cantate Domino     co  mode 8  1961 GR p.273
    'surrexit':  121,   # Surrexit Dominus   co  mode 6  1961 GR p.246
    'simon':     846,   # Simon Joannis      co  mode 8  1961 GR p.531
    'video':     920,   # Video caelos       co  mode 8  1961 GR p.38
    'panis':     782,   # Panis quem ego     co  mode 2  1961 GR p.362
    'qui':       798,   # Qui manducat       co  mode 8  1961 GR p.344
}

# ── Assignment plan ───────────────────────────────────────────────────────────
# (part_code, chant_key, wkday)  — wkday=None means "all days" default
# wkday: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
# (part_code, chant_key, wkday, cycle_sun)
# wkday: None=all days, 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
# cycle_sun: None=all years, 1=Year A, 2=Year B, 0=Year C  (liturgical_year mod 3)
ASSIGNMENTS = [
    # Weekly defaults
    ('in', 'jubilate', None, None),
    ('of', 'lauda',    None, None),
    ('co', 'cantate',  None, None),
    # Day-specific overrides
    ('in', 'replatur', 4,    None),   # feria 4 (Wednesday)
    ('co', 'video',    3,    None),   # feria 3 (Tuesday)
    ('co', 'panis',    5,    None),   # feria 5 (Thursday)
    ('co', 'qui',      6,    None),   # feria 6 (Friday)
    # Sunday year-specific communions (override the weekly default)
    ('co', 'surrexit', 1,    1),      # Dom anno A
    ('co', 'simon',    1,    0),      # Dom anno C
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be assigned without writing to DB')
    args = parser.parse_args()

    print('Assignment plan:')
    print(f'  season={SEASON}  subseason={SUBSEASON}  wknum={WKNUM}')
    print()
    day_label = {None: 'ALL', 1:'Sun', 2:'Mon', 3:'Tue', 4:'Wed', 5:'Thu', 6:'Fri', 7:'Sat'}
    year_label = {None: 'ALL', 1:'A', 2:'B', 0:'C'}
    for part_code, chant_key, wkday, cycle_sun in ASSIGNMENTS:
        gid = CHANT_GROUPS[chant_key]
        print(f'  {day_label[wkday]:4}  yr={year_label[cycle_sun]}  {part_code}  group={gid}  ({chant_key})')
    print()

    if args.dry_run:
        print('DRY RUN -- nothing written.')
        return

    confirm = input('Write these assignments? [y/N] ')
    if confirm.strip().lower() != 'y':
        print('Aborted.')
        return

    rw = get_rw_engine()
    with rw.connect() as conn:
        # Look up part_ids
        part_rows = conn.execute(text("""
            SELECT part_code, part_id FROM service_part
            WHERE part_code IN ('in', 'of', 'co') AND service_code = 'MASS'
        """)).fetchall()
        part_ids = {r[0]: r[1] for r in part_rows}

        count = 0
        for part_code, chant_key, wkday, cycle_sun in ASSIGNMENTS:
            conn.execute(text("""
                INSERT INTO lit_part_assignment
                    (jurisdiction, part_id, season, subseason, wknum, wkday,
                     cycle_sun, chant_group_id, assignment_authority_code)
                VALUES
                    (:jur, :part_id, :season, :subseason, :wknum, :wkday,
                     :cycle_sun, :gid, :authority)
                ON DUPLICATE KEY UPDATE
                    chant_group_id = VALUES(chant_group_id),
                    assignment_authority_code = VALUES(assignment_authority_code),
                    updated_at = CURRENT_TIMESTAMP
            """), {
                'jur': JURISDICTION,
                'part_id': part_ids[part_code],
                'season': SEASON,
                'subseason': SUBSEASON,
                'wknum': WKNUM,
                'wkday': wkday,
                'cycle_sun': cycle_sun,
                'gid': CHANT_GROUPS[chant_key],
                'authority': AUTHORITY,
            })
            count += 1

        conn.commit()

    print(f'Done. {count} assignments written.')


if __name__ == '__main__':
    main()
