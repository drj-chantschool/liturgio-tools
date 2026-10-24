drop view if exists v_chant_item;
drop table if exists chants;
drop table if exists service_part;
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
-- chants: every chant score, whatever its source (2026-10-23; replaces
-- local_chants, gregobase_chant_group_map and the v_chant_item union).
-- GregoBase rows are imported from the gregobase_chants staging table by
-- liturgio_tools.chants_import; local ('Dr. J') chants are written by the API.
-- ============================================================
CREATE TABLE chants (
  `chant_uuid` varchar(80) NOT NULL COMMENT 'gregobase:<id> | local:<uuid>; referenced by lit_part_sources, chant_indices, derived_from_uid',
  `chant_group_id` bigint unsigned NOT NULL,
  `version` varchar(128) DEFAULT 'english',
  `incipit` varchar(256) DEFAULT NULL,
  `office_part` varchar(16) DEFAULT NULL,
  `mode` varchar(8) DEFAULT NULL,
  `mode_var` varchar(16) DEFAULT NULL,
  `transcriber` varchar(128) DEFAULT 'Doctor J',
  `commentary` varchar(256) DEFAULT NULL,
  `notation` varchar(10) NOT NULL DEFAULT 'gabc',
  `elements` json NOT NULL COMMENT '[[type, content, options], ...]; see gabc_tools.elements',
  `headers` text COMMENT 'GregoBase GABC header lines (name, office-part, mode, book, transcriber)',
  `cantusid` varchar(32) DEFAULT NULL,
  `copyrighted` tinyint(1) DEFAULT NULL,
  `translation_source_code` varchar(40) DEFAULT NULL,
  `source_citation` varchar(500) DEFAULT NULL COMMENT 'Verifiable reference for translation (e.g. liturgies.net URL, book+page, psalm+verse)',
  `is_text_exact` tinyint unsigned DEFAULT '1',
  `derived_from_uid` varchar(80) DEFAULT NULL COMMENT 'English adaptation of this chant_uuid (not provenance)',
  `status` varchar(20) DEFAULT 'draft',
  `notes` varchar(500) DEFAULT NULL,
  `origin_source` varchar(20) DEFAULT NULL COMMENT 'Upstream feed (GREGOBASE); NULL = made here',
  `origin_id` varchar(64) DEFAULT NULL COMMENT 'Row id in the upstream feed',
  `origin_hash` char(64) DEFAULT NULL COMMENT 'SHA-256 of the upstream row at import (chants_import.origin_hash)',
  `origin_pending_hash` char(64) DEFAULT NULL COMMENT 'Upstream changed on a curated row: new hash awaiting review',
  `is_curated` tinyint(1) NOT NULL DEFAULT '0' COMMENT '1 = edited here; a re-import never overwrites it',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`chant_uuid`),
  UNIQUE KEY `uq_chants_origin` (`origin_source`, `origin_id`),
  KEY `idx_chants_group` (`chant_group_id`),
  KEY `idx_chants_version` (`version`),
  KEY `idx_chants_mode` (`mode`),
  KEY `idx_chants_status` (`status`),
  KEY `idx_chants_translation_source` (`translation_source_code`),
  KEY `idx_chants_office_part` (`office_part`),
  KEY `idx_chants_incipit` (`incipit`),
  KEY `idx_chants_derived_from` (`derived_from_uid`),
  CONSTRAINT `fk_chants_group` FOREIGN KEY (`chant_group_id`) REFERENCES `chant_group` (`chant_group_id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_chants_status` FOREIGN KEY (`status`) REFERENCES `p_review_status` (`status_code`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_chants_translation_source` FOREIGN KEY (`translation_source_code`) REFERENCES `p_translation_source` (`translation_source_code`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='Every chant score, whatever its source (GregoBase import, Dr. J transcriptions)';


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


-- --------------------------------------
-- v_chant_item: compatibility view over chants (same columns as the old
-- gregobase_chants + local_chants union; `gabc` is the element list as JSON).
-- Drop it once nothing reads it.
-- --------------------------------------
CREATE OR REPLACE VIEW v_chant_item AS
SELECT c.chant_uuid AS chant_item_uid,
       IF(c.chant_uuid LIKE 'gregobase:%', 'gregobase', 'local') AS source_code,
       SUBSTRING_INDEX(c.chant_uuid, ':', -1) AS source_pk,
       c.chant_group_id, c.version, c.notation, c.incipit, c.mode, c.mode_var, c.office_part,
       CAST(c.elements AS CHAR) AS gabc,
       c.translation_source_code, c.is_text_exact, c.derived_from_uid, c.status,
       c.created_at, c.updated_at, c.cantusid, c.transcriber, c.commentary, c.copyrighted,
       NULL AS duplicateof
FROM chants c;
