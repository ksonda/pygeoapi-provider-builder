PYTHON ?= .venv/bin/python

.PHONY: check check-docs fixtures stack-up stack-down

## Run every check. Phase 0 has documentation checks only; later phases add
## lint, types, unit, contract, integration and E2E suites here (PLAN.md §12).
check: check-docs

## Citations: strict for curated docs, errors-only for the research appendices.
check-docs:
	$(PYTHON) tools/check_citations.py --strict docs/pygeoapi-notes.md docs/decisions.md PLAN.md
	$(PYTHON) tools/check_citations.py -q

FIXTURES := obs.csv ne_110m_lakes.geojson poi_portugal.gpkg

## Sample data for the dev stack and tests, copied from pygeoapi's own test data.
fixtures:
	mkdir -p docker/data
	cd vendor/pygeoapi/tests/data && cp $(FIXTURES) ../../../../docker/data/

COMPOSE := docker compose -f docker/compose.yaml

## The dev stack: pygeoapi 0.24.0 on http://localhost:5000 and PostGIS on localhost:55432.
stack-up: fixtures
	touch docker/pygeoapi/pygeoapi.env
	$(COMPOSE) up -d --wait

stack-down:
	$(COMPOSE) down
