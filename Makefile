PYTHON ?= .venv/bin/python
BIN := .venv/bin
NPM := npm --prefix frontend
COMPOSE := docker compose -f docker/compose.yaml
IMAGE := geopython/pygeoapi:0.24.0

.PHONY: help setup vendor images check check-fast lint types check-docs drift test-unit test-docker \
	frontend api-types api-types-update e2e fixtures stack-up stack-reset-config stack-down dev-backend dev-frontend

## List the targets.
help:
	@awk '/^## /{doc=substr($$0,4); next} /^[a-z-]+:/{if (doc) {split($$0,t,":"); printf "  %-17s %s\n", t[1], doc}; doc=""}' $(MAKEFILE_LIST)

## First-time setup: dependencies, pinned sources, browsers and the images.
setup:
	uv sync --frozen
	$(PYTHON) tools/vendor.py
	cd frontend && npm ci && npx playwright install chromium
	docker pull $(IMAGE)
	$(PYTHON) tools/images.py

## The sandbox image (pinned pygeoapi + the spec engine's dependencies), tagged by content.
images:
	$(PYTHON) tools/images.py

## Check out the source repositories the docs cite, at pinned commits.
vendor:
	$(PYTHON) tools/vendor.py

## Every suite (PLAN.md §12). Needs Docker; starts the dev stack if it's down.
check: check-fast test-docker e2e

## The checks that don't need Docker.
check-fast: lint types check-docs drift test-unit frontend api-types

## ruff lint and format.
lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

## mypy (strict) on the backend, the harness, the runtime and the tools.
types:
	$(BIN)/mypy backend/provider_builder harness/src runtime/src tools

## Pinned sources, then citations: strict for curated docs, errors only elsewhere.
check-docs:
	$(PYTHON) tools/vendor.py --check
	$(PYTHON) tools/check_citations.py --strict docs/pygeoapi-notes.md docs/decisions.md PLAN.md
	$(PYTHON) tools/check_citations.py -q

## Descriptors match the keys pygeoapi's providers read (ADR-0010).
drift:
	$(PYTHON) tools/drift_check.py

## Unit, golden, harness and spec-engine tests (the engine runs against the mock API).
test-unit:
	$(BIN)/pytest backend/tests harness/tests runtime/tests -q

## Contract tests in the pinned image, and integration tests on the dev stack.
test-docker: stack-up
	PB_REQUIRE_DOCKER=1 $(BIN)/pytest tests -q

## Lint, type-check and build the frontend (the backend serves frontend/dist).
frontend:
	$(NPM) run lint
	$(NPM) run build

## The typed API client matches the backend's OpenAPI document (ADR-0011).
api-types:
	$(PYTHON) tools/export_openapi.py --check frontend/src/api/openapi.json
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	frontend/node_modules/.bin/openapi-typescript frontend/src/api/openapi.json \
		--default-non-nullable false -o "$$tmp/schema.d.ts" >/dev/null && \
	diff -u frontend/src/api/schema.d.ts "$$tmp/schema.d.ts" && \
	echo 'frontend/src/api/schema.d.ts is up to date'

## Regenerate the typed API client after changing the backend's API.
api-types-update:
	$(PYTHON) tools/export_openapi.py frontend/src/api/openapi.json
	$(NPM) run gen:api

## Playwright with axe, including the specs that need the dev stack (PostGIS, mock API).
e2e: frontend stack-up
	E2E_STACK=1 $(NPM) run e2e

FIXTURES := obs.csv ne_110m_lakes.geojson poi_portugal.gpkg dutch_addresses_4326.gpkg \
	CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2 coads_sst.nc \
	canada-hydat-daily-mean-02HC003.tinydb
FIXTURE_DIRS := analysed_sst.zarr tiles dutch_addresses_shape_4326

## Sample data for the dev stack, copied from pygeoapi's own test data.
fixtures:
	mkdir -p docker/data
	cd vendor/pygeoapi/tests/data && cp $(FIXTURES) ../../../../docker/data/ \
		&& cp -R $(FIXTURE_DIRS) ../../../../docker/data/
	cp vendor/pygeoapi/tests/data/open.canada.ca/sample-records.tinydb docker/data/canada-records.tinydb
	cp -R backend/tests/fixtures/stac-catalog docker/data/

## The dev stack: pygeoapi on :5080, PostGIS on :55432, the mock API on :8090.
stack-up: fixtures images
	touch docker/pygeoapi/pygeoapi.env
	test -f docker/pygeoapi/pygeoapi.config.yml || cp docker/pygeoapi/pygeoapi.config.seed.yml docker/pygeoapi/pygeoapi.config.yml
	mkdir -p workspace/providers
	$(COMPOSE) up -d --wait

## Put the dev stack's config back to the seed (publishes edit it), then recreate pygeoapi.
stack-reset-config:
	cp docker/pygeoapi/pygeoapi.config.seed.yml docker/pygeoapi/pygeoapi.config.yml
	$(COMPOSE) up -d --no-deps --force-recreate --wait pygeoapi

## Stop the dev stack.
stack-down:
	$(COMPOSE) down

## The backend on http://127.0.0.1:8765; it serves frontend/dist once built.
dev-backend:
	$(PYTHON) -m provider_builder --reload

## Vite on http://127.0.0.1:5173, proxying /api to the backend.
dev-frontend:
	$(NPM) run dev
