#!/bin/sh
set -eu
# psql variables quoted as SQL literals; never interpolate passwords as SQL code.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=owner_password="$POSTGRES_OWNER_PASSWORD" --set=app_password="$POSTGRES_APP_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE ROLE portal_owner LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE portal_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO portal_owner;
GRANT USAGE, CREATE ON SCHEMA public TO portal_owner;
GRANT USAGE ON SCHEMA public TO portal_app;
ALTER DEFAULT PRIVILEGES FOR ROLE portal_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO portal_app;
ALTER DEFAULT PRIVILEGES FOR ROLE portal_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO portal_app;
SQL
