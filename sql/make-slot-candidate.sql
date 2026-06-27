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

-- lit_part_assignment was DROPPED in step 8 (2026-06-26).
-- Assignment data now lives in lit_part_sources (rows with chant_uuid IS NOT NULL).
-- See scripts/migrate_step8_lps_absorbs_lpa.py.


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

