-- Parameters:
--   :dt            (DATE)
--   :jurisdiction  (VARCHAR)  e.g. 'US'
--   :service_code  (VARCHAR)  e.g. 'MASS'
--
-- Behavior:
--   - ctx row: prefer (:jurisdiction, :dt) else ('UNIVERSAL', :dt)
--   - assignments: prefer :jurisdiction rows else UNIVERSAL rows
--
-- Resolution model (phase 4c depth-in-tree):
--   The day's epoch slug is the starting node. We walk UP the lit_epoch_tree
--   adjacency list to collect all ancestors, assigning depth 0 to the specific
--   day, depth 1 to its week, depth 2 to subseason, depth 3 to season. An
--   assignment whose lit_epoch_slug is at depth 0 beats one at depth 1, etc. —
--   the most specific ancestor wins. Assignments with lit_epoch_slug IS NULL
--   are psalter fallbacks and always lose to any epoch match.
--
-- Resolver integration (phase 5 — calendar resolver):
--   The starting epoch node is NO LONGER taken straight from the temporal
--   calendar (proper_of_seasons.lit_day_id). Instead we consult the
--   materialized calendar resolver, lit_observance_resolved, for the
--   CELEBRATED observance on :dt. That observance's epoch_slug may be:
--     - a temporal day node (an ordinary feria/Sunday), OR
--     - a SAINT slug (e.g. 'st-joseph', 'assumption'), possibly TRANSFERRED in
--       from another civil date (e.g. St Joseph moved out of a Lenten Sunday).
--   We therefore set:
--       observed_epoch_slug = COALESCE(r.epoch_slug, pos.lit_day_id)
--   i.e. prefer the resolver's celebrated epoch, falling back to the bare
--   temporal day when no resolver row exists (e.g. a year not yet materialized).
--   The recursive ancestor walk then starts from observed_epoch_slug and works
--   for BOTH cases: temporal days climb their season tree as before, while
--   saint epochs are currently tree ROOTS (no ancestors), so an assignment to a
--   saint slug resolves at depth 0. Inheriting chants from a saint's Common
--   (e.g. Common of Martyrs) via the tree is FUTURE WORK; for now only direct
--   assignments to the saint slug are honoured.
--   cycle_wk / cycle_sun / wkday and the week number (wknum, for the psalter-mod
--   fallback) continue to come from the temporal proper_of_seasons / lit_epoch
--   row, since those are properties of the civil/temporal day regardless of any
--   overlaid sanctoral observance.
--
-- Step 8 (2026-06-26): queries lit_part_sources directly (lit_part_assignment
--   dropped). chant_group_id is derived by joining through lps.chant_uuid to
--   gregobase_chant_group_map or local_chants.

