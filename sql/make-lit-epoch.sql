-- ============================================================
-- make-lit-epoch.sql
-- IDEMPOTENT build/refresh of lit_epoch + lit_epoch_tree.
--
-- Rewritten 2026-09-26.  The previous version was DROP TABLE + rebuild, which
-- would have discarded ~310 hand-curated nodes (ritual / common / calendar /
-- non-Christmas mass), the SAINTS calendar edges, the six wknum=0 container
-- nodes, and every curated title.  It now UPSERTS and never drops a table.
--
-- Running this against an up-to-date database is a NO-OP: zero rows inserted,
-- updated or deleted.  That is the contract; it is regression-tested by
-- snapshotting both tables, running the script twice and diffing.
--
-- ------------------------------------------------------------
-- OWNERSHIP BOUNDARY (the crux — read before editing)
-- ------------------------------------------------------------
-- The generator OWNS the temporal skeleton and nothing else:
--
--   OWNED, structural columns refreshed on every run
--     (kind, rank_code, season, subseason, wknum, seq, sort_order):
--       * kind='day'        — the 393 canonical day nodes derived from
--                             p_liturgical_subseasons (see RECONCILIATIONS)
--       * kind='week'       — one per (season, subseason, wknum), INCLUDING the
--                             six wknum=0 container nodes
--       * kind='subseason'  — one per (season, subseason)
--       * kind='season'     — one per season
--       * kind='mass'       — ONLY the four Christmas Masses under NAT-DAY-00-0
--
--   OWNED, existence only (INSERT-if-missing; columns NEVER refreshed):
--       * kind='saint'      — one per proper_of_saints.slug.  lit_epoch.rank_code
--                             on saint nodes is CURATED (95 of 235 are set; the
--                             proper_of_saints.rank_code column disagrees), so a
--                             refresh would clobber it.
--
--   SEEDED ONLY, never overwritten:
--       * title  — supplied on INSERT and filled in when NULL.  An existing
--                  non-NULL title always wins.  Titles are an editorial
--                  decision (p_liturgical_day_slug_overrides, the curated
--                  season/subseason list below, "O Antiphons", …).
--       * notes  — never written at all.  OT-FOL-00-5 carries a hand-written
--                  provenance note on an OWNED day node.
--
--   NOT OWNED — never inserted, updated or deleted:
--       * kind IN ('ritual','common','calendar') nodes  (50 + 26 + 2)
--       * kind='mass' nodes other than the four Christmas ones (10: Epiphany /
--         Easter / Ascension / Pentecost vigils, the two Holy Thursday Masses,
--         and three saint vigils)
--       * every tree edge outside the owned classes below — calendar->saint
--         (235), common->common (17), saint->mass (3), and day->mass edges
--         other than NAT-DAY-00-0's four.
--
--   OWNED tree edge classes (inserted AND pruned when stale):
--       season->subseason, subseason->week, subseason->day, week->day,
--       and NAT-DAY-00-0 -> its four Christmas Masses.
--       NOTE: subseason->day is owned but is now ALWAYS EMPTY — wknum=0 days
--       hang off their container, not the subseason (see CONTAINER LAYER).
--       Keeping it owned is what lets this script converge a pre-container
--       database onto the current shape.
--
--   Stale OWNED NODES are REPORTED, NOT DELETED (STEP 12).  A node absent from
--   the generated set is usually a deliberate hand-addition awaiting a
--   p_liturgical_subseasons.maxseq bump (as OT-FOL-00-5 was for five days), and
--   fk_lps_epoch is ON DELETE RESTRICT anyway.  Silent deletion is the one
--   behaviour this script must never have.
--
-- ------------------------------------------------------------
-- CONTAINER LAYER (new 2026-09-26)
-- ------------------------------------------------------------
-- Six subseasons have wknum=0 days.  Those days used to attach straight to the
-- subseason (old Edge B).  They now go through a kind='week' container node:
--
--     subseason -> <SEASON>-<SUBSEASON>-00 -> each wknum=0 day
--
--   ADV-II-00, NAT-DAY-00, NAT-EPI-00, OT-FOL-00, TQ-HOLYWEEK-00, TQ-LENT-00
--
-- sort_order = MIN(sort_order) of its days, exactly like a wknum>0 week node.
-- title = the parent subseason's title (so ADV-II-00 = "O Antiphons").
-- This reproduces the 2026-09-16 / 09-21 / 09-26 hand restructure, each step of
-- which was verified to leave resolution byte-identical.
--
-- ------------------------------------------------------------
-- RECONCILIATIONS — three places where the naive formula != live truth
-- ------------------------------------------------------------
-- R1  OT-OT-01-1 is NOT a real day (Ordinary Time week 1 has no Sunday) but it
--     still CONSUMES RANK() slot 152.  Live day sort_order is therefore 1..394
--     with exactly one gap, at 152, and the OT-OT-01 week node keeps 152.
-- R2  Divine Mercy Sunday is PASC-AD_ASC-02-1 live (Paschaltide reslug), not the
--     formula's PASC-OCT-02-1.  It keeps that slug's sort_order (106) and the
--     PASC-OCT-02 week node does not exist.  Week/subseason sort_order is
--     derived BEFORE this reslug, which is why PASC-AD_ASC-02 is 107 and not 106.
-- R3  OT-FOL-00-5 (Immaculate Heart) is a MEMORIAL; no broad rule covers it.
--
-- ------------------------------------------------------------
-- Sources: p_liturgical_subseasons, p_liturgical_day_slug_overrides,
--          p_lit_rank (FK target), proper_of_saints.
-- liturgical_day is NOT used.  mysql.help_topic is no longer used either — the
-- 0..299 number generator is a WITH RECURSIVE CTE, so an unprivileged account
-- can run this.
--
-- Expected node counts: 393 day | 62 week | 17 subseason | 5 season
--                       | 235 saint | 4 mass  = 716 generated
-- (lit_epoch holds 804 rows; the other 88 are the NOT-OWNED layers.)
-- Expected owned edges: 393 week->day | 62 subseason->week | 17 season->subseason
--                       | 4 day->mass = 476   (lit_epoch_tree holds 738.)
--
-- Run it with ONE connection (TEMPORARY tables must survive between statements):
--     propers-parser/_tmp/run_sql_file.py liturgio-tools/sql/make-lit-epoch.sql
-- ============================================================

