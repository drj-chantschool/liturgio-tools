-- ============================================================
-- liturgio-api login: roles + users
--
-- p_app_role.role_level orders the roles; the API gates on
-- "level >= N", so new roles slot in between without code changes
-- as long as their level is chosen relative to these.
--
-- Users are managed with liturgio-api/manage_users.py (never by
-- hand-written INSERTs: password_hash is scrypt, see auth.py).
-- ============================================================

CREATE TABLE IF NOT EXISTS p_app_role (
    role_code     VARCHAR(20)       NOT NULL PRIMARY KEY,
    display_name  VARCHAR(80)       NOT NULL,
    role_level    SMALLINT UNSIGNED NOT NULL,   -- higher = more privilege
    sort_order    SMALLINT UNSIGNED NOT NULL DEFAULT 100,
    is_active     TINYINT           NOT NULL DEFAULT 1,
    notes         VARCHAR(500)      NULL,
    UNIQUE KEY uq_app_role_level (role_level)
);

INSERT INTO p_app_role (role_code, display_name, role_level, sort_order, notes) VALUES
('READONLY', 'Read-only', 10, 10, 'May view every page and change their own password; every other write is refused'),
('EDITOR',   'Editor',    50, 20, 'May use the write endpoints (where the deployment allows writes)'),
('ADMIN',    'Admin',     90, 30, 'Editor + user management')
ON DUPLICATE KEY UPDATE display_name = VALUES(display_name),
                        role_level   = VALUES(role_level),
                        sort_order   = VALUES(sort_order),
                        notes        = VALUES(notes);

CREATE TABLE IF NOT EXISTS app_user (
    user_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username       VARCHAR(64)   NOT NULL,      -- stored lower-case
    display_name   VARCHAR(120)  NULL,
    password_hash  VARCHAR(255)  NOT NULL,      -- scrypt$n$r$p$salt$hash (base64)
    role_code      VARCHAR(20)   NOT NULL,
    is_active      TINYINT       NOT NULL DEFAULT 1,
    -- bumped on password change / deactivation; sessions carry it, so
    -- old cookies stop working (prod is read-only, no session table)
    session_epoch  INT UNSIGNED  NOT NULL DEFAULT 0,
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_app_user_username (username),
    CONSTRAINT fk_app_user_role FOREIGN KEY (role_code) REFERENCES p_app_role (role_code)
);
