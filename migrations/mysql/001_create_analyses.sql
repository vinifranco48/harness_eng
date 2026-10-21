-- Administrative migration; application timestamps are supplied in UTC.
-- IF NOT EXISTS permits recovery after interrupted, non-transactional DDL.
-- The migration runner validates the complete table before recording it.
CREATE TABLE IF NOT EXISTS `analyses` (
    `id` CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `title` VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_analyses_updated_at_id` (`updated_at`, `id`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
