drop table if exists p_translation_source;
drop table if exists p_assignment_authority;

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