-- ============================================================
-- STEP 0: DDL — create only if absent.  Never dropped.
-- ============================================================
CREATE TABLE IF NOT EXISTS lit_epoch (
    slug        VARCHAR(64)  NOT NULL PRIMARY KEY,
    kind        VARCHAR(20)  NOT NULL,          -- season | subseason | week | day | mass | saint | ritual | common | calendar
    title       VARCHAR(200) NULL,
    rank_code   VARCHAR(40)  NULL,              -- FK p_lit_rank
    season      VARCHAR(10)  NULL,
    subseason   VARCHAR(10)  NULL,
    wknum       SMALLINT     NULL,
    seq         SMALLINT     NULL,
    sort_order  INT          NULL,
    notes       VARCHAR(500) NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    KEY idx_epoch_kind (kind),

    CONSTRAINT fk_epoch_rank
        FOREIGN KEY (rank_code) REFERENCES p_lit_rank(rank_code)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS lit_epoch_tree (
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
-- STEP 0b: staging tables.  Everything is computed here first; the live tables
-- are touched only by the four statements in STEP 10/11.
-- ============================================================
DROP TEMPORARY TABLE IF EXISTS tmp_day_raw;
DROP TEMPORARY TABLE IF EXISTS tmp_epoch_gen;
DROP TEMPORARY TABLE IF EXISTS tmp_tree_gen;
DROP TEMPORARY TABLE IF EXISTS tmp_titles;

CREATE TEMPORARY TABLE tmp_day_raw (
    slug       VARCHAR(64) NOT NULL PRIMARY KEY,
    season     VARCHAR(10) NOT NULL,
    subseason  VARCHAR(10) NOT NULL,
    wknum      SMALLINT    NOT NULL,
    seq        SMALLINT    NOT NULL,
    sort_order INT         NOT NULL
);

CREATE TEMPORARY TABLE tmp_epoch_gen (
    slug          VARCHAR(64)  NOT NULL PRIMARY KEY,
    kind          VARCHAR(20)  NOT NULL,
    title         VARCHAR(200) NULL,
    rank_code     VARCHAR(40)  NULL,
    season        VARCHAR(10)  NULL,
    subseason     VARCHAR(10)  NULL,
    wknum         SMALLINT     NULL,
    seq           SMALLINT     NULL,
    sort_order    INT          NULL,
    -- 1 = structural columns are generator-owned and get refreshed.
    -- 0 = existence only (saint nodes); columns are curated.
    owns_columns  TINYINT      NOT NULL DEFAULT 1,
    KEY idx_tmp_kind (kind),
    KEY idx_tmp_nat (season, subseason, wknum)
);

CREATE TEMPORARY TABLE tmp_tree_gen (
    parent_slug VARCHAR(64) NOT NULL,
    child_slug  VARCHAR(64) NOT NULL,
    PRIMARY KEY (parent_slug, child_slug)
);

CREATE TEMPORARY TABLE tmp_titles (
    slug  VARCHAR(64)  NOT NULL PRIMARY KEY,
    title VARCHAR(200) NOT NULL
);

-- ============================================================
-- STEP 1: the canonical ranked day list, straight from
-- p_liturgical_subseasons.  RANK() over (season_rnk, subseason_order, wknum,
-- seq) IS the sort_order contract: it is stable as long as no subseason's
-- minwk/maxwk/minseq/maxseq changes, and a maxseq bump only shifts days that
-- sort AFTER the bumped subseason (OT/FOL is last, which is why maxseq 4->5
-- added OT-FOL-00-5 at 394 and moved nothing).
--
-- The five exclusion predicates are unchanged and are applied BEFORE ranking.
-- R1/R2 are applied AFTER ranking, in STEP 3, precisely so they do not shift
-- anyone's sort_order.
-- ============================================================
INSERT INTO tmp_day_raw (slug, season, subseason, wknum, seq, sort_order)
WITH RECURSIVE nums(mynum) AS (
    SELECT 0
    UNION ALL
    SELECT mynum + 1 FROM nums WHERE mynum < 299
),
fulllist AS (
    SELECT
        ls.season
        , ls.subseason
        , wknums.mynum   AS wknum
        , seqs.mynum     AS seq
        , ls.season_rnk
        , ls.subseason_order
    FROM p_liturgical_subseasons ls
    JOIN nums wknums
    JOIN nums seqs
      ON wknums.mynum BETWEEN ls.minwk AND ls.maxwk
     AND seqs.mynum   BETWEEN ls.minseq AND ls.maxseq
),
filtered AS (
    SELECT *
    FROM fulllist
    WHERE
        -- Third Saturday of Advent is always an O Antiphon day
        NOT (season='ADV'  AND subseason='I'        AND wknum=3 AND seq=7)
        -- Partial week before Ash Wednesday (Mon-Wed excluded)
        AND NOT (season='TQ'   AND subseason='LENT'     AND wknum=0 AND seq<4)
        -- Divine Mercy Sunday is part of the Octave, not AD_ASC  (see R2)
        AND NOT (season='PASC' AND subseason='AD_ASC'   AND wknum=2 AND seq=1)
        -- Only Divine Mercy Sunday is part of the Octave (wk2 days after seq=1 excluded)
        AND NOT (season='PASC' AND subseason='OCT'      AND wknum=2 AND seq>1)
        -- Sixth week of Easter before Ascension (in either calendar)
        AND NOT (season='PASC' AND subseason='POST_ASC' AND wknum=6 AND seq<6)
)
SELECT
    CONCAT(season, '-', subseason, '-', LPAD(wknum, 2, '0'), '-', LPAD(seq, 1, '0')),
    season, subseason, wknum, seq,
    RANK() OVER (ORDER BY season_rnk, subseason_order, wknum, seq)
FROM filtered;

-- ============================================================
-- STEP 2: week / subseason / season nodes, derived from the PRE-reconciliation
-- day list (see R2 — this is what keeps PASC-AD_ASC-02 at 107 and OT-OT-01 at
-- 152).  Week nodes now cover wknum=0 too: that is the container layer.
-- ============================================================

-- 2a: week nodes, wknum > 0
INSERT INTO tmp_epoch_gen (slug, kind, season, subseason, wknum, sort_order)
SELECT CONCAT(d.season, '-', d.subseason, '-', LPAD(d.wknum, 2, '0')),
       'week', d.season, d.subseason, d.wknum, MIN(d.sort_order)
FROM tmp_day_raw d
WHERE d.wknum > 0
GROUP BY d.season, d.subseason, d.wknum;

-- 2b: week nodes, wknum = 0 — the six CONTAINERS
INSERT INTO tmp_epoch_gen (slug, kind, season, subseason, wknum, sort_order)
SELECT CONCAT(d.season, '-', d.subseason, '-00'),
       'week', d.season, d.subseason, 0, MIN(d.sort_order)
FROM tmp_day_raw d
WHERE d.wknum = 0
GROUP BY d.season, d.subseason;

-- 2c: R2 — PASC-OCT-02 is not a real week; its only day was reslugged into
--     PASC-AD_ASC-02.  Removed after ranking so nothing else moves.
DELETE FROM tmp_epoch_gen WHERE slug = 'PASC-OCT-02';

-- 2d: subseason nodes
INSERT INTO tmp_epoch_gen (slug, kind, season, subseason, sort_order)
SELECT CONCAT(d.season, '-', d.subseason), 'subseason', d.season, d.subseason,
       MIN(d.sort_order)
FROM tmp_day_raw d
GROUP BY d.season, d.subseason;

-- 2e: season nodes
INSERT INTO tmp_epoch_gen (slug, kind, season, sort_order)
SELECT d.season, 'season', d.season, MIN(d.sort_order)
FROM tmp_day_raw d
GROUP BY d.season;

-- ============================================================
-- STEP 3: day nodes into staging, with the two set-level reconciliations.
-- title comes from p_liturgical_day_slug_overrides.title_ovr when present
-- (that table's lit_day_id IS the canonical day slug, not an FK).
-- rank_code starts as the UNDETERMINED placeholder; STEP 5 assigns it.
-- ============================================================
INSERT INTO tmp_epoch_gen (slug, kind, title, rank_code, season, subseason, wknum, seq, sort_order)
SELECT d.slug, 'day', ovr.title_ovr, 'UNDETERMINED',
       d.season, d.subseason, d.wknum, d.seq, d.sort_order
FROM tmp_day_raw d
LEFT JOIN p_liturgical_day_slug_overrides ovr ON ovr.lit_day_id = d.slug;

-- 3a: R1 — Ordinary Time week 1 has no Sunday.  Deleted after ranking, so
--     sort_order 152 stays vacant and every later day keeps its ordinal.
DELETE FROM tmp_epoch_gen WHERE slug = 'OT-OT-01-1';

-- 3b: R2 — the Paschaltide reslug.  Divine Mercy Sunday is the 2nd Sunday of
--     Easter under AD_ASC, keeping the sort_order the formula gave PASC-OCT-02-1.
UPDATE tmp_epoch_gen
SET slug      = 'PASC-AD_ASC-02-1',
    subseason = 'AD_ASC',
    title     = (SELECT title_ovr FROM p_liturgical_day_slug_overrides
                 WHERE lit_day_id = 'PASC-AD_ASC-02-1')
WHERE slug = 'PASC-OCT-02-1';

-- ============================================================
-- STEP 4: generated day titles, for days with no title_ovr.
-- Four passes, as before.  Each is guarded by title IS NULL, so an override
-- always wins.
-- ============================================================

-- Pass A: Octave / Christmas Octave day titles
UPDATE tmp_epoch_gen
SET title = CONCAT(
    ELT(seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'),
    ' within the octave of ',
    CASE season
        WHEN 'ADV'  THEN 'Advent'
        WHEN 'NAT'  THEN 'the Nativity'
        WHEN 'TQ'   THEN 'Lent'
        WHEN 'PASC' THEN 'Easter'
        WHEN 'OT'   THEN 'Ordinary Time'
    END
)
WHERE title IS NULL
  AND kind = 'day'
  AND subseason IN ('IO', 'OCT');

-- Pass B: Holy Week day titles
UPDATE tmp_epoch_gen
SET title = CONCAT(
    ELT(seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'),
    ' of Holy Week'
)
WHERE title IS NULL
  AND kind = 'day'
  AND subseason IN ('HOLYWEEK');

-- Pass C: Epiphany-season day titles (days after Epiphany, seq 1-6)
UPDATE tmp_epoch_gen
SET title = CONCAT(
    CAST(seq AS CHAR),
    CASE WHEN seq=1 THEN 'st'
         WHEN seq IN (2,22,32) THEN 'nd'
         WHEN seq IN (3,23,33) THEN 'rd'
         ELSE 'th'
    END,
    ' day after the Epiphany'
)
WHERE title IS NULL
  AND kind = 'day'
  AND subseason = 'EPI';

-- Pass D: all remaining day titles (weekly pattern — Sundays and weekdays)
UPDATE tmp_epoch_gen
SET title = CONCAT(
    CASE WHEN seq <> 1 THEN CONCAT(
         ELT(seq, 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'),
         ' of the ')
    ELSE '' END,
    CAST(wknum AS CHAR),
    CASE WHEN wknum=1 THEN 'st'
         WHEN wknum IN (2,22,32) THEN 'nd'
         WHEN wknum IN (3,23,33) THEN 'rd'
         ELSE 'th'
    END,
    ' ',
    CASE WHEN seq = 1 THEN 'Sunday' ELSE 'week' END,
    ' of ',
    CASE season
        WHEN 'ADV'  THEN 'Advent'
        WHEN 'NAT'  THEN 'the Nativity'
        WHEN 'TQ'   THEN 'Lent'
        WHEN 'PASC' THEN 'Easter'
        WHEN 'OT'   THEN 'Ordinary Time'
    END
)
WHERE title IS NULL
  AND kind = 'day';

-- ============================================================
-- STEP 5: rank_code for day nodes (17 broad rules + 12 natural-key overrides).
-- Matched by (season, subseason, wknum, seq), since lit_epoch.slug is the
-- canonical lit_day_id, not the friendly liturgical_day.slug.
-- ============================================================

-- Rule 1: Ordinary Time Sundays
UPDATE tmp_epoch_gen SET rank_code = 'SUNDAY'
WHERE kind='day' AND season='OT' AND subseason='OT' AND seq=1;

-- Rule 2: Ordinary Time weekdays (Mon-Sat)
UPDATE tmp_epoch_gen SET rank_code = 'WEEKDAY'
WHERE kind='day' AND season='OT' AND subseason='OT' AND seq<>1;

-- Rule 3: Sundays of Advent I (subseason II has no Sundays)
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='ADV' AND subseason='I' AND seq=1;

-- Rule 4: Weekdays of Advent I (before Dec 17)
UPDATE tmp_epoch_gen SET rank_code = 'WEEKDAY'
WHERE kind='day' AND season='ADV' AND subseason='I' AND seq<>1;

-- Rule 5: Privileged weekdays of Advent II (Dec 17-24, O Antiphons + Dec 24)
UPDATE tmp_epoch_gen SET rank_code = 'PRIVILEGED_WEEKDAY'
WHERE kind='day' AND season='ADV' AND subseason='II';

-- Rule 6: Sundays of Lent + Palm Sunday (TQ seq=1)
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='TQ' AND seq=1;

-- Rule 7: Lenten weekdays (incl. Ash Wed partial week wknum=0 seq 4-7)
UPDATE tmp_epoch_gen SET rank_code = 'PRIVILEGED_WEEKDAY'
WHERE kind='day' AND season='TQ' AND subseason='LENT' AND seq<>1;

-- Rule 8: Holy Week Mon-Thu
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='TQ' AND subseason='HOLYWEEK' AND seq IN (2,3,4,5);

-- Rule 9: Triduum — Good Friday + Holy Saturday
UPDATE tmp_epoch_gen SET rank_code = 'TRIDUUM'
WHERE kind='day' AND season='TQ' AND subseason='HOLYWEEK' AND seq IN (6,7);

-- Rule 10: All Easter-season Sundays (seq=1 across OCT, AD_ASC, POST_ASC, PENT)
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='PASC' AND seq=1;

-- Rule 11: Easter Octave weekdays
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='PASC' AND subseason='OCT' AND seq<>1;

-- Rule 12: Easter weekdays after the Octave (AD_ASC and POST_ASC non-Sundays)
UPDATE tmp_epoch_gen SET rank_code = 'WEEKDAY'
WHERE kind='day' AND season='PASC' AND subseason IN ('AD_ASC','POST_ASC') AND seq<>1;

-- Rule 13: Ascension of the Lord
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='PASC' AND subseason='ASC';

-- Rule 14: Pentecost Sunday
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='PASC' AND subseason='PENT';

-- Rule 15: Christmas Octave weekdays (IO seq 1-6 and OCT seq<>0)
UPDATE tmp_epoch_gen SET rank_code = 'PRIVILEGED_WEEKDAY'
WHERE kind='day' AND season='NAT' AND subseason IN ('IO','OCT') AND seq<>0;

-- Rule 16: Second Sunday of Christmas (NAT/PO seq=0)
UPDATE tmp_epoch_gen SET rank_code = 'SUNDAY'
WHERE kind='day' AND season='NAT' AND subseason='PO' AND seq=0;

-- Rule 17: Post-octave weekdays of the Nativity season (Jan 2 onward)
UPDATE tmp_epoch_gen SET rank_code = 'WEEKDAY'
WHERE kind='day' AND season='NAT' AND subseason IN ('PO','EPI') AND seq<>0;

-- Natural-key overrides (these are the WHERE slug='...' rules of
-- set-liturgical-day-ranks.sql).

-- Easter Sunday (PASC-OCT-01-1) -> TRIDUUM
UPDATE tmp_epoch_gen SET rank_code = 'TRIDUUM'
WHERE kind='day' AND season='PASC' AND subseason='OCT' AND wknum=1 AND seq=1;

-- Christmas Day (NAT-DAY-00-0) -> PRINCIPAL_TEMPORAL
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='NAT' AND subseason='DAY' AND wknum=0 AND seq=0;

-- Epiphany (NAT-EPI-00-0) -> PRINCIPAL_TEMPORAL
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='NAT' AND subseason='EPI' AND wknum=0 AND seq=0;

-- Baptism of the Lord (NAT-BAPT-01-0) -> FEAST_OF_THE_LORD
UPDATE tmp_epoch_gen SET rank_code = 'FEAST_OF_THE_LORD'
WHERE kind='day' AND season='NAT' AND subseason='BAPT' AND wknum=1 AND seq=0;

-- Holy Family (NAT-IO-01-0) -> FEAST_OF_THE_LORD
UPDATE tmp_epoch_gen SET rank_code = 'FEAST_OF_THE_LORD'
WHERE kind='day' AND season='NAT' AND subseason='IO' AND wknum=1 AND seq=0;

-- Ash Wednesday (TQ-LENT-00-4) -> PRINCIPAL_TEMPORAL
UPDATE tmp_epoch_gen SET rank_code = 'PRINCIPAL_TEMPORAL'
WHERE kind='day' AND season='TQ' AND subseason='LENT' AND wknum=0 AND seq=4;

-- Trinity Sunday (OT-FOL-00-1) -> SOLEMNITY
UPDATE tmp_epoch_gen SET rank_code = 'SOLEMNITY'
WHERE kind='day' AND season='OT' AND subseason='FOL' AND wknum=0 AND seq=1;

-- Most Holy Body and Blood of Christ (OT-FOL-00-2) -> SOLEMNITY
UPDATE tmp_epoch_gen SET rank_code = 'SOLEMNITY'
WHERE kind='day' AND season='OT' AND subseason='FOL' AND wknum=0 AND seq=2;

-- Most Sacred Heart of Jesus (OT-FOL-00-3) -> SOLEMNITY
UPDATE tmp_epoch_gen SET rank_code = 'SOLEMNITY'
WHERE kind='day' AND season='OT' AND subseason='FOL' AND wknum=0 AND seq=3;

-- Our Lord Jesus Christ, King of the Universe (OT-FOL-00-4) -> SOLEMNITY
UPDATE tmp_epoch_gen SET rank_code = 'SOLEMNITY'
WHERE kind='day' AND season='OT' AND subseason='FOL' AND wknum=0 AND seq=4;

-- R3: Immaculate Heart of the BVM (OT-FOL-00-5) -> MEMORIAL.  A Marian memorial
-- rehomed into the temporale 2026-09-16; no broad rule reaches it.
UPDATE tmp_epoch_gen SET rank_code = 'MEMORIAL'
WHERE kind='day' AND season='OT' AND subseason='FOL' AND wknum=0 AND seq=5;

-- Solemnity of Mary, Mother of God (NAT-OCT-02-0) -> SOLEMNITY
UPDATE tmp_epoch_gen SET rank_code = 'SOLEMNITY'
WHERE kind='day' AND season='NAT' AND subseason='OCT' AND wknum=2 AND seq=0;

-- ============================================================
-- STEP 6: curated season / subseason / exception-week titles.
-- These are editorial strings with no formula, so they live here as data.
-- They are applied to STAGING only; an existing live title always wins
-- (STEP 10 COALESCEs).
-- ============================================================
INSERT INTO tmp_titles (slug, title) VALUES
    ('ADV',  'Advent'),
    ('NAT',  'Christmas'),
    ('TQ',   'Lent'),
    ('PASC', 'Easter'),
    ('OT',   'Ordinary Time'),

    ('ADV-I',         'Advent'),
    ('ADV-II',        'O Antiphons'),
    ('NAT-BAPT',      'Baptism of the Lord'),
    ('NAT-DAY',       'Nativity of the Lord'),
    ('NAT-EPI',       'Epiphany'),
    ('NAT-IO',        'Octave of the Nativity'),
    ('NAT-OCT',       'Solemnity of Mary, Mother of God'),
    ('NAT-PO',        'Nativity Season after the Octave'),
    ('OT-FOL',        'Observances after Pentecost'),
    ('OT-OT',         'Ordinary Time'),
    ('PASC-AD_ASC',   'Easter - Before the Ascension'),
    ('PASC-ASC',      'Ascension of the Lord'),
    ('PASC-OCT',      'Easter - within the Octave'),
    ('PASC-PENT',     'Pentecost'),
    ('PASC-POST_ASC', 'Easter- After the Ascension'),
    ('TQ-HOLYWEEK',   'Holy Week'),
    ('TQ-LENT',       'Lent'),

    -- Two week nodes whose subseason spans exactly one week AND is a named
    -- observance rather than a stretch of the season: they take the observance
    -- name, not "Nth week of ...".  (PASC-ASC-06 and PASC-PENT-08 are also
    -- single-week but DO use the ordinal form — hence an explicit list, not a rule.)
    ('NAT-BAPT-01',   'Baptism of the Lord'),
    ('NAT-OCT-02',    'Solemnity of Mary, Mother of God');

-- 6a: apply the curated list by slug (seasons, subseasons, the 2 week exceptions)
UPDATE tmp_epoch_gen g
JOIN tmp_titles t ON t.slug = g.slug
SET g.title = t.title
WHERE g.title IS NULL;

-- 6b: container week nodes take their parent subseason's title
--     (so ADV-II-00 = 'O Antiphons', NAT-DAY-00 = 'Nativity of the Lord', …)
UPDATE tmp_epoch_gen g
JOIN tmp_titles t ON t.slug = CONCAT(g.season, '-', g.subseason)
SET g.title = t.title
WHERE g.title IS NULL
  AND g.kind = 'week'
  AND g.wknum = 0;

-- 6c: every remaining week node: "Nth week of <season>".
--     NB the season word here is 'Christmas', not the day passes' 'the Nativity'.
UPDATE tmp_epoch_gen
SET title = CONCAT(
    CAST(wknum AS CHAR),
    CASE WHEN wknum=1 THEN 'st'
         WHEN wknum IN (2,22,32) THEN 'nd'
         WHEN wknum IN (3,23,33) THEN 'rd'
         ELSE 'th'
    END,
    ' week of ',
    CASE season
        WHEN 'ADV'  THEN 'Advent'
        WHEN 'NAT'  THEN 'Christmas'
        WHEN 'TQ'   THEN 'Lent'
        WHEN 'PASC' THEN 'Easter'
        WHEN 'OT'   THEN 'Ordinary Time'
    END
)
WHERE title IS NULL
  AND kind = 'week';

-- ============================================================
-- STEP 7: saint nodes from proper_of_saints — EXISTENCE ONLY.
-- owns_columns = 0: rank_code and title on saint nodes are curated (95 of 235
-- carry a rank the source table does not agree with, and four titles have been
-- edited), so STEP 10's refresh must skip them.
-- ============================================================
INSERT INTO tmp_epoch_gen (slug, kind, title, owns_columns)
SELECT ps.slug, 'saint', MIN(ps.common_name), 0
FROM proper_of_saints ps
GROUP BY ps.slug;

-- ============================================================
-- STEP 8: the four Christmas Mass nodes under NAT-DAY-00-0.
-- These four ONLY.  The other ten kind='mass' nodes (Epiphany / Easter /
-- Ascension / Pentecost vigils, the two Holy Thursday Masses, three saint
-- vigils) are curated by the propers pipelines and are not generated here.
-- ============================================================
INSERT INTO tmp_epoch_gen (slug, kind, title, rank_code, season, subseason, wknum, seq, sort_order)
VALUES
    ('NAT-DAY-00-0-vigil',  'mass', 'Christmas — Vigil Mass',          'PRINCIPAL_TEMPORAL', 'NAT', 'DAY', 0, 0, 29),
    ('NAT-DAY-00-0-night',  'mass', 'Christmas — Mass at Night',        'PRINCIPAL_TEMPORAL', 'NAT', 'DAY', 0, 1, 29),
    ('NAT-DAY-00-0-dawn',   'mass', 'Christmas — Mass at Dawn',         'PRINCIPAL_TEMPORAL', 'NAT', 'DAY', 0, 2, 29),
    ('NAT-DAY-00-0-day',    'mass', 'Christmas — Mass During the Day',  'PRINCIPAL_TEMPORAL', 'NAT', 'DAY', 0, 3, 29);

-- ============================================================
-- STEP 9: the owned tree edges, into staging.
-- ============================================================

-- Edge A: week -> day, for EVERY day.  wknum>0 days land on their week node,
--         wknum=0 days on their container.  This replaces the old Edge A +
--         Edge B (subseason -> wknum=0 day) pair.
--         tmp_day_raw supplies the day side (MySQL cannot open one TEMPORARY
--         table twice in a single statement).  R1's deletion and R2's reslug are
--         re-applied here so the edge set stays in step with STEP 3.
INSERT INTO tmp_tree_gen (parent_slug, child_slug)
SELECT ew.slug,
       CASE WHEN d.slug = 'PASC-OCT-02-1' THEN 'PASC-AD_ASC-02-1' ELSE d.slug END
FROM tmp_day_raw d
JOIN tmp_epoch_gen ew
  ON ew.kind = 'week'
 AND ew.season = d.season
 AND ew.subseason = CASE WHEN d.slug = 'PASC-OCT-02-1' THEN 'AD_ASC' ELSE d.subseason END
 AND ew.wknum = d.wknum
WHERE d.slug <> 'OT-OT-01-1';

-- Edge B: subseason -> week (covers containers, since they are week nodes)
INSERT INTO tmp_tree_gen (parent_slug, child_slug)
SELECT CONCAT(ew.season, '-', ew.subseason), ew.slug
FROM tmp_epoch_gen ew
WHERE ew.kind = 'week';

-- Edge C: season -> subseason
INSERT INTO tmp_tree_gen (parent_slug, child_slug)
SELECT ess.season, ess.slug
FROM tmp_epoch_gen ess
WHERE ess.kind = 'subseason';

-- Edge D: Christmas Day -> its four Mass nodes (named explicitly — the old
--         "WHERE kind='mass'" form would now mis-parent all fourteen).
INSERT INTO tmp_tree_gen (parent_slug, child_slug) VALUES
    ('NAT-DAY-00-0', 'NAT-DAY-00-0-vigil'),
    ('NAT-DAY-00-0', 'NAT-DAY-00-0-night'),
    ('NAT-DAY-00-0', 'NAT-DAY-00-0-dawn'),
    ('NAT-DAY-00-0', 'NAT-DAY-00-0-day');

-- ============================================================
-- STEP 10: upsert lit_epoch.  Two statements, both no-ops on a current DB.
-- MySQL does not rewrite a row whose values are unchanged, so updated_at is
-- left alone too.
-- ============================================================
START TRANSACTION;

INSERT INTO lit_epoch (slug, kind, title, rank_code, season, subseason, wknum, seq, sort_order)
SELECT g.slug, g.kind, g.title, g.rank_code, g.season, g.subseason, g.wknum, g.seq, g.sort_order
FROM tmp_epoch_gen g
LEFT JOIN lit_epoch e ON e.slug = g.slug
WHERE e.slug IS NULL;

UPDATE lit_epoch e
JOIN tmp_epoch_gen g ON g.slug = e.slug
SET e.kind       = g.kind,
    e.rank_code  = g.rank_code,
    e.season     = g.season,
    e.subseason  = g.subseason,
    e.wknum      = g.wknum,
    e.seq        = g.seq,
    e.sort_order = g.sort_order,
    e.title      = COALESCE(e.title, g.title)
WHERE g.owns_columns = 1;

-- title-only backfill for the existence-only kinds (saint): never touches
-- rank_code or any structural column.
UPDATE lit_epoch e
JOIN tmp_epoch_gen g ON g.slug = e.slug
SET e.title = g.title
WHERE g.owns_columns = 0
  AND e.title IS NULL;

-- ============================================================
-- STEP 11: sync the owned tree edges.  Insert what is missing, then prune
-- stale edges WITHIN THE OWNED CLASSES ONLY.
-- ============================================================
INSERT INTO lit_epoch_tree (parent_slug, child_slug)
SELECT g.parent_slug, g.child_slug
FROM tmp_tree_gen g
LEFT JOIN lit_epoch_tree t
       ON t.parent_slug = g.parent_slug AND t.child_slug = g.child_slug
WHERE t.parent_slug IS NULL;

DELETE t
FROM lit_epoch_tree t
JOIN lit_epoch p ON p.slug = t.parent_slug
JOIN lit_epoch c ON c.slug = t.child_slug
LEFT JOIN tmp_tree_gen g
       ON g.parent_slug = t.parent_slug AND g.child_slug = t.child_slug
WHERE g.parent_slug IS NULL
  AND (
        (p.kind = 'season'    AND c.kind = 'subseason')
     OR (p.kind = 'subseason' AND c.kind IN ('week', 'day'))
     OR (p.kind = 'week'      AND c.kind = 'day')
     OR (t.parent_slug = 'NAT-DAY-00-0' AND c.kind = 'mass')
  );

COMMIT;

-- ============================================================
-- STEP 12: report.  Stale owned nodes are listed, never deleted — see the
-- OWNERSHIP BOUNDARY note.  An empty result here is the healthy state.
-- ============================================================
SELECT 'STALE OWNED NODE — review by hand, NOT deleted' AS warning,
       e.slug, e.kind, e.sort_order,
       (SELECT COUNT(*) FROM lit_part_sources l WHERE l.lit_epoch_slug = e.slug) AS lps_rows
FROM lit_epoch e
LEFT JOIN tmp_epoch_gen g ON g.slug = e.slug
WHERE g.slug IS NULL
  AND e.kind IN ('day', 'week', 'subseason', 'season', 'saint')
ORDER BY e.kind, e.slug;

SELECT kind, COUNT(*) AS n_generated FROM tmp_epoch_gen GROUP BY kind ORDER BY kind;

SELECT COUNT(*) AS n_generated_edges FROM tmp_tree_gen;

SELECT e.kind, COUNT(*) AS live_nodes FROM lit_epoch e GROUP BY e.kind ORDER BY e.kind;

SELECT COUNT(*) AS live_edges FROM lit_epoch_tree;

DROP TEMPORARY TABLE IF EXISTS tmp_day_raw;
DROP TEMPORARY TABLE IF EXISTS tmp_epoch_gen;
DROP TEMPORARY TABLE IF EXISTS tmp_tree_gen;
DROP TEMPORARY TABLE IF EXISTS tmp_titles;
