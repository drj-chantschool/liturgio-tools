-- make-lit-observance-resolved.sql
-- Materialization table for the liturgical calendar resolver.
-- Stores the fully-resolved observance for every date × jurisdiction,
-- after precedence ranking and forward-transfer of impeded solemnities.
--
-- Apply once:
--   SOURCE sql/make-lit-observance-resolved.sql;
-- Re-population is handled by liturgio_tools.liturgical_calendar.resolver.materialize_year(),
-- which is idempotent (deletes existing rows for the year before reinserting).

CREATE TABLE IF NOT EXISTS lit_observance_resolved (
    -- Scope key
    jurisdiction   VARCHAR(64)  NOT NULL,
    dt             DATE         NOT NULL,

    -- The observance: either the temporal epoch slug (lit_day_id from proper_of_seasons)
    -- or a saint slug (from proper_of_saints / lit_epoch).
    epoch_slug     VARCHAR(64)  NOT NULL,

    -- Rank in effect on this date (may differ from the saint's nominal rank when
    -- a local solemnity elevation has been applied).
    rank_code      VARCHAR(40)  NOT NULL,

    -- Liturgical role for this (jurisdiction, dt, epoch_slug) triple:
    --   celebrated    - the principal observance of the day
    --   commemoration - a sanctoral day that is acknowledged but not celebrated
    --   optional      - an optional memorial available on a feria
    --   omitted       - a non-transferable saint outranked on its nominal date (no make-up)
    role           VARCHAR(20)  NOT NULL,

    -- Transfer flag: 1 when this observance was moved forward from its nominal date.
    is_transferred TINYINT      NOT NULL DEFAULT 0,

    -- For transferred observances, the civil date on which they were originally
    -- assigned in the sanctoral calendar (NULL when is_transferred = 0).
    nominal_dt     DATE         NULL,

    -- Free-text notes (rubrical simplifications, debug info, etc.).
    notes          VARCHAR(500) NULL,

    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (jurisdiction, dt, epoch_slug),

    -- Fast lookup of the single celebrated observance per (jurisdiction, dt).
    KEY idx_obsres_celebrated (jurisdiction, dt, role),

    -- Referential integrity: epoch_slug must exist in lit_epoch (covers both
    -- temporal day slugs and saint slugs, which are both stored there).
    CONSTRAINT fk_obsres_epoch
        FOREIGN KEY (epoch_slug)
        REFERENCES lit_epoch(slug)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    -- rank_code must exist in p_lit_rank.
    CONSTRAINT fk_obsres_rank
        FOREIGN KEY (rank_code)
        REFERENCES p_lit_rank(rank_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);
