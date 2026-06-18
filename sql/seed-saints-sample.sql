-- =============================================================================
-- seed-saints-sample.sql
-- SAMPLE / TEST data for resolver development.
-- Real saints are populated lazily during Graduale Romanum OCR.
--
-- Idempotent: uses INSERT ... ON DUPLICATE KEY UPDATE so safe to re-run.
-- PK is (jurisdiction, slug).
-- =============================================================================

INSERT INTO proper_of_saints
    (jurisdiction, slug, common_name, rank_code, month_nominal, day_nominal)
VALUES
    ('UNIVERSAL', 'st-joseph',             'Saint Joseph, Spouse of the Blessed Virgin Mary',            'SOLEMNITY', 3,  19),
    ('UNIVERSAL', 'annunciation',          'The Annunciation of the Lord',                                'SOLEMNITY', 3,  25),
    ('UNIVERSAL', 'st-mark',               'Saint Mark, Evangelist',                                      'FEAST',     4,  25),
    ('UNIVERSAL', 'nativity-john-baptist', 'The Nativity of Saint John the Baptist',                      'SOLEMNITY', 6,  24),
    ('UNIVERSAL', 'ss-peter-paul',         'Saints Peter and Paul, Apostles',                             'SOLEMNITY', 6,  29),
    ('UNIVERSAL', 'assumption',            'The Assumption of the Blessed Virgin Mary',                   'SOLEMNITY', 8,  15),
    ('UNIVERSAL', 'all-saints',            'All Saints',                                                  'SOLEMNITY', 11,  1),
    ('UNIVERSAL', 'immaculate-conception', 'The Immaculate Conception of the Blessed Virgin Mary',        'SOLEMNITY', 12,  8)
ON DUPLICATE KEY UPDATE
    common_name    = VALUES(common_name),
    rank_code      = VALUES(rank_code),
    month_nominal  = VALUES(month_nominal),
    day_nominal    = VALUES(day_nominal);
