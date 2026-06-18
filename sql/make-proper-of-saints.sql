drop table if exists proper_of_saints;

-- ============================================================
-- proper_of_saints
-- Calendar of saints: nominal (un-transferred) dates of observance,
-- scoped by jurisdiction, with a precedence rank.
-- slug identifies the saint/feast CONCEPT and is intended as the
-- join key for liturgical texts (lit_part_sources);
-- a hard FK from texts will be added in a later step.
-- Populated lazily during Graduale Romanum OCR.
-- ============================================================
CREATE TABLE proper_of_saints (
    jurisdiction   VARCHAR(64)  NOT NULL,            -- 'UNIVERSAL', 'US', etc. (same domain as proper_of_seasons.jurisdiction)
    slug           VARCHAR(64)  NOT NULL,            -- stable saint/feast identifier, e.g. 'st-mark'
    common_name    VARCHAR(200) NOT NULL,            -- display name, e.g. 'Saint Mark, Evangelist'
    rank_code      VARCHAR(40)  NOT NULL DEFAULT 'UNDETERMINED',  -- FK -> p_lit_rank.rank_code; defaults to review queue
    month_nominal  TINYINT UNSIGNED NULL,            -- 1..12 nominal month of observance
    day_nominal    TINYINT UNSIGNED NULL,            -- 1..31 nominal day of observance
    common_of      VARCHAR(200) NULL,                -- e.g. 'Common of Martyrs' when the saint has no proper texts
    notes          VARCHAR(500) NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (jurisdiction, slug),

    KEY idx_saints_slug (slug),
    KEY idx_saints_date (jurisdiction, month_nominal, day_nominal),
    KEY idx_saints_rank (rank_code),

    CONSTRAINT fk_saints_rank
        FOREIGN KEY (rank_code)
        REFERENCES p_lit_rank(rank_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT chk_saints_month
        CHECK (month_nominal IS NULL OR (month_nominal BETWEEN 1 AND 12)),
    CONSTRAINT chk_saints_day
        CHECK (day_nominal IS NULL OR (day_nominal BETWEEN 1 AND 31))
);
