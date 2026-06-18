-- =============================================================================
-- set-liturgical-day-ranks.sql
-- Assign precedence ranks to all rows in liturgical_day.
--
-- Run AFTER make-liturgical-day.sql (which resets all rows to 'UNDETERMINED').
-- Safe to re-run: every statement is a plain UPDATE with no side effects beyond
-- setting lit_rank; idempotent by design.
--
-- Build order:
--   1. make_param_tables.sql          (p_lit_rank, p_liturgical_day_slug_overrides, etc.)
--   2. make-liturgical-day.sql        (populates liturgical_day; all rows = UNDETERMINED)
--   3. set-liturgical-day-ranks.sql   (THIS FILE)
--
-- Reconciliation notes (STEP A grounding against real data, 2026-06-16):
--
--   ADV subseason 'II'   — seq values 1-7 are the O Antiphons (Dec 17-23),
--                          seq=8 is DEC24.  NONE are Sundays.  All rows are
--                          PRIVILEGED_WEEKDAY (rule 5).  The ADV-I seq=1 rule
--                          is limited to subseason='I' to avoid mis-firing here.
--
--   NAT subseasons       — DAY=Christmas, IO=Dec 26-31 (seq 0=HolyFamily feast,
--                          seq 1-6 are weekdays), OCT=Jan 1 (MOTHEROFGOD only,
--                          seq=0), PO=Jan 2-7 (seq 0=2nd Sunday of Christmas,
--                          seq 1-6 are weekdays), EPI=days after Epiphany
--                          (seq 0=Epiphany feast, seq 1-6 are weekdays),
--                          BAPT=Baptism of the Lord (seq=0 only).
--                          Rule 16 ("NAT Sundays → SUNDAY") applies only to
--                          NAT-PO subseason where seq=0 is the 2nd Sunday.
--
--   TQ LENT wknum=0      — Ash Wednesday (seq=4) + Thu/Fri/Sat after Ash
--                          Wednesday (seq 5-7).  These are the partial opening
--                          week.  wknum=0 is not a Sunday—it is the partial
--                          week marker. All covered by "TQ LENT seq<>1 →
--                          PRIVILEGED_WEEKDAY" since wknum 1+ Sundays have seq=1.
--
--   TQ HOLYWEEK          — All rows have wknum=0.  seq values: 1=PALM, 2=Mon,
--                          3=Tue, 4=Wed, 5=HOLYTHUR, 6=GOODFRI, 7=HOLYSAT.
--
--   PASC POST_ASC        — wknum=6 has seq 6-7 (Fri/Sat after Ascension).
--                          wknum=7 has seq 1-7 (7th Sunday and its week).
--                          seq=1 rows in POST_ASC are Easter weekday Sundays
--                          and are caught by the broad PASC seq=1 rule (rule 10).
--
--   OT FOL               — TRINITY/CORPUS/SACREDHEART/CHRISTTHEKING are
--                          stored with wknum=0, seq=1..4 (not day-of-week).
--                          They are Solemnities assigned via slug override below;
--                          they should NOT match the "OT seq=1 → SUNDAY" rule.
--                          That rule is scoped to subseason='OT' only.
--
--   MOTHEROFGOD (Jan 1)  — slug='MOTHEROFGOD', lit_day_id='NAT-OCT-02-0'.
--                          Identified confidently; set to SOLEMNITY below.
--
--   PASCOCT (Divine Mercy Sunday, 2nd Sunday of Easter) — slug='PASCOCT',
--                          lit_day_id='PASC-OCT-02-1', wknum=2 seq=1.
--                          Caught by rule 10 (PASC seq=1 → PRINCIPAL_TEMPORAL).
--                          No override needed beyond that; Divine Mercy Sunday
--                          ranks at PRINCIPAL_TEMPORAL in the temporal calendar.
-- =============================================================================

START TRANSACTION;

-- ---------------------------------------------------------------------------
-- BROAD CATEGORY RULES
-- Applied in least-specific to most-specific order so later rules can override.
-- ---------------------------------------------------------------------------

