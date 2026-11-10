-- KERP PostgreSQL bootstrap: extensions + the runtime role (T-0003, TD-052).
--
-- One script, two callers, so local runs match CI:
--   * CI backend job, step "Create non-superuser app role": psql -v ON_ERROR_STOP=1 -f <this>
--   * docker-compose.yml: mounted as /docker-entrypoint-initdb.d/20-kerp-app.sql, so it runs
--     once on an empty data volume, after the PostGIS image's own 10_postgis.sh.
-- Both run it as the superuser (POSTGRES_USER, "kerp") connected to the test database.
--
-- kerp_app is the role the application and the tests connect as. It must stay
-- NOSUPERUSER NOBYPASSRLS: a superuser or BYPASSRLS role ignores row-level security and
-- the RLS-bypass contract tests would pass for the wrong reason. It owns schema public
-- because it runs the migrations in the tests (TD-056).
-- The password is a dev/CI-only placeholder for a throwaway container; never reuse it.

CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE ROLE kerp_app LOGIN PASSWORD 'kerp_app' NOSUPERUSER NOBYPASSRLS;

DO $$
BEGIN
  EXECUTE format('GRANT ALL ON DATABASE %I TO kerp_app', current_database());
END
$$;

ALTER SCHEMA public OWNER TO kerp_app;
