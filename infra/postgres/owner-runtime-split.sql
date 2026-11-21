-- KERP PostgreSQL owner/runtime split (T-0343, ADR 0019), for CI's `backend-pg-roles` job.
--
-- Run as the superuser ("kerp"), connected to the test database, AFTER initdb/20-kerp-app.sql
-- (which creates the runtime role kerp_app and hands it schema public, TD-056). This script
-- takes the schema away from kerp_app again and gives it to a separate owner role, the way
-- production is laid out (docs/deploy/first-run.md):
--
--   * kerp_owner: owns schema public and runs the migrations (KERP_TEST_MIGRATION_URL).
--     NOSUPERUSER NOBYPASSRLS, like an RDS master's delegate; it never serves requests.
--   * kerp_app:   the runtime role (KERP_DATABASE_URL). Owns nothing, may create nothing in
--     the schema; migration 0039 grants it exactly what the app needs.
--
-- The passwords are dev/CI-only placeholders for a throwaway container; never reuse them.

CREATE ROLE kerp_owner LOGIN PASSWORD 'kerp_owner' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO kerp_owner', current_database());
END
$$;

ALTER SCHEMA public OWNER TO kerp_owner;
-- PostgreSQL 15+ already denies CREATE on public to PUBLIC; stated so it cannot drift back.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