-- Rule 1: Ordinary Time Sundays
UPDATE liturgical_day
   SET lit_rank = 'SUNDAY'
 WHERE season = 'OT'
   AND subseason = 'OT'
   AND seq = 1;

-- Rule 2: Ordinary Time weekdays (Mon–Sat)
UPDATE liturgical_day
   SET lit_rank = 'WEEKDAY'
 WHERE season = 'OT'
   AND subseason = 'OT'
   AND seq <> 1;

-- Rule 3: Sundays of Advent (subseason I only — subseason II has no Sundays)
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'ADV'
   AND subseason = 'I'
   AND seq = 1;

-- Rule 4: Weekdays of Advent I (before Dec 17)
UPDATE liturgical_day
   SET lit_rank = 'WEEKDAY'
 WHERE season = 'ADV'
   AND subseason = 'I'
   AND seq <> 1;

-- Rule 5: Privileged weekdays of Advent II (Dec 17–24, O Antiphons + Dec 24)
--   seq values 1–7 = O Antiphons (Dec 17–23), seq=8 = DEC24.
--   None are Sundays; all are PRIVILEGED_WEEKDAY.
UPDATE liturgical_day
   SET lit_rank = 'PRIVILEGED_WEEKDAY'
 WHERE season = 'ADV'
   AND subseason = 'II';

-- Rule 6: Sundays of Lent (TQ/LENT seq=1) + Palm Sunday (TQ/HOLYWEEK seq=1)
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'TQ'
   AND seq = 1;

-- Rule 7: Lenten weekdays (TQ LENT, all non-Sundays including Ash Wed partial week)
--   wknum=0, seq 4-7: Ash Wednesday + Thu/Fri/Sat after Ash Wednesday.
--   wknum 1-6, seq 2-7: Mon–Sat of Lenten weeks.
UPDATE liturgical_day
   SET lit_rank = 'PRIVILEGED_WEEKDAY'
 WHERE season = 'TQ'
   AND subseason = 'LENT'
   AND seq <> 1;

-- Rule 8: Holy Week Mon–Thu (PRINCIPAL_TEMPORAL)
--   seq 2=Mon, 3=Tue, 4=Wed, 5=Holy Thursday (Chrism/Maundy)
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'TQ'
   AND subseason = 'HOLYWEEK'
   AND seq IN (2, 3, 4, 5);

-- Rule 9: Triduum — Good Friday and Holy Saturday
--   seq 6=Good Friday, 7=Holy Saturday (Easter Sunday override follows below)
UPDATE liturgical_day
   SET lit_rank = 'TRIDUUM'
 WHERE season = 'TQ'
   AND subseason = 'HOLYWEEK'
   AND seq IN (6, 7);

-- Rule 10: All Easter-season Sundays (seq=1 across OCT, AD_ASC, POST_ASC, PENT)
--   Includes PASCSUN (Easter Sunday, overridden to TRIDUUM below),
--   PASCOCT (Divine Mercy Sunday), and Sundays of weeks 3–7.
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'PASC'
   AND seq = 1;

-- Rule 11: Easter Octave weekdays (subseason=OCT, non-Sunday)
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'PASC'
   AND subseason = 'OCT'
   AND seq <> 1;

-- Rule 12: Easter weekdays after the Octave (AD_ASC and POST_ASC non-Sundays)
UPDATE liturgical_day
   SET lit_rank = 'WEEKDAY'
 WHERE season = 'PASC'
   AND subseason IN ('AD_ASC', 'POST_ASC')
   AND seq <> 1;

-- Rule 13: Ascension of the Lord (Thursday of week 6 of Easter)
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'PASC'
   AND subseason = 'ASC';

-- Rule 14: Pentecost Sunday
UPDATE liturgical_day
   SET lit_rank = 'PRINCIPAL_TEMPORAL'
 WHERE season = 'PASC'
   AND subseason = 'PENT';

