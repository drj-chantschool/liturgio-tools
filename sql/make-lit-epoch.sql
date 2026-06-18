-- ============================================================
-- make-lit-epoch.sql
-- Phase 4a: lit_epoch node table + lit_epoch_tree adjacency list
--
-- ADDITIVE ONLY — does not touch liturgical_day, proper_of_saints,
-- lit_part_assignment, or any other existing table.
--
-- Idempotent: leading DROPs (tree first due to FK) make this
-- safe to re-run.
--
-- Tree structure:
--   season
--     subseason
--       week (for subseasons that have wknum > 0 days)
--         day (with wknum > 0)
--       day (fallback: days with wknum = 0 / NULL attach directly to subseason)
--   saint  (roots for now; Commons hierarchy is future work)
-- ============================================================

DROP TABLE IF EXISTS lit_epoch_tree;
DROP TABLE IF EXISTS lit_epoch;

-- ============================================================
-- DDL: lit_epoch
-- ============================================================
CREATE TABLE lit_epoch (
    slug        VARCHAR(64)  NOT NULL PRIMARY KEY,
    kind        VARCHAR(20)  NOT NULL,          -- season | subseason | week | day | saint
    title       VARCHAR(200) NULL,
    rank_code   VARCHAR(40)  NULL,              -- FK p_lit_rank; populated for day nodes; NULL for saint (rank lives per-jurisdiction in proper_of_saints)
    season      VARCHAR(10)  NULL,
    subseason   VARCHAR(10)  NULL,
    wknum       SMALLINT     NULL,
    seq         SMALLINT     NULL,
    lit_day_id  VARCHAR(64)  NULL,              -- back-ref to liturgical_day for day nodes
    sort_order  INT          NULL,
    notes       VARCHAR(500) NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    KEY idx_epoch_kind   (kind),
    KEY idx_epoch_litday (lit_day_id),

    CONSTRAINT fk_epoch_rank
        FOREIGN KEY (rank_code) REFERENCES p_lit_rank(rank_code)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

-- ============================================================
-- DDL: lit_epoch_tree  (parent_slug, child_slug adjacency list)
-- ============================================================
CREATE TABLE lit_epoch_tree (
    parent_slug VARCHAR(64) NOT NULL,
    child_slug  VARCHAR(64) NOT NULL,

    PRIMARY KEY (parent_slug, child_slug),
    KEY idx_tree_child (child_slug),

    CONSTRAINT fk_tree_parent
        FOREIGN KEY (parent_slug) REFERENCES lit_epoch(slug)
        ON UPDATE CASCADE ON DELETE CASCADE,

    CONSTRAINT fk_tree_child
        FOREIGN KEY (child_slug)  REFERENCES lit_epoch(slug)
        ON UPDATE CASCADE ON DELETE CASCADE
);

-- ============================================================
-- POPULATION: nodes (insert in dependency order: day/saint first,
-- then week, subseason, season — FK on rank_code only; tree edges
-- are added afterwards so insertion order for slug uniqueness
-- doesn't matter as long as all slugs exist before edges)
-- ============================================================

-- 1. day nodes (one per liturgical_day row, 393 expected)
INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, lit_day_id, sort_order)
SELECT
    ld.lit_day_id                       AS slug,
    'day'                               AS kind,
    ld.title                            AS title,
    ld.lit_rank                         AS rank_code,
    ld.season,
    ld.subseason,
    ld.wknum,
    ld.seq,
    ld.lit_day_id                       AS lit_day_id,
    ld.lit_day_order                    AS sort_order
FROM liturgical_day ld;

-- 2. week nodes: one per distinct (season, subseason, wknum) where wknum > 0
--    slug = CONCAT(season, '-', subseason, '-', LPAD(wknum, 2, '0'))
--    sort_order = MIN(lit_day_order) of member days
INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, lit_day_id, sort_order)
SELECT
    CONCAT(ld.season, '-', ld.subseason, '-', LPAD(ld.wknum, 2, '0'))  AS slug,
    'week'                                                               AS kind,
    NULL                                                                 AS title,
    NULL                                                                 AS rank_code,
    ld.season,
    ld.subseason,
    ld.wknum,
    NULL                                                                 AS seq,
    NULL                                                                 AS lit_day_id,
    MIN(ld.lit_day_order)                                                AS sort_order
FROM liturgical_day ld
WHERE ld.wknum IS NOT NULL
  AND ld.wknum > 0
