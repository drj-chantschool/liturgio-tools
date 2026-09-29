-- Tracks reviewed chant-group pairs for the merge manager page.
-- group_id_a is always MIN(id_a, id_b) so each pair has one canonical row.
-- No FK constraints on group_id_a/b intentionally: merged groups are deleted,
-- and we want to keep the historical review record.
CREATE TABLE IF NOT EXISTS chant_group_merge_queue (
    id             BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    group_id_a     BIGINT UNSIGNED NOT NULL,   -- lower chant_group_id of the pair
    group_id_b     BIGINT UNSIGNED NOT NULL,   -- higher chant_group_id of the pair
    status         ENUM('merged', 'rejected', 'related') NOT NULL,
    merged_into_id BIGINT UNSIGNED NULL,       -- surviving group (status='merged' only)
    reviewed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_cgmq_pair (group_id_a, group_id_b),
    KEY idx_cgmq_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