-- Rule 15: Christmas Octave weekdays (NAT/IO and NAT/OCT, non-feast-day rows)
--   IO seq 1-6 = Dec 26-31 weekdays.
--   OCT has only MOTHEROFGOD (seq=0) which gets a SOLEMNITY override below.
UPDATE liturgical_day
   SET lit_rank = 'PRIVILEGED_WEEKDAY'
 WHERE season = 'NAT'
   AND subseason IN ('IO', 'OCT')
   AND seq <> 0;

-- Rule 16: Second Sunday of Christmas (NAT/PO seq=0)
--   "Second Sunday of Christmas" is stored as subseason=PO, seq=0.
--   There is no other NAT Sunday in the data.
UPDATE liturgical_day
   SET lit_rank = 'SUNDAY'
 WHERE season = 'NAT'
   AND subseason = 'PO'
   AND seq = 0;

-- Rule 17: Post-octave weekdays of the Nativity season (Jan 2 onward, before Epiphany)
--   Covers NAT/PO weekdays (Jan 2-7, seq 1-6) and NAT/EPI days after Epiphany
--   (seq 1-6 = days between Epiphany and Baptism of the Lord).
UPDATE liturgical_day
   SET lit_rank = 'WEEKDAY'
 WHERE season = 'NAT'
   AND subseason IN ('PO', 'EPI')
   AND seq <> 0;


-- ---------------------------------------------------------------------------
-- SPECIFIC SLUG OVERRIDES
-- These win over all broad rules above.
-- ---------------------------------------------------------------------------

-- Easter Sunday → TRIDUUM (highest rank; overrides PRINCIPAL_TEMPORAL from rule 10)
UPDATE liturgical_day SET lit_rank = 'TRIDUUM'          WHERE slug = 'PASCSUN';

-- Christmas Day
UPDATE liturgical_day SET lit_rank = 'PRINCIPAL_TEMPORAL' WHERE slug = 'NATIVITY';

-- Epiphany of the Lord (seq=0, already left UNDETERMINED by rules — safe either way)
UPDATE liturgical_day SET lit_rank = 'PRINCIPAL_TEMPORAL' WHERE slug = 'EPIPHANY';

-- Baptism of the Lord
UPDATE liturgical_day SET lit_rank = 'FEAST_OF_THE_LORD' WHERE slug = 'BAPTOFLORD';

-- Holy Family of Jesus, Mary, and Joseph
UPDATE liturgical_day SET lit_rank = 'FEAST_OF_THE_LORD' WHERE slug = 'HOLYFAMILY';

-- Ash Wednesday
UPDATE liturgical_day SET lit_rank = 'PRINCIPAL_TEMPORAL' WHERE slug = 'CINERUM';

-- Trinity Sunday (OT-FOL-00-1)
UPDATE liturgical_day SET lit_rank = 'SOLEMNITY'         WHERE slug = 'TRINITY';

-- Most Holy Body and Blood of Christ
UPDATE liturgical_day SET lit_rank = 'SOLEMNITY'         WHERE slug = 'CORPUS';

-- Most Sacred Heart of Jesus
UPDATE liturgical_day SET lit_rank = 'SOLEMNITY'         WHERE slug = 'SACREDHEART';

-- Our Lord Jesus Christ, King of the Universe
UPDATE liturgical_day SET lit_rank = 'SOLEMNITY'         WHERE slug = 'CHRISTTHEKING';

-- Solemnity of Mary, Mother of God (Jan 1, NAT-OCT-02-0)
--   Identified confidently as slug='MOTHEROFGOD'.
UPDATE liturgical_day SET lit_rank = 'SOLEMNITY'         WHERE slug = 'MOTHEROFGOD';

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification query (informational; comment out if running non-interactively)
-- ---------------------------------------------------------------------------
-- SELECT lit_rank, COUNT(*) AS n
--   FROM liturgical_day
--  GROUP BY lit_rank
--  ORDER BY (SELECT sort_order FROM p_lit_rank p WHERE p.rank_code = lit_rank);
