-- lit_text_chant_link: many-to-many between liturgical source texts and chants.
--
-- text_id        -> lit_part_sources.text_id (real FK, cascades).
-- chant_item_uid -> v_chant_item.chant_item_uid, a VIEW unifying gregobase
--                   ('gregobase:N') and local ('local:<uuid>') chants. Because it
--                   targets a VIEW, no real FOREIGN KEY can be declared on it; it is
--                   kept as an indexed column (idx_ltcl_chant) instead.
CREATE TABLE IF NOT EXISTS lit_text_chant_link (
    text_id        BIGINT UNSIGNED NOT NULL,   -- FK lit_part_sources.text_id
    chant_item_uid VARCHAR(80)     NOT NULL,    -- v_chant_item.chant_item_uid (view; no FK possible)
    notes          VARCHAR(500)    NULL,
    created_at     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (text_id, chant_item_uid),
    KEY idx_ltcl_chant (chant_item_uid),
    CONSTRAINT fk_ltcl_text FOREIGN KEY (text_id)
        REFERENCES lit_part_sources(text_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
