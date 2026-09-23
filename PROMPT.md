# Build prompt: pygeoapi Provider Builder

> **How to use:** Check the parameters in §1 and change any you disagree with. Then open Claude Code in this folder and say: *"Read PROMPT.md and follow it, starting with Phase 0."* Everything below is written to Claude.

---

## 0. Mission

Build a web application that lets people **create new pygeoapi providers** and **configure existing ones** through a GUI. For common cases, nobody should have to write YAML or Python.

In pygeoapi, "provider" means two things: the **plugin class** that talks to a backend, and the **`providers:` entry** in a resource that configures it. The app has two first-class modes, one for each, and they connect:

- **Mode A: Configure.** Create a resource (collection) and its provider entries from any available provider: pygeoapi's built-ins, installed third-party plugins, or providers made in Mode B. Then validate, preview and publish it.
- **Mode B: Create.** Build a provider for a data source pygeoapi can't read today, such as an agency's REST API, a custom SQL query or a time-series database.
  - Data stewards use templates and point-and-click mapping, with no code.
  - Developers write or refine Python in a code workspace.

  Both paths run the same contract tests, and both end with a provider that appears in Mode A's catalog, ready to publish.

**Users.** Data stewards know their data (a CSV, a PostGIS table, an agency's JSON API) but not pygeoapi. Developers want correct, tested provider code and config without the boilerplate.

**Success means:**
- A steward goes from "I have a GeoPackage" to a live `/collections/{id}/items` in under five minutes (Mode A).
- A steward turns a JSON API into a live collection without writing code (Mode B, then Mode A).
- A developer goes from "I need a provider for X" to a tested, installable package with the boring parts already done (Mode B).
- Developers trust the output enough to commit it unedited.

## 1. Parameters

These are my defaults. In Phase 0, confirm them with me in a single message. If I reply "defaults", use them.

| Parameter | Default | Alternatives |
|---|---|---|
| Target pygeoapi | **0.24.0** (latest release, 2026-07-28). Pin it. | An older version our deployments run |
| Scope | **Both modes in v1** | Mode A only; Mode B only |
| Provider creation | No-code templates for HTTP JSON API → Features, SQL query → Features, and time series → EDR. Plus a code workspace for every provider type, with "eject to code" | Code workspace only (scaffolds + contract tests) |
| Running custom code | Only in disposable sandbox containers. Code reaches production only through a Git PR and CI | Trusted local mode: a local venv, as an explicit single-user opt-in |
| Stack | Backend: FastAPI on Python ≥ 3.12, importing pygeoapi. Frontend: React + TypeScript + Vite, with CodeMirror for code and YAML | All-Python: FastAPI + Jinja2 + HTMX (same backend API) |
| Publish targets | (1) copy or download YAML, (2) write to a local config file | (3) pygeoapi Admin API, (4) open a Git pull request against a config repo |
| Plugin catalog | pygeoapi core providers, `pygeoapi-plugins` (cgs-earth), and providers created in Mode B | Core and created providers only |
| Geoconnex options | On: `uri_field` checks, JSON-LD guidance, and ODM2/QUDT links for EDR parameters | Off |
| Users & auth | Single-user tool bound to 127.0.0.1. Can be deployed behind a reverse proxy that handles auth | Built-in OIDC |
| Test runtime | Docker: `geopython/pygeoapi:0.24.0`, PostGIS and the sandbox containers, via docker compose | Local venv only (no Mode B sandbox) |

**Environment notes (2026-09-22; check again).** This Mac has Python 3.11.5 and 3.10 from Homebrew, but pygeoapi 0.24 needs **Python ≥ 3.12**. Node is **not** installed. The Docker CLI is installed, but the daemon wasn't running, and Mode B's sandbox depends on it. Install what you need (for example `brew install python@3.12 node`, or `uv`) and record the steps in the README.

## 2. Ground truth: read the source, not your memory

pygeoapi's config and plugin interfaces change between minor releases. The `pubsub` and `validator` plugins, for example, are recent additions, and the way EDR providers declare their query types has changed. So:

1. Install the pinned version in a venv.
2. Clone the repo at the matching release tag into a gitignored `vendor/` folder, for its `docs/` and `tests/data/`.
3. Treat that code as the authority.

The facts below were checked against pygeoapi master and the 0.24 docs on 2026-09-22. **Re-verify each one against the pinned version and cite `file:line`.**

### Config model
- The top-level sections are `server`, `logging`, `metadata` and `resources`. Each key under `resources` is a resource id. For collections, pygeoapi uses the id as-is in the URL `/collections/{id}`.
- Resource `type` is one of `collection`, `stac-collection` or `process`.
  - A `collection` requires `title`, `description`, `keywords`, `extents` and `providers`. `extents` needs at least `spatial.bbox`; `crs` defaults to CRS84.
  - Optional keys: `links` (each needs `type`, `rel` and `href`), `linked-data.context`, `visibility`, and dataset-level `templates`.
  - `title`, `description` and `keywords` can be dicts keyed by language (i18n).
- Provider `type` is one of `feature`, `coverage`, `record`, `map`, `tile`, `edr` or `stac`.
  - Required keys: `type`, `name` and `data` (a string or an object).
  - Common optional keys: `default`, `editable`, `count`, `table`, `id_field`, `title_field`, `uri_field`, `time_field`, `x_field`/`y_field`/`z_field`, `properties`, `format: {name, mimetype}`, `crs`, `storage_crs`, `storage_crs_coordinate_epoch`, `always_xy`, `options`, `include_extra_query_parameters`, `validator` and `formatters`.
  - Exception: CSV uses `geometry: {x_field, y_field}` instead of top-level `x_field`/`y_field`.
- The JSON Schema lives at `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml` and does not forbid extra provider keys. Provider-specific keys (PostgreSQL's `geom_field`, the fields inside `data`, most `options`) are **not described by any schema**. That gap is why this app needs its own descriptors (§3).
- `pygeoapi.util.yaml_load` replaces `${VAR}` and `${VAR:-default}` with environment values. An undefined variable with no default raises an error.

### Plugins
- `pygeoapi/plugin.py` has a registry, `PLUGINS['provider']`, that maps short names to classes. At time of writing it contains: AzureBlobStorage, CSV, CSWFacade, Elasticsearch, ElasticsearchCatalogue, ERDDAPTabledap, ESRI, FileSystem, GeoJSON, Hateoas, MapScript, MongoDB, MVT-elastic, MVT-postgresql, MVT-proxy, MVT-tippecanoe, MySQL, OGR, OpenSearch, OracleDB, Parquet, PostgreSQL (`pygeoapi/provider/sql.py`), rasterio, SensorThings, SensorThingsEDR, Socrata, SQLiteGPKG, TinyDB, TinyDBCatalogue, WMSFacade, WMTSFacade, xarray, xarray-edr.
- `load_plugin('provider', provider_def)` looks up a short name in the registry. A name containing dots is imported as a full class path, which is how third-party plugins, including the ones Mode B creates, are referenced. An unknown short name raises `InvalidPluginError`.
- Base classes:
  - `provider/base.py::BaseProvider`: features, records, coverages and maps.
  - `provider/base_edr.py::BaseEDRProvider`: EDR.
  - `provider/tile.py::BaseTileProvider`: tiles.
  - `provider/base_mvt.py`: MVT tiles.
- `BaseProvider` defines `get_fields()`, `get_schema()`, `get_metadata()`, `get_domains()`, `query()`, `get()`, `create()`, `update()` and `delete()`. It also defines the `Provider*Error` exceptions (`ProviderConnectionError`, `ProviderQueryError`, `ProviderItemNotFoundError`, `ProviderInvalidDataError`, …). Use them to turn failures into plain-language messages.
- Provider-specific keys are documented only in each module under `pygeoapi/provider/` and in `docs/source/publishing/ogcapi-*.rst`. Read both, because they don't always agree. For example, the ESRI docs example sets `crs: 4326`, while the schema describes `crs` as a list of CRS URIs. Settle conflicts like this by running the validator and the provider, not by guessing.

### Provider contract (what Mode B must produce)
- **Features and records** (see `provider/geojson.py`):
  - `query(self, offset=0, limit=10, resulttype='results', bbox=[], datetime_=None, properties=[], sortby=[], select_properties=[], skip_geometry=False, q=None, **kwargs)` is decorated with `@crs_transform`. It returns a GeoJSON FeatureCollection with `numberReturned`, plus `numberMatched` when counting is on or `resulttype == 'hits'`.
  - `get(identifier, **kwargs)` returns one Feature and raises `ProviderItemNotFoundError` for an unknown id.
  - `get_fields()` returns `{'name': {'type': 'string'}, …}` in core providers, but the plugin docs still show `{'name': 'string'}`. Follow the code.
- **EDR** (see `provider/base_edr.py`):
  - On master, `__init_subclass__` registers query types from method names listed in `EDR_QUERY_TYPES` (position, radius, area, cube, trajectory, corridor, items, locations, instances). A subclass that defines none raises `ProviderInvalidDataError`.
  - `query()` dispatches on `query_type`, and `get_parameters()` builds CoverageJSON parameter definitions from field metadata.
  - Older releases used a `register()` decorator instead, so check which mechanism the pinned version uses.
- **Coverages, tiles and maps:** read the base classes alongside a core example of each (`rasterio_.py`, `mvt_tippecanoe.py`, `wms_facade.py`).
- **Models for API-backed providers:** the ESRI and Socrata providers show pagination and filter push-down. `sensorthings_edr.py` and `xarray_edr.py` show EDR.

### Validation and tooling
- `pygeoapi config validate -c <file>` checks a config against the schema. The same check in code is `pygeoapi.config.validate_config`.
- `pygeoapi openapi generate <config> --output-file <file>` rebuilds the OpenAPI document. By default (`--fail-on-invalid-collection`) it loads the providers, so it can double as a smoke test for the whole config. Verify that.
- The server reads `PYGEOAPI_CONFIG` and `PYGEOAPI_OPENAPI` once, at startup, so new resources need a reload. One way is gunicorn `--reload --reload-extra-file $PYGEOAPI_CONFIG`.

### Admin API
The Admin API lives in `pygeoapi/api/admin.py` and is enabled with `server.admin: true`.
- Routes: `GET|PUT|PATCH /admin/config`, `GET|POST /admin/config/resources`, and `GET|PUT|PATCH|DELETE /admin/config/resources/{id}`.
- The POST body is `{"<resource_id>": {…}}`. It returns 400 if the id already exists.
- Every write runs `validate_config` first (400 on failure) and then regenerates the OpenAPI document.
- It writes with pygeoapi's own YAML dump. That keeps `${VAR}` placeholders but likely drops comments and custom formatting. Verify both.
- It has **no authentication**. pygeoapi leaves access control to whoever deploys it. Only call it from our backend, and only behind an auth proxy.

### Prior art (review before designing)
- **`byteroad/pygeoapi_config`** is a QGIS plugin (MIT license; docs at pygeoapi-config.byteroad.net). It has Server, Metadata and Resources tabs, works on local files or through the Admin API, and validates thoroughly before saving. It only configures WMSFacade, PostgreSQL and MVT-proxy, and it can't create providers. Learn from its validation UX.
- **`cgs-earth/pygeoapi-plugins`** (pip `pygeoapi-plugins`) provides CKANProvider, GeoPandasProvider, SPARQLProvider, PseudoPostgreSQLProvider, MVTPostgreSQLProvider and the MVT cache variants. These are real-world custom providers: the catalog must support them, and they show what Mode B should be able to produce.
- **`cgs-earth/pygeoapi-geoconnex-examples`** has configs that produce Geoconnex-ready JSON-LD landing content from CSV, GeoJSON, GPKG, ESRI and CKAN data, using `uri_field` and JSON-LD templates. It sets templates with a resource-level `template:` key. Check that against core pygeoapi's dataset-level `templates` before relying on it.
- **USGS `pygeoapi-plugin-cookiecutter`** (code.usgs.gov/wma/nhgf) is a project template for pygeoapi plugins. It targets an older Python (3.9), so borrow its structure, not its version pins.
- **OGC CITE test suites** run in Docker via TEAM Engine (`ogccite/ets-ogcapi-features10`, `ogccite/ets-ogcapi-edr10`). Use them as an optional conformance check in the harness (§5).

**Deliverable for this step:** `docs/pygeoapi-notes.md`, a summary of the above for the pinned version with a source citation for each fact, plus anything else you find that matters to either mode.

## 3. Provider descriptors (the catalog)

Use pygeoapi's own JSON Schema for everything it defines: resource metadata, links, extents and common provider keys. Fill the gap with a catalog of **provider descriptors**, one declarative file per provider. The UI renders forms from descriptors, the backend validates against them, and YAML is generated from them. Supporting another existing plugin should mean adding a file, not writing UI code.

Here is an illustrative descriptor. Verify every key against the source; don't copy it blindly.

```yaml
# catalog/core/postgresql.yaml
id: PostgreSQL
plugin: PostgreSQL                  # registry short name, or a dotted path for plugins
provider_type: feature              # inherits id_field, title_field, time_field, uri_field,
                                    # properties, crs, storage_crs... from catalog/_base/feature.yaml
title: PostgreSQL / PostGIS table
summary: Publish a table or view as OGC API - Features.
source: pygeoapi/provider/sql.py    # where these keys were verified
docs: https://docs.pygeoapi.io/en/latest/publishing/ogcapi-features.html#postgresql
requires: [sqlalchemy, geoalchemy2, psycopg2]   # import-checked; shown as an "available / missing" badge
capabilities: {bbox: true, datetime: true, sortby: true, cql: true, transactions: true}
variants:                           # `data` may be an object OR a connection string
  - id: fields
    fields:
      - {key: data.host, type: string, required: true}
      - {key: data.port, type: integer, default: 5432}
      - {key: data.dbname, type: string, required: true}
      - {key: data.user, type: string, required: true}
      - {key: data.password, type: secret, required: true}
      - {key: data.search_path, type: "string[]", default: [public]}
  - id: dsn
    fields:
      - {key: data, type: secret, format: postgresql-dsn, required: true}  # a DSN embeds the password
fields:
  - {key: table, type: string, required: true, choices_from: introspect.tables}
  - {key: geom_field, type: string, default: geom, choices_from: introspect.geometry_columns}
  - {key: count, type: boolean, default: true, advanced: true}
  - {key: options.connect_timeout, type: integer, unit: s, advanced: true}
probe: feature                      # which smoke test to run (see §4, J1 step 4)
examples: [docs-dict-form, docs-dsn-form]   # copied from pygeoapi docs; used by golden tests
```

Descriptor rules:
1. **Common keys** come from base descriptors, one per provider type (`catalog/_base/feature.yaml` and so on). Provider descriptors extend or override them.
2. **Compiled schemas.** Descriptors compile to JSON Schema plus a UI schema. The same compiled schema drives the form in the frontend and validation in the backend.
3. **Secrets** (`type: secret`) are never written as values. They're written as `${ENV_VAR}` placeholders with a suggested, editable name such as `PYGEOAPI_<RESOURCE>_<FIELD>`, and the UI lists the env vars the deployment must define. A user may type a real value for a test run; it stays in memory for that request only.
4. **Citations.** Every hand-written descriptor cites its `source` and `docs` and carries at least one `example` taken from pygeoapi's docs. A **descriptor schema** validates every catalog file in CI.
5. **Drift check.** A script scans each provider module for the keys it reads (`provider_def[...]`, `.get('...')`, `self.data[...]`, `options.get(...)`). It reports keys the descriptor is missing and keys the descriptor invented. Run it in CI and whenever you bump pygeoapi.
6. **Generic fallback.** A provider without a descriptor (any dotted path) still works through a generic form: type, name, data, key/value options, and a raw YAML fragment editor that validates as you type.
7. **Extra catalogs.** The app can load extra catalog directories, such as one shipped inside an org's plugin package, so organizations can add descriptors without forking the app.
8. **Created providers.** Mode B writes a descriptor for every provider it creates, so the provider shows up in Mode A with no extra steps. Template-built descriptors are generated from the spec. Code-built providers get a descriptor file in their scaffold that the developer keeps current; the drift check covers it too.

**Catalog coverage:**
- **Tier 1** (tested end to end):
  - Features: CSV, GeoJSON, SQLiteGPKG, OGR, PostgreSQL, ESRI.
  - Coverages: rasterio, xarray.
  - EDR: xarray-edr.
  - Tiles: MVT-tippecanoe, MVT-proxy.
  - Maps: WMSFacade.
  - Records: TinyDBCatalogue.
- **Tier 2:** all other core providers. Each gets a descriptor and golden tests that pass schema validation, plus integration tests where they're cheap.
- **Org plugins:** the `pygeoapi-plugins` providers listed in §2.
- **Created providers:** whatever Mode B produces.

## 4. Mode A: configure existing providers

### J1: Create a collection (the main wizard)
1. **Start.** Create a new collection, or add a provider to an existing one.
2. **Choose a backend.** Show a searchable catalog grouped by what the user wants to publish: vector features, gridded coverages, EDR time series and positions, vector tiles, maps, catalog records, or STAC. Include providers created in Mode B, labeled with their version and latest test result. Each card shows:
   - the OGC API it enables
   - its capabilities
   - an availability badge ("ready" or "missing `psycopg2`")
   - a docs link

   If nothing fits, offer "Create a new provider →", which opens Mode B.
3. **Connect.** A form built from the descriptor, with inline validation, sensible defaults, and advanced options collapsed.
4. **Test and inspect.** The backend builds the `provider_def` and loads it with pygeoapi's own `load_plugin` in the sandbox runner (§6). Then it runs the probe for that provider type:
   - feature or record: `get_fields()` plus `query(limit=5)`
   - coverage: `get_fields()` plus a tiny subset
   - EDR: `get_fields()` plus a minimal query
   - tile or map: one small tile or image

   It returns field names and types, sample records, and the bbox and time range when they're cheap to compute. Show `Provider*Error`s in plain language (for example, "Couldn't reach db.example.org:5432. Check the host, port and firewall.") and keep the raw traceback behind a *Details* toggle.
5. **Map fields.** Fill pickers from the inspection results:
   - `id_field` (warn if the sample has duplicates or nulls)
   - `title_field`, `time_field` and `uri_field`
   - x/y columns (CSV)
   - a `properties` allow-list, with ordering

   Show the sample in a table and on a map.
6. **Describe.**
   - **Resource id:** a URL-safe, unique slug. Warn that renaming it later breaks links, including any Geoconnex PIDs that redirect to it.
   - **Metadata:** title, description, keywords and links.
   - **Extents:** spatial (draw it on the map, or "use data extent") and temporal.
   - **Optional:** i18n strings and the linked-data context.
   - **Geoconnex (when on):** ask for `uri_field`, check that sample values look like `https://geoconnex.us/…` URIs, and link to the JSON-LD template options.
7. **Review.** Show the generated YAML for this resource and a diff against the current config. Show results from three layers of validation:
   - descriptor rules
   - pygeoapi's `validate_config` run on the *merged* config
   - the probe

   Errors block publishing; warnings don't.
8. **Publish and verify.**
   - Write to the chosen target (§1).
   - Regenerate OpenAPI, then trigger the reload or tell the user how to do it.
   - Check the live endpoint: `/collections/{id}` plus the data URL for the provider type.

   End with working links, or a precise statement of what's left (for example, "set `PYGEOAPI_OBS_DB_PASSWORD` on the server, then restart").

### J2: Browse and edit
- Load a config from a file or through the Admin API.
- List resources with a provider summary and a live health check for each.
- Edit through the same forms, duplicate, or delete (with confirmation).
- Import a pasted YAML resource snippet into the form.

Editing must be **lossless**: any keys, comments and ordering the UI doesn't understand stay exactly as they were.

### J3: Add a provider to an existing collection
For example, add tiles to a feature collection. Explain and enforce pygeoapi's rules for collections with several providers. Read how pygeoapi picks among them (`get_provider_by_type`, `get_provider_default`, the `default: true` key) rather than assuming.

## 5. Mode B: create new providers

### How it fits together

```
Template + mapping ──► provider spec ───┐
                            │           │
                          eject         ├──► contract harness ──► package + descriptor ──► Mode A catalog
                            ▼           │
Code workspace ──────► Python package ──┘
```

- **Template-built providers** are described by a **provider spec**: a declarative YAML document, validated by a JSON Schema and executed by a generic engine. This project ships the engine as its own pip-installable pygeoapi plugin package (working name `pygeoapi-spec-providers`). A spec is data, not code. Once the engine is reviewed and installed, stewards can add providers without anyone reviewing new Python each time.
- **Code-built providers** are ordinary Python packages that subclass pygeoapi's base classes.
- **Eject** converts a spec into equivalent, readable Python, for when a template gets you most of the way. It's one-way: afterwards the code is the source of truth, not the spec. Warn before ejecting.
- Decide whether a resource's `data` points at a spec file or the spec is inlined under `options`. Record the choice and the reason in `docs/decisions.md`.

Here is an illustrative spec. Design the real schema in `PLAN.md`.

```yaml
# workspace/providers/state-wells/spec.yaml
spec_version: 1
template: http-features
title: State groundwater wells
source:
  url: https://api.example.gov/v2/wells
  auth: {type: header, name: X-API-Key, value: "${STATE_WELLS_API_KEY}"}
  timeout_s: 20
  cache_ttl_s: 300
records: $.data.items               # JSONPath to the list of records
id: well_id
geometry: {type: point, x: longitude, y: latitude}
properties:
  - {name: well_id, type: string}
  - {name: depth_ft, type: number, from: depth}
  - {name: last_measured, type: datetime, from: lastMeasurementDate}
pagination: {style: offset, offset_param: offset, limit_param: limit, max_page_size: 500}
filters:                            # pushed down to the API where mapped; otherwise applied locally
  bbox: {param: bbox, format: "{minx},{miny},{maxx},{maxy}"}
  properties: {county: county_name}
limits: {max_records_scanned: 10000}
```

### Templates in v1
1. **HTTP JSON/GeoJSON API → Features.**
   - Auth: an API key in a header or query parameter, taken from an env var.
   - Mapping: record list path, id, geometry (lat/lon fields, WKT, GeoJSON geometry, or none), and properties (rename, set type).
   - Pagination: none, offset/limit, page/size, next link, or cursor.
   - Filters: bbox, datetime and property filters are pushed down to query parameters where the API supports them. Otherwise the engine filters locally, capped by `max_records_scanned`.
   - Caching and rate limiting.
2. **SQL query → Features.** A read-only `SELECT` (joins, views, computed columns) against PostgreSQL, MySQL or SQLite. The engine wraps it as a subquery and applies pygeoapi's filters as bound parameters. The spec declares the id column, the geometry column and its SRID, and a statement timeout.
3. **Time series → EDR**, for monitoring-location data such as stream gages or wells. It has three sources:
   - locations: an HTTP API, a SQL query, or an existing feature provider
   - parameters: id, label and unit, plus optional ODM2 and QUDT URIs when Geoconnex options are on
   - observations: an HTTP URL template, or SQL with placeholders for location id, parameter and time range

   It supports the `locations`, `cube` and `area` query types, plus `position` with a configurable snap radius. Responses are CoverageJSON point series or coverage collections.

Coverage, tile and map providers get code scaffolds only in v1.

### J4: Create a provider from a template
1. Pick a template and point it at a source: a URL and auth, or a database connection and query.
2. The backend fetches a sample through the sandbox, following the SSRF rules (§7). It suggests mappings: the record array, id-like fields, lat/lon or geometry fields, ISO dates, and pagination hints (`next` links, `total` counts, `offset` parameters).
3. The user confirms or adjusts the suggestions. Show the sample as a collapsible JSON tree where clicking a node sets a path, next to a table and map preview that update as the mapping changes.
4. The user says which filters and sorts the source can do itself, and how. The engine does the rest locally, and the UI explains the performance cost.
5. Run the contract harness. Fix and rerun until it passes.
6. Name, version and save. The descriptor is generated automatically. Offer "Publish a collection with this provider →", which opens J1 prefilled.

### J5: Write or refine provider code
- Start from either:
  - a scaffold for any provider type (feature, record, coverage, EDR with chosen query types, tile, map), or
  - an ejected template provider.
- Scaffolds get method stubs whose signatures and docstrings come from the **installed** base classes. Unimplemented methods raise `NotImplementedError`.
- Edit in the browser (CodeMirror, with `ruff` lint and format from the backend), or work locally and sync from a folder or Git repo. The harness works the same either way.
- Each created provider lives in its own Git-tracked folder under `workspace/providers/<name>/`, so history and diffs come for free.

### J6: Test, package and ship
- **Gate:** the contract harness must pass before packaging. Warnings are allowed.
- **Package contents:**
  - template-built: the spec plus a dependency on the runtime package
  - code-built: a pip-installable package with `pyproject.toml` (pygeoapi pinned), the provider module, the generated contract tests and fixtures, a README, an example config snippet, a CHANGELOG and the descriptor
- **Delivery:** download a zip, commit or open a PR against a Git repo (the route to production), or build a test image `FROM geopython/pygeoapi:0.24.0` with the package installed.
- **Versioning:** created providers use semantic versions. Editing a published provider makes a new version, and Mode A shows which collections use which version.

### The contract harness
One harness, shared by template-built and code-built providers, answers: "Will pygeoapi accept this provider, and does it behave the way clients expect?" It runs in the sandbox runner (§6) against the user's real sample data:
1. **Load:** `load_plugin` accepts the `provider_def`, and `get_fields()` has the shape that core providers use in the pinned version.
2. **Features and records:**
   - `query()` returns a valid FeatureCollection with correct counts, and honors `limit` and `offset`.
   - `bbox`, `datetime_`, `properties`, `sortby`, `select_properties`, `skip_geometry` and `resulttype='hits'` each either work or are declared unsupported, and the descriptor's `capabilities` match.
   - `get()` round-trips an id taken from `query()`, and an unknown id raises `ProviderItemNotFoundError`.
3. **EDR:** at least one query type is implemented, responses validate as CoverageJSON, and the parameters in responses match those declared by `get_fields()`.
4. **Coverages, tiles and maps:** checks derived from the base classes and core examples.
5. **Live:** start a disposable pygeoapi (pinned image) with the provider configured, and request its real endpoints as JSON, HTML and JSON-LD. Optionally run the OGC CITE suite for a conformance report.

Show the results as a checklist with plain-language explanations and fix hints. Also write the same checks into the package as pytest tests, so they keep running in CI.

## 6. Architecture requirements

- **Single renderer.** One backend function turns form state into the `provider_def` and resource dict. The YAML preview, validation, probe and publish step all call it. Never build YAML with string templates; build dicts and serialize them.
- **Config I/O.** Use `ruamel.yaml` in round-trip mode so comments, key order, quoting and `${VAR}` placeholders survive. Never resolve placeholders when loading a config for editing; resolve them only inside the sandbox. Writes must:
  - be atomic (temp file, then rename)
  - keep timestamped backups
  - validate before writing
  - detect concurrent edits (a hash or ETag of the file)
- **Sandbox runner.** Every Mode A probe and Mode B harness run happens in a fresh container built `FROM geopython/pygeoapi:<pinned>`, with the runtime package and the provider under test installed. Each run has:
  - a timeout, plus CPU and memory limits
  - network egress only to the hosts the provider declares
  - results returned as JSON

  This matches the deployment environment and contains providers that hang or pull heavy dependencies (GDAL, rasterio, database drivers). If the probe can't run in the same image that serves pygeoapi, say so clearly. In Phase 1, a subprocess worker with a timeout is enough.
- **Runtime package** (`runtime/`): the spec engine.
  - Depends only on pygeoapi and a few well-maintained libraries (for example httpx, a JSONPath library, SQLAlchemy, sqlglot).
  - Has its own tests and version, and installs into the pygeoapi image.
  - Resolves `${VAR}` in spec files the same way pygeoapi's `yaml_load` does.
- **Code generation.** Scaffolds and eject output are built from templates plus the installed base classes' signatures, formatted with `ruff format`, and covered by golden-file tests.
- **Backend API.** A starting sketch; refine it in `PLAN.md`:
  - Catalog: `GET /api/catalog`, `GET /api/catalog/{id}`
  - Mode A: `POST /api/probe`, `POST /api/render`, `POST /api/validate`, `GET /api/config`, `PUT /api/config`, `POST /api/publish`, `GET /api/resources/{id}/health`
  - Mode B: `POST /api/samples` (fetch a sample for mapping), `POST /api/specs/validate`, `GET /api/workspace`, `POST /api/workspace`, `POST /api/workspace/{id}/harness`, `POST /api/workspace/{id}/eject`, `POST /api/workspace/{id}/package`

  Keep an OpenAPI spec for this API and generate typed frontend clients from it.
- **Frontend.** Forms are rendered from the compiled schemas. Compare react-jsonschema-form with custom widgets (secret, field picker, CRS picker, bbox on a map, JSON-tree path picker) against a small in-house renderer, and record the choice in `docs/decisions.md`. Use MapLibre GL for maps and CodeMirror for YAML and Python. Meet WCAG 2.1 AA: labeled fields, keyboard navigation, focus that moves correctly between wizard steps, and errors linked to their fields.
- **Suggested layout:**
  - `backend/` and `frontend/`
  - `catalog/` (`_base/`, `core/`, `plugins/`)
  - `runtime/` (the spec engine package)
  - `harness/` (contract checks and the sandbox runner)
  - `scaffold_templates/`
  - `workspace/` (gitignored here; each created provider has its own Git repo)
  - `docker/` (compose: app, pygeoapi, PostGIS, sandbox base image)
  - `docs/` and `tests/`

## 7. Security (non-negotiable)

- **Probes and sample fetches can be abused for SSRF and file reads.**
  - Restrict file-based providers to configured data roots and block path traversal.
  - For URL-based providers, use a host allowlist and block link-local and metadata addresses such as 169.254.169.254.
  - Set timeouts everywhere.
- **Custom Python is arbitrary code execution by design.**
  - Run it only in disposable sandbox containers: non-root, no host mounts except its own workspace, resource limits, and egress only to declared hosts.
  - Never import user code into the builder's own backend process or into a production pygeoapi.
  - Code reaches production only as a reviewed package (Git PR plus CI).
  - If trusted local mode is chosen (§1), make it an explicit opt-in and show a persistent warning banner.
- **Specs are data.**
  - The engine never calls `eval` or `exec`, and never renders spec values as Jinja templates.
  - URL templates use a strict placeholder formatter that URL-encodes values.
  - A SQL spec must parse (for example with sqlglot) as a single `SELECT`. It runs in a read-only transaction with a statement timeout, and pygeoapi's filters are applied only as bound parameters.
  - HTTP sources follow the same host allowlist as probes.
- **Binding and auth.** Bind to 127.0.0.1 by default. When deployed, require auth (a reverse proxy or OIDC) and CSRF protection on every endpoint that changes state.
- **Secrets** never appear in YAML, specs, generated code, logs, API responses, error messages, browser storage or Git commits. Mask them in the UI.
- **Admin API.** Only the backend calls it, and the README must say plainly that it has to sit behind auth.
- **Never leave an invalid config on disk.** If a check after publishing fails, offer a one-click rollback to the backup.

## 8. Testing and verification

- **Unit tests:**
  - every catalog file validates against the descriptor schema
  - golden-file tests of form state → YAML for each Tier-1 provider
  - round-trip tests: comments, ordering, unknown keys and `${VAR}` all preserved
  - env-var placeholder naming
- **Contract tests:** every generated resource, merged into a base config, passes `validate_config`, `pygeoapi openapi generate` succeeds, and the drift check passes.
- **Runtime engine:** unit tests against a local mock API (offset, page, next-link and cursor pagination; auth; error responses; slow responses) and a PostGIS fixture.
- **Eject equivalence:** for each template, the ejected code and the spec-driven provider return identical results on the fixtures.
- **Scaffolds:** every provider type's scaffold installs and lints clean, and its contract tests run (`xfail` for stubs).
- **Harness self-tests:** core providers (CSV, GeoJSON) pass, and deliberately broken fixture providers fail with the expected messages.
- **Sandbox tests:** a provider that tries to reach an undeclared host or read outside its workspace is stopped.
- **Integration tests** (docker compose with `geopython/pygeoapi:0.24.0` and PostGIS): publish through each target, reload, and assert the endpoint serves data. Use the fixtures in pygeoapi's `tests/data` (for example `obs.csv`, `ne_110m_lakes.geojson`, `poi_portugal.gpkg`).
- **E2E tests** (Playwright):
  - the CSV and PostGIS happy paths
  - a wrong password shows a friendly error
  - editing an existing resource keeps unknown keys
  - create a provider from the mock JSON API, pass the harness, publish it through Mode A, and fetch its items from pygeoapi
- **Manual check in Claude Code.** Add `.claude/launch.json` entries for the dev servers, walk through both modes in the browser pane, and screenshot each step at desktop and mobile widths.
- **Lint and types:** `ruff` + `mypy` for the backend and runtime, `eslint` + `tsc --strict` for the frontend. A single command (`make check` or similar) runs everything.

## 9. Plan of work

**At the end of each phase, stop.** Run all checks, demo the result in the browser pane, and summarize what changed, what's next and any open questions. Then wait for my go-ahead.

- **Phase 0: Research and plan (no app code).**
  1. `git init` and install the pinned pygeoapi in a venv.
  2. Write `docs/pygeoapi-notes.md` (§2) and review the prior art.
  3. Write `PLAN.md` covering both modes: architecture, descriptor and spec schemas, API, sandbox design, milestones, risks.
  4. Ask me to confirm §1, plus anything you can't settle from the source.

  Use subagents for research that can run in parallel, such as one per provider family.
- **Phase 1: Walking skeleton (Mode A).**
  - backend, frontend, and a catalog with CSV, GeoJSON and PostgreSQL
  - the full J1 wizard with the export target
  - a subprocess probe worker
  - unit and contract tests
  - a docker compose setup in which pygeoapi serves the output
- **Phase 2: Mode B core.**
  - the spec schema, and the runtime package with the HTTP → Features template
  - the sandbox runner, with Mode A's probe moved onto it
  - the contract harness
  - J4 end to end, including the generated descriptor and the hand-off to J1

  End-of-phase demo: turn the mock JSON API into a live collection without writing code.
- **Phase 3: Publishing and editing.**
  - the file target, with round-trip safety and backups
  - the Admin API target
  - J2 and J3
  - checks after publishing, and rollback
  - versions for created providers
- **Phase 4: Mode B breadth.**
  - the SQL → Features and time series → EDR templates
  - the code workspace and scaffolds for every provider type (J5)
  - eject
  - packaging and delivery (J6)
- **Phase 5: Catalog breadth.**
  - finish Tier 1 and write the Tier 2 descriptors
  - add the org plugins
  - run the drift check in CI
  - add previews for coverages, EDR, tiles and maps
  - add the Geoconnex options
- **Phase 6: Hardening.**
  - a security review, including an attempt to escape the sandbox
  - an accessibility audit
  - `docs/adding-a-provider.md` (a descriptor for an existing plugin) and `docs/creating-a-provider.md` (a Mode B walkthrough)
  - a Dockerfile for the app
  - a README quickstart a new teammate can follow in 10 minutes

## 10. Definition of done (v1 = Phases 0–5)

**Mode A**
- [ ] For every Tier-1 provider, a user can finish J1 without seeing YAML, and an automated test confirms that pygeoapi 0.24 serves the result.
- [ ] Opening a hand-written, commented config and saving it with no changes produces a byte-identical file, or a documented minimal diff.
- [ ] No secret value ever reaches disk, and the required env vars are listed at publish time.
- [ ] Supporting another existing plugin takes only a descriptor file, and `docs/adding-a-provider.md` shows this with a worked example.
- [ ] Every hand-written descriptor key cites where it comes from in pygeoapi's source or docs.

**Mode B**
- [ ] A steward can turn a JSON API into a working collection without writing code. Tests use the mock API; the demo uses one real, keyless public API, documented in the README.
- [ ] The SQL → Features template publishes a multi-table join as a collection that passes the harness.
- [ ] The time series → EDR template returns valid CoverageJSON for each query type it supports, as checked by the harness (and by the CITE EDR suite, if you get it running).
- [ ] A developer can start a provider of any type from a scaffold, or eject a template-built one. The result installs, passes `ruff`, and passes the harness once its methods are implemented.
- [ ] Ejected code runs in stock pygeoapi without the runtime package and returns the same results as its spec.
- [ ] Custom code never runs outside the sandbox, and the sandbox tests prove the egress and file-access limits hold.

**Both**
- [ ] The drift check and the unit, contract, integration and E2E tests all pass from one command.

## 11. Out of scope for v1

- Editing `server`, `logging` and `metadata`. Show them read-only for now; editing comes in v2.
- OGC API - Processes resources (`type: process`) and process managers.
- No-code templates for coverage, tile and map providers. They get code scaffolds only.
- Deploying custom code to production straight from the browser.
- Multi-user roles and approvals. The Git PR target covers review.
- Reimplementing pygeoapi's validation or plugin loading. Always call pygeoapi itself.

## 12. Working agreements

- Never invent a config key. If you can't find it in the source or docs, ask me or leave it out.
- Generate provider code from the installed base classes, never from memory of pygeoapi's API.
- When the source and the docs disagree, trust the source for the pinned version and note the disagreement in `docs/pygeoapi-notes.md`.
- Record significant choices in `docs/decisions.md` as short ADRs.
- Commit in small, working steps.
- Prefer well-maintained, widely used dependencies, and justify each new one in a line.
