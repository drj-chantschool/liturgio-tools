-- Parameters:
--   :dt            (DATE)
--   :jurisdiction  (VARCHAR)  e.g. 'US'
--   :service_code  (VARCHAR)  e.g. 'VESPERS'
--
-- Behavior:
--   - ctx row: prefer (:jurisdiction, :dt) else (UNIVERSAL, :dt)
--   - assignments: prefer :jurisdiction rows else UNIVERSAL rows

WITH ctx AS (
    SELECT
        pos.dt,
        pos.jurisdiction AS ctx_jurisdiction,     -- the row we found (US or UNIVERSAL)
        :jurisdiction    AS req_jurisdiction,     -- what caller asked for

        pos.cycle_wk,
        pos.cycle_sun,
        ld.season,
        ld.subseason,
        ld.wknum,
        ld.seq,
        pos.wkday,
        ld.title
    FROM proper_of_seasons pos
    JOIN liturgical_day ld
      ON ld.lit_day_id = pos.lit_day_id
    WHERE pos.dt = :dt
      AND pos.jurisdiction IN (:jurisdiction, 'UNIVERSAL')
    ORDER BY
      CASE WHEN pos.jurisdiction = :jurisdiction THEN 0 ELSE 1 END
    LIMIT 1
),
candidates AS (
    select
        ctx.title,
        sp.part_id,
        sp.service_code,
        sp.part_code,
        sp.display_order,

        lpa.assignment_id,
        lpa.jurisdiction AS assignment_jurisdiction,
        lpa.chant_group_id,
        lpa.assignment_authority_code,
        lpa.notes,

        -- Specificity scoring (higher wins). Liturgical matches dominate psalter fallback.
        (
            100000 *
            (
                (lpa.season     IS NOT NULL) +
                (lpa.subseason  IS NOT NULL) +
                (lpa.wknum      IS NOT NULL) +
                (lpa.wkday      IS NOT NULL) +
                (lpa.seq        IS NOT NULL) +
                (lpa.cycle_wk   IS NOT NULL) +
                (lpa.cycle_sun  IS NOT NULL)
            )
            +
            1000 *
            (
                (lpa.wknum_mod_4 IS NOT NULL) +
                (lpa.wknum_mod_2 IS NOT NULL)
            )
            +
            -- jurisdiction preference (US beats UNIVERSAL, but doesn't outweigh liturgical specificity)
            CASE WHEN lpa.jurisdiction = ctx.req_jurisdiction THEN 10 ELSE 0 END
        ) AS specificity_score

    FROM ctx
    JOIN service_part sp
      ON sp.service_code = :service_code

    left JOIN lit_part_assignment lpa
      ON lpa.part_id = sp.part_id
     AND lpa.jurisdiction IN (ctx.req_jurisdiction, 'UNIVERSAL')

     -- ---- Liturgical keys (wildcards allowed) ----
     AND (lpa.season    IS NULL OR lpa.season    = ctx.season)
     AND (lpa.subseason IS NULL OR lpa.subseason = ctx.subseason)
     AND (lpa.wknum     IS NULL OR lpa.wknum     = ctx.wknum)
     AND (lpa.wkday     IS NULL OR lpa.wkday     = ctx.wkday)
     AND (lpa.seq       IS NULL OR lpa.seq       = ctx.seq)

     -- ---- Cycle keys (wildcards allowed) ----
     AND (lpa.cycle_wk  IS NULL OR lpa.cycle_wk  = ctx.cycle_wk)
     AND (lpa.cycle_sun IS NULL OR lpa.cycle_sun = ctx.cycle_sun)

     -- ---- Psalter fallback keys (wildcards allowed) ----
     -- NOTE: replace MOD(ctx.wknum, N) with your real psalter-week logic if needed.
     AND (lpa.wknum_mod_4 IS NULL OR lpa.wknum_mod_4 = MOD(ctx.wknum, 4))
     AND (lpa.wknum_mod_2 IS NULL OR lpa.wknum_mod_2 = MOD(ctx.wknum, 2))
),
ranked AS (
    SELECT
        c.*,
        ROW_NUMBER() OVER (
            PARTITION BY c.part_id
            ORDER BY
                c.specificity_score DESC,
                c.assignment_id DESC
        ) AS rn
    FROM candidates c
)
select
    title,
    part_id,
    service_code,
    part_code,
    display_order,

    chant_group_id,
    assignment_authority_code,

    assignment_jurisdiction,
    notes,
    assignment_id
FROM ranked
-- WHERE rn = 1
ORDER BY display_order, rn ;
