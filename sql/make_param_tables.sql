drop table if exists p_translation_source;
drop table if exists p_assignment_authority;
drop table if exists p_lit_rank;

-- ============================================================
-- Lookup table for assignment authority
-- ============================================================
CREATE TABLE p_assignment_authority (
    authority_code VARCHAR(20) NOT NULL PRIMARY KEY,   -- e.g. 'GRADUALE', 'MISSAL'
    display_name   VARCHAR(80) NOT NULL,               -- e.g. 'Graduale Romanum'
    sort_order     SMALLINT UNSIGNED NOT NULL DEFAULT 100,
    is_active      TINYINT NOT NULL DEFAULT 1,
    notes          VARCHAR(500) NULL
);

-- Seed common values (edit freely later)
INSERT INTO p_assignment_authority (authority_code, display_name, sort_order) VALUES
('GRADUALE', 'Graduale Romanum', 10),
('MISSAL',   'Roman Missal',     20),
('LOTH1',    'Liturgy of the Hours, first edition',     30),
('LOTH2',    'Liturgy of the Hours, second edition', 40),
('OCO',      'Ordo Cantus Officii 2015', 60),
('CUSTOM',   'Custom',           90);


-- ============================================================
-- translation_source (lookup)
-- ============================================================
CREATE TABLE p_translation_source (
    translation_source_code VARCHAR(40) NOT NULL PRIMARY KEY,
    display_name            VARCHAR(200) NOT NULL,
    sort_order              SMALLINT UNSIGNED NOT NULL DEFAULT 100,
    is_active               TINYINT NOT NULL DEFAULT 1,
    notes                   VARCHAR(500) NULL
);

-- Preload requested sources
INSERT INTO p_translation_source (translation_source_code, display_name, sort_order) VALUES
('GREGORIAN_MISSAL',        'Gregorian Missal',                        10),
('ROMAN_MISSAL_2010_ICEL',  'Roman Missal (2010 ICEL)',                20),
('ABBEY_PSALMS_CANTICLES',  'Abbey Psalms and Canticles',              30),
('NEW_AMERICAN_BIBLE',      'New American Bible',                      40),
('LOTH_1975_ICEL',          'Liturgy of the Hours (1975 ICEL)',        50),
('LITURGIA_HORARUM_1985',   'Liturgia Horarum (1985 Editio Typica)',   60);


-- ============================================================
-- p_lit_rank  —  Table of Liturgical Days (1969 General Norms)
-- ============================================================
-- 13 precedence levels of the Roman Rite per the 1969 "General Norms for
-- the Liturgical Year and the Calendar" (GNLYC) §§ 59–63, plus a sentinel
-- for rows not yet assigned.
--
-- can_be_transferred / can_be_impeded flags are best-effort interpretations
-- of the GNLYC rules and are themselves subject to review; NULL = undetermined.
--   can_be_transferred  1 = may be moved to another day when impeded
--   can_be_impeded      0 = always takes precedence (cannot be displaced)
--                       1 = may be impeded by a higher-ranked day
CREATE TABLE p_lit_rank (
    rank_code           VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name        VARCHAR(255) NOT NULL,
    sort_order          SMALLINT UNSIGNED NOT NULL,   -- lower = higher precedence
    can_be_transferred  TINYINT NULL,                 -- 1=transferred when impeded; NULL=undetermined
    can_be_impeded      TINYINT NULL,                 -- 0=always takes precedence; NULL=undetermined
    is_active           TINYINT NOT NULL DEFAULT 1,
    notes               VARCHAR(500) NULL
);

INSERT INTO p_lit_rank (rank_code, display_name, sort_order, can_be_transferred, can_be_impeded) VALUES
('TRIDUUM',              'Easter Triduum',                                                                                                                                                                                    10,   0,    0),
('PRINCIPAL_TEMPORAL',   'Principal temporal day / solemnity of the Lord (Nativity, Epiphany, Ascension, Pentecost; Sundays of Advent, Lent, Easter; Ash Wednesday; weekdays of Holy Week; days within the Octave of Easter)', 20,   0,    0),
('SOLEMNITY',            'Solemnity (General Calendar) / All Souls',                                                                                                                                                          30,   1,    1),
('PROPER_SOLEMNITY',     'Proper solemnity (patron, dedication, title, founder)',                                                                                                                                              40,   1,    1),
('FEAST_OF_THE_LORD',    'Feast of the Lord (General Calendar)',                                                                                                                                                               50,   0,    1),
('SUNDAY',               'Sunday of Christmas season or Ordinary Time',                                                                                                                                                        60,   0,    1),
('FEAST',                'Feast (General Calendar)',                                                                                                                                                                            70,   0,    1),
('PROPER_FEAST',         'Proper feast',                                                                                                                                                                                        80,   0,    1),
('PRIVILEGED_WEEKDAY',   'Privileged weekday (Advent Dec 17-24; Octave of the Nativity; weekdays of Lent)',                                                                                                                    90,   0,    1),
('MEMORIAL',             'Obligatory memorial (General Calendar)',                                                                                                                                                             100,   0,    1),
('PROPER_MEMORIAL',      'Proper obligatory memorial',                                                                                                                                                                        110,   0,    1),
('OPTIONAL_MEMORIAL',    'Optional memorial',                                                                                                                                                                                  130,   0,    1),
('WEEKDAY',              'Weekday / feria',                                                                                                                                                                                    120,   0,    1),
('UNDETERMINED',         'Undetermined — pending review',                                                                                                                                                                     9000, NULL, NULL);

-- Intentional divergence from canonical GNLYC order (optional memorial #12, feria #13):
-- WEEKDAY is ranked above OPTIONAL_MEMORIAL so the resolver defaults to the feria
-- and offers the optional memorial as an alternative, not the other way around.
UPDATE p_lit_rank
SET notes = 'Intentionally ranked below WEEKDAY for resolver default-selection: optional memorials are offered as an alternative to the feria, not as the default. Diverges from canonical GNLYC order (optional memorial #12, feria #13).'
WHERE rank_code = 'OPTIONAL_MEMORIAL';

UPDATE p_lit_rank
SET notes = 'Ranked above OPTIONAL_MEMORIAL (sort_order 120 vs 130): resolver defaults to the feria and offers any applicable optional memorial as an alternative. Intentional divergence from canonical GNLYC order (feria #13, optional memorial #12).'
WHERE rank_code = 'WEEKDAY';

