-- DDL and seed data for liturgical season/subseason lookup tables.
-- Captured from live DB 2026-06-19.
-- These tables are referenced by:
--   liturgical_day (existing FKs), lit_epoch (FKs added step 7b).

CREATE TABLE IF NOT EXISTS `p_liturgical_seasons` (
  `season` varchar(40) NOT NULL,
  `chron_order` smallint DEFAULT NULL,
  PRIMARY KEY (`season`),
  UNIQUE KEY `chron_order` (`chron_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO `p_liturgical_seasons` (`season`, `chron_order`) VALUES
  ('ADV',  1),
  ('NAT',  2),
  ('TQ',   3),
  ('PASC', 4),
  ('OT',   5),
  ('FOL',  6)
ON DUPLICATE KEY UPDATE `chron_order` = VALUES(`chron_order`);


CREATE TABLE IF NOT EXISTS `p_liturgical_subseasons` (
  `season` varchar(40) NOT NULL,
  `subseason` varchar(40) NOT NULL,
  `subseason_order` smallint DEFAULT NULL,
  `season_rnk` int DEFAULT NULL,
  `minwk` int DEFAULT NULL,
  `maxwk` int DEFAULT NULL,
  `minseq` int DEFAULT NULL,
  `maxseq` int DEFAULT NULL,
  PRIMARY KEY (`season`, `subseason`),
  CONSTRAINT `p_liturgical_subseasons_p_liturgical_seasons_FK`
    FOREIGN KEY (`season`) REFERENCES `p_liturgical_seasons` (`season`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO `p_liturgical_subseasons`
  (`season`, `subseason`, `subseason_order`, `season_rnk`, `minwk`, `maxwk`, `minseq`, `maxseq`)
VALUES
  ('ADV',  'I',         1, 1, 1,  3,  1, 7),
  ('ADV',  'II',        2, 1, 0,  0,  1, 8),
  ('NAT',  'DAY',       1, 2, 0,  0,  0, 0),
  ('NAT',  'IO',        2, 2, 1,  1,  0, 6),
  ('NAT',  'OCT',       3, 2, 2,  2,  0, 0),
  ('NAT',  'PO',        4, 2, 2,  2,  0, 6),
  ('NAT',  'EPI',       5, 2, 0,  0,  0, 6),
  ('NAT',  'BAPT',      6, 2, 1,  1,  0, 0),
  ('TQ',   'LENT',      1, 3, 0,  5,  1, 7),
  ('TQ',   'HOLYWEEK',  2, 3, 0,  0,  1, 7),
  ('PASC', 'OCT',       1, 4, 1,  2,  1, 7),
  ('PASC', 'AD_ASC',    2, 4, 2,  6,  1, 7),
  ('PASC', 'ASC',       3, 4, 6,  6,  5, 5),
  ('PASC', 'POST_ASC',  4, 4, 6,  7,  1, 7),
  ('PASC', 'PENT',      5, 4, 8,  8,  1, 1),
  ('OT',   'OT',        1, 5, 1, 34,  1, 7),
  ('OT',   'FOL',       2, 5, 0,  0,  1, 4)
ON DUPLICATE KEY UPDATE
  `subseason_order` = VALUES(`subseason_order`),
  `season_rnk`      = VALUES(`season_rnk`),
  `minwk`           = VALUES(`minwk`),
  `maxwk`           = VALUES(`maxwk`),
  `minseq`          = VALUES(`minseq`),
  `maxseq`          = VALUES(`maxseq`);