WITH RECURSIVE ctx AS (
    -- Resolve the liturgical day for this date, preferring the requested jurisdiction.
    -- The observed epoch comes from the resolver's celebrated observance when
    -- available (accounts for saints + transfers), else the temporal day.
    SELECT
        pos.dt,
        pos.jurisdiction                AS ctx_jurisdiction,
        :jurisdiction                   AS req_jurisdiction,
        COALESCE(r.epoch_slug, pos.lit_day_id) AS day_slug,
        pos.cycle_wk,
        pos.cycle_sun,
        pos.wkday,
        le.wknum,                        -- week number for psalter-mod fallback
        le.title
    FROM proper_of_seasons pos
    JOIN lit_epoch le
      ON le.slug = pos.lit_day_id
    -- Overlay the resolver's CELEBRATED observance for the chosen ctx
    -- jurisdiction on this date (saints/transfers). NULL -> temporal fallback.
    LEFT JOIN lit_observance_resolved r
      ON r.jurisdiction = pos.jurisdiction
     AND r.dt           = :dt
     AND r.role         = 'celebrated'
    WHERE pos.dt = :dt
      AND pos.jurisdiction IN (:jurisdiction, 'UNIVERSAL')
    ORDER BY
        CASE WHEN pos.jurisdiction = :jurisdiction THEN 0 ELSE 1 END
    LIMIT 1
),
ancestors AS (
    -- Walk UP the tree from the day's slug.
    -- depth 0 = the specific day (most specific),
    -- depth 1 = parent (week or subseason),
    -- depth 2 = grandparent (subseason or season),
    -- depth 3 = great-grandparent (season).
    -- Saints are typically root nodes with no parents — they stay at depth 0.
    SELECT ctx.day_slug AS slug, 0 AS depth
    FROM ctx
    UNION ALL
    SELECT et.parent_slug, a.depth + 1
    FROM ancestors a
    JOIN lit_epoch_tree et ON et.child_slug = a.slug
),
candidates AS (
    SELECT
        ctx.title,
        sp.part_id,
        sp.service_code,
        sp.part_code,
        sp.display_order,

        lps.text_id,
        lps.jurisdiction                AS assignment_jurisdiction,
        lps.assignment_authority_code,
        lps.notes,
        lps.chant_uuid,

        -- Derive chant_group_id by joining through chant_uuid.
        -- gregobase: prefix → gregobase_chant_group_map; local: → local_chants.
        COALESCE(gcm.chant_group_id, lc.chant_group_id) AS chant_group_id,

        -- Is this an epoch match (1) or a psalter fallback (0)?
        -- Epoch matches always beat fallback.
        CASE WHEN lps.lit_epoch_slug IS NOT NULL THEN 1 ELSE 0 END
            AS epoch_match,

        -- For epoch matches, the depth at which the slug was found.
        -- Smallest depth wins (day-level override beats week beats season).
        -- NULL for psalter fallbacks (they lose regardless).
        anc.depth,

        -- Specificity of cycle/wkday filters: more non-null filters = more specific.
        (   (lps.cycle_wkday  IS NOT NULL) +
            (lps.cycle_sun IS NOT NULL) +
            (lps.wkday     IS NOT NULL)
        ) AS cycle_specificity,

        -- Jurisdiction preference: requested jurisdiction beats UNIVERSAL.
        CASE WHEN lps.jurisdiction = ctx.req_jurisdiction THEN 1 ELSE 0 END
            AS jur_preference

    FROM ctx

    -- All MASS service parts (or whichever service_code was requested)
    JOIN service_part sp
      ON sp.service_code = :service_code

    -- Epoch-matched assignments from lit_part_sources
    JOIN lit_part_sources lps
      ON lps.part_id = sp.part_id
     AND lps.jurisdiction IN (ctx.req_jurisdiction, 'UNIVERSAL')

    LEFT JOIN ancestors anc
      ON anc.slug = lps.lit_epoch_slug   -- NULL for psalter rows

    -- Resolve chant_group_id from chant_uuid (two prefix types)
    LEFT JOIN gregobase_chant_group_map gcm
           ON lps.chant_uuid LIKE 'gregobase:%'
          AND gcm.gregobase_id = CAST(SUBSTRING(lps.chant_uuid, 11) AS UNSIGNED)
    LEFT JOIN local_chants lc
           ON lps.chant_uuid LIKE 'local:%'
          AND lc.local_chant_id = SUBSTRING(lps.chant_uuid, 7)

    -- Only include this row if it is an epoch match OR a psalter fallback
    WHERE (
        -- Epoch match: the assignment's slug is an ancestor of today's day slug
        (lps.lit_epoch_slug IS NOT NULL AND anc.slug IS NOT NULL)

        -- Psalter fallback: no epoch slug, but wknum_mod filters must agree
        OR (
            lps.lit_epoch_slug IS NULL
            AND (lps.wknum_mod_4 IS NULL OR lps.wknum_mod_4 = MOD(ctx.wknum, 4))
            AND (lps.wknum_mod_2 IS NULL OR lps.wknum_mod_2 = MOD(ctx.wknum, 2))
        )
    )

    -- Cycle and wkday wildcard filters (applied to both epoch and psalter rows)
    AND (lps.cycle_wkday IS NULL OR lps.cycle_wkday = ctx.cycle_wk)
    AND (lps.cycle_sun IS NULL OR lps.cycle_sun = ctx.cycle_sun)
    AND (lps.wkday     IS NULL OR lps.wkday     = ctx.wkday)
),
ranked AS (
    SELECT
        c.*,
        ROW_NUMBER() OVER (
            PARTITION BY c.part_id
            ORDER BY
                c.epoch_match        DESC,   -- epoch beats psalter
                c.depth              ASC,    -- smallest depth wins (day < week < season)
                c.cycle_specificity  DESC,   -- more cycle/wkday filters = more specific
                c.jur_preference     DESC,   -- requested jurisdiction beats UNIVERSAL
                c.text_id            DESC    -- final tiebreak: latest row wins
        ) AS rn
    FROM candidates c
)
SELECT
    title,
    part_id,
    service_code,
    part_code,
    display_order,

    text_id,
    chant_uuid,
    chant_group_id,
    assignment_authority_code,

    assignment_jurisdiction,
    notes,
    rn               -- ranking within each part_id; rn = 1 is the winner
FROM ranked
-- WHERE rn = 1      -- uncomment to return only the winning assignment per part
ORDER BY display_order, rn;
