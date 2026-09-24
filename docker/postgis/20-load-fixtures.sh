#!/bin/sh
# Load pygeoapi's PostgreSQL test fixture the way pygeoapi's own CI does
# (.github/workflows/main.yml pipes it into psql): the dump's
# `CREATE SCHEMA topology` clashes with the image's postgis_topology
# extension, and plain psql carries on past that one error.
set -e
gunzip -c /fixtures/hotosm_bdi_waterways.sql.gz \
  | psql --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" > /dev/null
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tAc \
  "select count(*) || ' waterways loaded' from osm.hotosm_bdi_waterways"
