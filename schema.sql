-- schema.sql — Trigzi server MySQL schema
-- MySQL 8+ / MariaDB 10.6+
--
-- Three concerns, three table groups:
--   1. Product data   — product, gtin_miss_cache
--   2. User content   — user_image (moderation queue)
--   3. Server state   — sessions, enrichments
--
-- The `product` table mirrors the gtin_cache.db column layout exactly so
-- that the import pipeline (gtin_cache.db → MySQL) and the export pipeline
-- (MySQL → gtin_cache.db snapshot) are mechanical transforms with no
-- semantic conversion. Nutrition values use the same ×10 integer encoding;
-- blobs use the same LE uint16_t canonical/category encoding.
--
-- Run order: schema.sql first, then schema_migrate.sql if upgrading from
-- the old fat-JSON products table.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Product data
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS product (
    -- Primary key: EAN-13 as INT64 (matches gtin_cache.db INTEGER PRIMARY KEY)
    gtin            BIGINT NOT NULL PRIMARY KEY,

    -- Locale
    country_code    CHAR(2)          NOT NULL DEFAULT 'AU',   -- ISO 3166-1 alpha-2
    lang_code       CHAR(5)          NOT NULL DEFAULT 'en',   -- BCP-47

    -- Source bitmask (matches gtin_cache.db: 1=WW 2=Coles 4=IGA 8=OFF)
    sources         TINYINT UNSIGNED NOT NULL DEFAULT 0,

    scraped_at      DATETIME,

    -- Image
    -- img_url  : source URL used by the image miner
    -- img_downloaded : 0=pending 1=done 2=404 3=failed
    img_url         TEXT,
    img_downloaded  TINYINT UNSIGNED NOT NULL DEFAULT 0,

    -- EU-14 allergen bitmask (matches gtin_cache.db allergen INTEGER)
    allergen        SMALLINT UNSIGNED NOT NULL DEFAULT 0,

    -- Nutrition — integer-encoded (matches gtin_cache.db ×10 scheme)
    -- size_g / serving_g stored as-is (grams)
    -- macro columns: g × 10  (e.g. 12.3g → 123)
    -- sodium: mg × 10        (e.g. 450mg → 4500)
    size_g          SMALLINT UNSIGNED,
    serving_g       SMALLINT UNSIGNED,
    energy_kj       SMALLINT UNSIGNED,
    protein         SMALLINT UNSIGNED,    -- g × 10
    fat             SMALLINT UNSIGNED,    -- g × 10
    fat_sat         SMALLINT UNSIGNED,    -- g × 10
    carbs           SMALLINT UNSIGNED,    -- g × 10
    sugars          SMALLINT UNSIGNED,    -- g × 10
    fibre           SMALLINT UNSIGNED,    -- g × 10
    sodium          INT UNSIGNED,         -- mg × 10 (INT: high-sodium products exceed SMALLINT)

    -- Dietary scores (matches gtin_cache.db)
    nova            TINYINT UNSIGNED,     -- 1–4
    nutriscore      TINYINT UNSIGNED,     -- 0=A … 4=E
    healthstar      TINYINT UNSIGNED,     -- stars × 2 (0–10)
    egl             TINYINT UNSIGNED,     -- 1=low 2=medium 3=high 4=very high

    -- Binary blobs — N × LE uint16_t (matches gtin_cache.db BLOB encoding)
    categories      BLOB,
    canonicals      BLOB,

    -- Display fields
    name            VARCHAR(512),
    brand           VARCHAR(256),
    raw_ingredients TEXT,

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_country           (country_code),
    INDEX idx_country_scraped   (country_code, scraped_at),
    INDEX idx_img_pending       (img_downloaded, country_code)   -- miner queue scan
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Phone-home miss cache
-- When a device scans a GTIN not in gtin_cache.db, the server enriches
-- on the fly and caches the result here. Subsequent requests return the
-- cached row without re-running enrichment.

CREATE TABLE IF NOT EXISTS gtin_miss_cache (
    gtin            BIGINT NOT NULL PRIMARY KEY,
    country_code    CHAR(2)          NOT NULL DEFAULT 'AU',

    -- Subset of product columns — enough to build a gtin_cache.db-compatible row
    name            VARCHAR(512),
    brand           VARCHAR(256),
    raw_ingredients TEXT,
    allergen        SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    canonicals      BLOB,
    nova            TINYINT UNSIGNED,
    nutriscore      TINYINT UNSIGNED,
    healthstar      TINYINT UNSIGNED,
    egl             TINYINT UNSIGNED,

    -- Which enrichment path produced this result
    source          VARCHAR(64),          -- 'ocr', 'barcodelookup', 'llm'

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_country (country_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. User content — moderation queue
-- ─────────────────────────────────────────────────────────────────────────────

-- User-submitted product images land here pending moderation.
-- Approved images are moved to the d4/d4 shard on disk and the product
-- row is updated. Rejected images are deleted from temp storage.
--
-- The device_hash is the first 16 hex chars of SHA256(device_id) — enough
-- to rate-limit and detect bad actors without storing PII.

CREATE TABLE IF NOT EXISTS user_image (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    gtin            BIGINT NOT NULL,

    -- Temp storage path (relative to image root) until moderation completes
    -- e.g. "pending/{uuid}.jpg"
    storage_key     VARCHAR(512)     NOT NULL,

    image_type      ENUM('front','ingredients','nutrition','back','other')
                        NOT NULL DEFAULT 'front',
    mime_type       VARCHAR(64)      NOT NULL DEFAULT 'image/jpeg',
    size_bytes      INT UNSIGNED,
    sha256          CHAR(64),

    -- Contributor (anonymised)
    device_hash     CHAR(16),              -- SHA256(device_id)[0:16]
    country_code    CHAR(2)          NOT NULL DEFAULT 'AU',
    captured_at     DATETIME,
    created_at      DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Moderation
    mod_state       ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
    mod_reason      VARCHAR(256),          -- rejection reason (internal)
    moderated_at    DATETIME,

    INDEX idx_gtin       (gtin),
    INDEX idx_mod_state  (mod_state),
    INDEX idx_device     (device_hash),
    INDEX idx_created    (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Server state (unchanged from prior schema)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS enrichments (
    id          INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task        VARCHAR(50),
    llm_model   VARCHAR(100),
    prompt_ver  VARCHAR(20),
    prompt_hash CHAR(8),
    prompt_text MEDIUMTEXT,
    UNIQUE KEY uq_prompt (prompt_hash, llm_model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS sessions (
    device_id            VARCHAR(64)  NOT NULL PRIMARY KEY,
    last_seen_at         DATETIME,
    ip_last              VARCHAR(45),
    app_version          VARCHAR(32),
    motd_last_date       DATE,
    tier                 ENUM('free','paid') NOT NULL DEFAULT 'free',
    tier_expires_at      DATETIME,
    tokens_used_today    INT          NOT NULL DEFAULT 0,
    tokens_budget_daily  INT          NOT NULL DEFAULT 50000,
    tokens_reset_date    DATE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
