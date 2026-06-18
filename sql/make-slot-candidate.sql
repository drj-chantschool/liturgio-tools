drop table if exists gregobase_chant_group_map;
drop table if exists lit_part_assignment;
drop table if exists service_part;
drop table if exists local_chants;
drop table if exists chant_group;

-- ============================================================
-- chant_group
-- ============================================================
CREATE TABLE chant_group (
    chant_group_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Stable human-facing identity for the chant concept
    canonical_name VARCHAR(200) NOT NULL,   -- e.g. "Prope es tu (Entrance/Introit)"
    incipit        VARCHAR(200) NULL,

    -- Optional metadata for grouping/searching
    text_key VARCHAR(255) NULL,             -- normalized text/incipit key (if you maintain one)
    mode     VARCHAR(10) NULL,
    notes    VARCHAR(500) NULL,

    PRIMARY KEY (chant_group_id),

    UNIQUE KEY uq_chant_group_name (canonical_name),
    KEY idx_chant_group_text_key (text_key)
);



-- ============================================================
-- local_chants (extended)
-- ============================================================
CREATE TABLE local_chants (
    local_chant_id CHAR(36) NOT NULL,              -- UUID (text form)
    chant_group_id BIGINT UNSIGNED NOT NULL,        -- FK to chant_group

    -- Your "version name" (default 'english', but allow 'english_alt', etc.)
    version VARCHAR(40) NOT NULL DEFAULT 'english',

    -- Gregobase-like metadata (for parity / filtering)
    incipit     VARCHAR(256) NULL,
    `office-part` VARCHAR(16) NULL,                 -- keep same naming as gregobase for compatibility
    mode        VARCHAR(8) NULL,
    mode_var    VARCHAR(16) NULL,
    transcriber VARCHAR(128) NOT NULL DEFAULT 'Doctor J',
    commentary  VARCHAR(256) NULL,

    -- The chant encoding you store (gabc for now)
    notation VARCHAR(10) NOT NULL DEFAULT 'gabc',
    gabc     LONGTEXT NOT NULL,

    -- Provenance of the translation text used
    translation_source_code VARCHAR(40) NULL,

    -- 1 = verbatim as it appears in the source; 0 = adapted/modified
    is_text_exact TINYINT UNSIGNED NOT NULL DEFAULT 1,

    -- Optional: link back to what you started from (gregobase:123 / local:<uuid>)
    derived_from_uid VARCHAR(80) NULL,

    -- Optional: workflow/status (draft/reviewed/published)
    status VARCHAR(20) NOT NULL DEFAULT 'draft',

    notes VARCHAR(500) NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (local_chant_id),

    KEY idx_local_chants_group (chant_group_id),
    KEY idx_local_chants_version (version),
    KEY idx_local_chants_office_part (`office-part`),
    KEY idx_local_chants_mode (mode),
    KEY idx_local_chants_status (status),
    KEY idx_local_chants_translation_source (translation_source_code),

    CONSTRAINT fk_local_chants_group
        FOREIGN KEY (chant_group_id)
        REFERENCES chant_group(chant_group_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_local_chants_translation_source
        FOREIGN KEY (translation_source_code)
        REFERENCES translation_source(translation_source_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);


-- ============================================================
-- service_part (updated)
-- Catalog of "parts" (slots) that exist for a given service/office,
-- with ordering. No seq/wkday preference logic lives here anymore.
-- ============================================================
CREATE TABLE service_part (
    part_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    service_code  VARCHAR(20) NOT NULL,   -- e.g. MASS, VESPERS
    part_code     VARCHAR(30) NOT NULL,   -- e.g. INTROIT, MAGNIFICAT_ANT, ANT1

    display_name  VARCHAR(120) NULL,      -- optional per-service override label
    display_order SMALLINT UNSIGNED NOT NULL,

    is_required   TINYINT UNSIGNED NOT NULL DEFAULT 1,
    notes         VARCHAR(500) NULL,

    PRIMARY KEY (part_id),

    -- This is what makes it a "lookup" for assignments
    UNIQUE KEY uq_service_part (service_code, part_code),

    KEY idx_service_part_service (service_code),
    KEY idx_service_part_code (part_code)
);



-- ============================================================
-- lit_part_assignment (phase 4b: migrated to lit_epoch_slug)
--
-- DEPENDS ON: lit_epoch must be created first (run make-lit-epoch.sql
-- before this file in the build order).
--
-- lit_epoch_slug is a FK into lit_epoch(slug) and replaces the former
-- granular columns season / subseason / wknum / seq.  NULL means the row
-- applies at all epochs (psalter-fallback / pure-cycle rows).
--
-- wknum_mod_4 and wknum_mod_2 are "last-resort" psalter selectors.
-- Desired intent: they should only be used when lit_epoch_slug is NULL.
-- NOTE: MySQL error 3823 prevents a CHECK constraint from referencing a
-- column that participates in a FK referential action, so the constraint
--   CHECK ((wknum_mod_4 IS NULL AND wknum_mod_2 IS NULL) OR lit_epoch_slug IS NULL)
-- cannot be stored.  This invariant is enforced at the application layer.
-- ============================================================
CREATE TABLE lit_part_assignment (
    assignment_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    jurisdiction  VARCHAR(64) NOT NULL,      -- UNIVERSAL, US, etc.
    part_id       BIGINT UNSIGNED NOT NULL,  -- FK -> service_part.part_id

    -- Liturgical epoch reference (replaces season/subseason/wknum/seq)
    lit_epoch_slug VARCHAR(64) NULL,         -- FK -> lit_epoch(slug); NULL = all epochs

    -- Psalter fallback selectors (last resort; only meaningful when lit_epoch_slug IS NULL)
    wknum_mod_4   TINYINT UNSIGNED NULL,     -- 0..3
    wknum_mod_2   TINYINT UNSIGNED NULL,     -- 0..1

    -- Lowest-level selectors
    wkday         TINYINT UNSIGNED NULL,     -- 1..7 (Sunday=1)

    -- Optional lectionary cycle selectors (wildcards via NULL)
    cycle_wk      TINYINT UNSIGNED NULL,     -- 0/1
    cycle_sun     TINYINT UNSIGNED NULL,     -- 0/1/2

    -- Assignment target (your chant concept)
    chant_group_id BIGINT UNSIGNED NOT NULL,

    -- Why/authority for this assignment (nullable)
    assignment_authority_code VARCHAR(20) NULL,

    -- 1=primary/GR-default, 2=first alternate, etc.
    -- Allows multiple valid chant options per slot (e.g. GR proper + 'or' option).
    -- Part of the unique key so each (slot, option_num) is distinct.
    -- Added step 6b (2026-06-17); migrated via scripts/migrate_step6b_option_num.py.
    option_num    TINYINT UNSIGNED NOT NULL DEFAULT 1,

    notes         VARCHAR(500) NULL,

    -- 1 = in the review queue / not yet human-reviewed (safe default for
    -- script/LLM/untrusted-created assignments); 0 = human-reviewed
    needs_review  TINYINT NOT NULL DEFAULT 1,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (assignment_id),

    -- Unique per (slot, option_num): primary and alternates can coexist
    UNIQUE KEY uq_lit_part_assignment_v2 (
        jurisdiction,
        part_id,
        lit_epoch_slug,
        wkday,
        cycle_wk,
        cycle_sun,
        wknum_mod_4,
        wknum_mod_2,
        option_num
    ),

    KEY idx_lpa_lookup (jurisdiction, part_id),
    KEY idx_lpa_group (chant_group_id),
    KEY idx_lpa_authority (assignment_authority_code),
    KEY idx_lpa_needs_review (needs_review),

    CONSTRAINT fk_lpa_epoch
        FOREIGN KEY (lit_epoch_slug)
        REFERENCES lit_epoch(slug)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_lpa_part
        FOREIGN KEY (part_id)
        REFERENCES service_part(part_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_lpa_group
        FOREIGN KEY (chant_group_id)
        REFERENCES chant_group(chant_group_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_lpa_assignment_authority
        FOREIGN KEY (assignment_authority_code)
        REFERENCES p_assignment_authority(authority_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    -- Ranges / sanity
    CONSTRAINT chk_lpa_wkday_range
        CHECK (wkday IS NULL OR (wkday BETWEEN 1 AND 7)),

    CONSTRAINT chk_lpa_wknum_mod_4_range
        CHECK (wknum_mod_4 IS NULL OR wknum_mod_4 IN (0,1,2,3)),

    CONSTRAINT chk_lpa_wknum_mod_2_range
        CHECK (wknum_mod_2 IS NULL OR wknum_mod_2 IN (0,1)),

    CONSTRAINT chk_lpa_cycle_wk_range
        CHECK (cycle_wk IS NULL OR cycle_wk IN (0,1)),

    CONSTRAINT chk_lpa_cycle_sun_range
        CHECK (cycle_sun IS NULL OR cycle_sun IN (0,1,2))

    -- REMOVED (phase 4b):
    --   chk_lpa_not_both_wkday_and_seq  (seq column dropped)
    --   chk_lpa_psalter_only_when_no_liturgical_keys  (replaced by application-layer
    --     enforcement; MySQL 3823 prevents CHECK on FK-referential-action columns)
);


CREATE TABLE gregobase_chant_group_map (
    gregobase_id   INT NOT NULL,
    chant_group_id BIGINT UNSIGNED NOT NULL,

    PRIMARY KEY (gregobase_id),
    KEY idx_gbgm_group (chant_group_id),

    CONSTRAINT fk_gbgm_gregobase
        FOREIGN KEY (gregobase_id)
        REFERENCES gregobase_chants(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_gbgm_group
        FOREIGN KEY (chant_group_id)
        REFERENCES chant_group(chant_group_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);



-- --------------------------------------
-- v_chant_item  - unifies local_chants and gregobase_chants
-- --------------------------------------

CREATE OR REPLACE VIEW v_chant_item AS
SELECT
    CONCAT('gregobase:', CAST(g.id AS CHAR)) AS chant_item_uid,
    'gregobase' AS source_code,
    CAST(g.id AS CHAR) AS source_pk,
    m.chant_group_id,
    g.version,
    'gabc' AS notation,
    g.incipit,
    g.mode,
    g.mode_var,
    g.`office-part` AS office_part,
    g.gabc,
    NULL AS translation_source_code,
    NULL AS is_text_exact,
    NULL AS derived_from_uid,
    NULL AS status,
    NULL AS created_at,
    NULL AS updated_at,
    g.cantusid,
    g.transcriber,
    g.commentary,
    g.copyrighted,
    g.duplicateof
FROM gregobase_chants g
JOIN gregobase_chant_group_map m
  ON m.gregobase_id = g.id
UNION ALL
SELECT
    CONCAT('local:', l.local_chant_id) AS chant_item_uid,
    'local' AS source_code,
    l.local_chant_id AS source_pk,
    l.chant_group_id,
    l.version,
    l.notation,
    l.incipit,
    l.mode,
    l.mode_var,
    l.`office-part` AS office_part,
    l.gabc,
    l.translation_source_code,
    l.is_text_exact,
    l.derived_from_uid,
    l.status,
    l.created_at,
    l.updated_at,
    NULL AS cantusid,
    l.transcriber,
    l.commentary,
    NULL AS copyrighted,
    NULL AS duplicateof
FROM local_chants l;