GROUP BY ld.season, ld.subseason, ld.wknum;

-- 3. subseason nodes: one per distinct (season, subseason)
--    slug = CONCAT(season, '-', subseason)
--    sort_order = MIN(lit_day_order) of member days
INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, lit_day_id, sort_order)
SELECT
    CONCAT(ld.season, '-', ld.subseason)    AS slug,
    'subseason'                             AS kind,
    NULL                                    AS title,
    NULL                                    AS rank_code,
    ld.season,
    ld.subseason,
    NULL                                    AS wknum,
    NULL                                    AS seq,
    NULL                                    AS lit_day_id,
    MIN(ld.lit_day_order)                   AS sort_order
FROM liturgical_day ld
GROUP BY ld.season, ld.subseason;

-- 4. season nodes: one per distinct season
--    slug = season value itself
--    sort_order = MIN(lit_day_order) of member days
INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, lit_day_id, sort_order)
SELECT
    ld.season                       AS slug,
    'season'                        AS kind,
    NULL                            AS title,
    NULL                            AS rank_code,
    ld.season,
    NULL                            AS subseason,
    NULL                            AS wknum,
    NULL                            AS seq,
    NULL                            AS lit_day_id,
    MIN(ld.lit_day_order)           AS sort_order
FROM liturgical_day ld
GROUP BY ld.season;

-- 5. saint nodes: one per distinct slug from proper_of_saints
--    title = MIN(common_name) — collapses jurisdictions to one canonical name
--    rank_code = NULL (rank is per-jurisdiction in proper_of_saints)
--    slug is the lowercase-kebab value from proper_of_saints (no collision with UPPERCASE temporal slugs)
INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, lit_day_id, sort_order)
SELECT
    ps.slug                         AS slug,
    'saint'                         AS kind,
    MIN(ps.common_name)             AS title,
    NULL                            AS rank_code,
    NULL                            AS season,
    NULL                            AS subseason,
    NULL                            AS wknum,
    NULL                            AS seq,
    NULL                            AS lit_day_id,
    NULL                            AS sort_order
FROM proper_of_saints ps
GROUP BY ps.slug;

-- ============================================================
-- POPULATION: tree edges
-- Build by matching on stored season/subseason/wknum columns
-- in lit_epoch nodes — no string-parsing of slugs.
-- ============================================================

-- Edge A: week -> day
--   parent = the week node whose (season, subseason, wknum) matches the day
--   child  = the day node
--   Only for day nodes with wknum > 0 (days with wknum = 0 go directly to subseason)
INSERT INTO lit_epoch_tree (parent_slug, child_slug)
SELECT
    ew.slug     AS parent_slug,
    ed.slug     AS child_slug
FROM lit_epoch ed
JOIN lit_epoch ew
  ON ew.kind      = 'week'
 AND ew.season    = ed.season
 AND ew.subseason = ed.subseason
 AND ew.wknum     = ed.wknum
WHERE ed.kind   = 'day'
  AND ed.wknum IS NOT NULL
  AND ed.wknum  > 0;

-- Edge B: subseason -> day (fallback for days with wknum = 0 or NULL)
--   These days have no week node, so they attach directly to their subseason.
INSERT INTO lit_epoch_tree (parent_slug, child_slug)
SELECT
    es.slug     AS parent_slug,
    ed.slug     AS child_slug
FROM lit_epoch ed
JOIN lit_epoch es
  ON es.kind      = 'subseason'
 AND es.season    = ed.season
 AND es.subseason = ed.subseason
WHERE ed.kind  = 'day'
  AND (ed.wknum IS NULL OR ed.wknum = 0);

-- Edge C: subseason -> week
INSERT INTO lit_epoch_tree (parent_slug, child_slug)
SELECT
    es.slug     AS parent_slug,
    ew.slug     AS child_slug
FROM lit_epoch ew
JOIN lit_epoch es
  ON es.kind      = 'subseason'
 AND es.season    = ew.season
 AND es.subseason = ew.subseason
WHERE ew.kind = 'week';

-- Edge D: season -> subseason
INSERT INTO lit_epoch_tree (parent_slug, child_slug)
SELECT
    esn.slug    AS parent_slug,
    ess.slug    AS child_slug
FROM lit_epoch ess
JOIN lit_epoch esn
  ON esn.kind   = 'season'
 AND esn.season = ess.season
WHERE ess.kind = 'subseason';

-- saint nodes are roots; no parent edges inserted (Commons hierarchy is future work).
