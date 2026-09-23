# PLAN: pygeoapi Provider Builder

**Status:** Phase 0 draft, for your review. **Target:** pygeoapi **0.24.0**.

Facts about pygeoapi are cited in [docs/pygeoapi-notes.md](docs/pygeoapi-notes.md),
written "notes §n" below. Choices are recorded as ADRs in
[docs/decisions.md](docs/decisions.md). This plan refines PROMPT.md, and says why
wherever it differs.

**Contents:** [1 Summary](#1-summary) · [2 Architecture](#2-architecture) ·
[3 Single renderer](#3-the-single-renderer) · [4 Config I/O](#4-config-io) ·
[5 Descriptors](#5-provider-descriptors) · [6 Mode A](#6-mode-a-configure) ·
[7 Mode B](#7-mode-b-create) · [8 Sandbox](#8-sandbox-runner) ·
[9 Security](#9-security) · [10 Backend API](#10-backend-api) ·
[11 Frontend](#11-frontend) · [12 Testing](#12-testing) ·
[13 Milestones](#13-milestones) · [14 Risks](#14-risks) ·
[15 Open questions](#15-open-questions)

---

## 1. Summary

**What we build.** A FastAPI backend, which imports pygeoapi 0.24.0 but never
imports user code, and a React frontend. They drive two modes over one pipeline:

```
 form state ──render()──► resource dict + provider_def ──► validate (4 layers) ──► publish ──► verify
     ▲                                                          │
     │ descriptors (catalog)                          sandbox: probe / harness /
     │                                                openapi generate / live check
 Mode B: spec or code ──► contract harness ──► package + descriptor ──► catalog
```

**What the research changed:**
1. **pygeoapi's own validation is weak.** `validate_config` is a schema-only check
   with misleading messages. The schema has a typo that lets untyped resources
   through, and it doesn't check provider names or keys (notes §6.1, §1.3).
   - We add a **runtime-rules layer**: about 15 cited rules, each derived from a
     500 or 400 we reproduced.
   - We run `pygeoapi openapi generate` in the sandbox as a smoke test.
2. **Editing must patch text, not re-serialize.**
   - Round-tripping through ruamel.yaml changes 16 of 18 pygeoapi sample configs.
   - The Admin API's PyYAML dump loses comments.
   - So edits are surgical text patches located by ruamel (ADR-0003).
3. **The Admin API is unsafe by default** (notes §10). It silently loses updates
   because its in-memory config goes stale. With `server.admin: true`, it leaks
   the resolved config, secrets included, through `/openapi`. It stays a
   *guarded* target (§6.4).
4. **Reload order matters.** A reload doesn't regenerate OpenAPI (notes §9.1).
   The file target writes OpenAPI **before** the config, then reloads.
5. **Org plugins are developed against the Internet of Water fork.** With
   upstream 0.24 they need dotted paths, and two are broken (notes §11).
   **Which pygeoapi do your deployments run?** It is question Q1 in §15, and
   it decides the sandbox base image.
6. **EDR engine classes must define their query methods in their own class
   body.** Advertised query types can be narrowed per spec on the instance
   (notes §5.2, ADR-0009).

**Scope.** Everything in PROMPT.md §1 defaults, delivered in Phases 1–5, with
hardening in Phase 6. Out of scope as in PROMPT.md §11.

## 2. Architecture

### 2.1 Components

```
┌──────────── browser (React + TS + Vite) ────────────┐
│ Mode A wizard · Browse/Edit · Mode B template &     │
│ code workspace · harness checklist · previews       │
└──────────────┬──────────────────────────────────────┘
               │ typed client (openapi-fetch), CSRF header
┌──────────────▼─────────── backend (FastAPI, 127.0.0.1) ──────────────┐
│ catalog (load + compile descriptors)   renderer (form → dicts)       │
│ config_io (ruamel locate, surgical     validation (schema · rules ·  │
│   patches, ETag, atomic, backups)        descriptor · probe)         │
│ publish (targets, reload, verify,      workspace (Git repos per      │
│   rollback, publish log)                 created provider)           │
│ codegen (scaffold, eject; ruff)        jobs (queue, SSE progress)    │
│ runner client ─────────────────────────────────────┐                 │
└────────────────────────────────────────────────────┼─────────────────┘
                                                     │ Docker Engine API
┌────────────────────────────────────────────────────▼─────────────────┐
│ sandbox container: FROM geopython/pygeoapi:0.24.0                     │
│  + pygeoapi-provider-harness (worker, checks) + pygeoapi-spec-provid.│
│  + provider under test; non-root; no host mounts except ro data/ws;  │
│  internal network → egress gateway (declared host:port only)         │
└───────────────────────────────────────────────────────────────────────┘
      targets: export · local config file (+ reload) · Admin API · Git PR
      dev stack (docker compose): pygeoapi:0.24.0 · PostGIS · mock API
```

### 2.2 Repository layout

```
backend/            FastAPI app: package `provider_builder`
frontend/           React + TypeScript + Vite
catalog/
  schema/           descriptor.schema.json (validates every catalog file)
  _base/            feature.yaml, record.yaml, coverage.yaml, edr.yaml, tile.yaml, map.yaml, stac.yaml
  core/             one file per core provider (csv.yaml, postgresql.yaml, ...)
  plugins/cgs-earth/  pygeoapi-plugins descriptors
runtime/            pip package `pygeoapi-spec-providers` (the spec engine)
harness/            pip package `pygeoapi-provider-harness`:
                    contract checks, sandbox worker, pytest plugin; host-side runner
scaffold_templates/ Jinja2 templates for code scaffolds and eject output
docker/             compose.yaml, sandbox/Dockerfile, gateway/, mock-api/, seed data
tools/              check_citations.py, drift check, catalog lint
docs/               notes, decisions, research, guides
tests/              cross-package integration and e2e tests (frontend e2e in frontend/e2e)
workspace/          gitignored; created providers, one Git repo each
vendor/             gitignored; upstream sources for reference and citations
```

It's one uv workspace (`backend`, `runtime`, `harness`) with one lock file
(ADR-0002). `runtime` and `harness` have no dependency on `backend`, so they
install alone into the pygeoapi image.

### 2.3 Processes and trust boundaries

| Process | Trust | Imports | Network |
|---|---|---|---|
| Backend | trusted | pygeoapi (schema, `yaml_load` semantics, base-class introspection). **Never** user code, never `load_plugin` | 127.0.0.1 only, plus configured targets |
| Sandbox container | untrusted | pygeoapi, harness, runtime, provider under test | declared hosts only, via gateway |
| Egress gateway | trusted, minimal | none (TCP forwarder) | the declared host:port list per run |
| Target pygeoapi | production | whatever the deployment has | n/a |

Anything that loads a provider runs in the sandbox, including core providers,
because they open user-supplied paths, URLs and DSNs. That covers
`load_plugin`, probes, sample fetches, the harness and `openapi generate` on
a merged config (it loads providers, notes §6.2). In Phase 1 the "sandbox" is
a subprocess worker (§8.1).

### 2.4 Data flow: J1

1. The frontend builds **form state** (§3).
2. The backend `render()`s it into dicts.
3. Four validation layers run (§6.2). Probe and OpenAPI generation are sandbox
   jobs.
4. Review shows the YAML fragment plus a diff against the target config.
5. Publish applies surgical patches (file target), regenerates OpenAPI in the
   sandbox, writes atomically, reloads, and verifies the live endpoints.
6. The result is working links, or a precise statement of what's left, with
   rollback on failure.

## 3. The single renderer

`render(form_state) -> RenderResult` is the only code that produces config.
Preview, validation, probe and publish all call it. It builds plain dicts and
never templates strings.

**Form state** (JSON, versioned):

```jsonc
{
  "form_version": 1,
  "resource_id": "obs",
  "resource": {                      // pygeoapi resource keys, except providers
    "type": "collection",
    "title": "Observations",          // or {"en": ..., "fr": ...}
    "description": "...", "keywords": ["..."],
    "links": [{"type": "text/html", "rel": "canonical", "title": "...", "href": "..."}],
    "extents": {"spatial": {"bbox": [...], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                "temporal": {"begin": "2000-10-30T18:24:39Z", "end": null}},
    "linked-data": {"context": [...]}, "templates": {"path": "/templates/obs"}
  },
  "providers": [{
    "descriptor": "CSV", "descriptor_rev": "sha256:…",
    "variant": null,
    "values": {"data": "/data/obs.csv", "id_field": "id",
               "geometry.x_field": "long", "geometry.y_field": "lat"},
    "secrets": {"data.password": {"env": "PYGEOAPI_OBS_DB_PASSWORD"}},  // names only, never values
    "extra": {}                    // keys the descriptor doesn't know (generic fallback, lossless edit)
  }],
  "base": {"target": "local", "etag": "sha256:…"}   // when editing an existing config
}
```

**Output:**
- `resource_id`
- `resource`: the full dict with `providers` in order
- `yaml`: the fragment in the target's detected style
- `env`: `[{name, used_by, secret, note}]`
- `warnings`

**Rules:**
- **Keys and order.** Dotted descriptor keys become nested dicts. Provider keys
  are emitted in canonical order: `type`, `name`, `data`, then descriptor field
  order, then `extra` in its original order.
- **Defaults.** A key equal to pygeoapi's default (the descriptor `default`, which
  is cited) is omitted unless the user set it explicitly. The descriptor field
  `suggest` pre-fills the form and is emitted.
- **Secrets.** Emitted only as `${NAME}`, never as values (§9). The name is
  suggested as `PYGEOAPI_<RESOURCE>_<FIELD>`, with a descriptor-provided field
  hint (`env_name: DB_PASSWORD`). Name rules:
  - uppercase `[A-Z0-9_]`, matching `\w+` from `yaml_load` (notes §2)
  - collisions get a `_2` suffix
  - editable in the UI
- **Placeholder style.** A placeholder is always a plain block scalar: never
  quoted, never inside a flow sequence, never in `|`/`>` (notes §2).
  Any list that contains a placeholder is emitted in block style.
- **Types.** Temporal extents are datetimes (unquoted RFC 3339) or `null`.
  Resource ids are strings (notes §1.2, §1.4).
- **Validation environment.** When a validation step needs resolved values,
  placeholders get the deployment values the user declared for non-secret
  variables, or type-appropriate dummies otherwise. A failure at a dummy
  location becomes a warning ("couldn't check `port` because `${DB_PORT}` is
  unknown"), not an error.

## 4. Config I/O

Implements ADR-0003.

- **Load.** Read bytes and compute `etag = sha256(bytes)`. Parse with
  `ruamel.yaml` in round-trip mode only to index node positions. Never resolve
  placeholders.
- **Detect the style.** Record per-file indentation (mapping indent, sequence
  indent and offset), quote style and line endings. These are needed when a
  block must be re-serialized.
- **Edit.** Diff the old resource dict against the new one, then apply minimal
  text patches:
  - replace a scalar in place, keeping its trailing comment
  - insert or delete a key's line span
  - replace the smallest enclosing block, in the detected style
  - append a new resource at the end of `resources:`

  If the result doesn't parse back to exactly the intended data, the write is
  refused. A save with no changes writes nothing: byte-identical by
  construction.
- **Write.** In this order:
  1. Validate the *resulting text* (§6.2 layers 1–2).
  2. Re-check the ETag, and return 409 with a diff on mismatch.
  3. Back up the current file to `<backup-dir>/<name>.<UTC timestamp>.<hash>.yml`,
     keeping the last N.
  4. Write a temp file in the same directory, fsync it, and `os.replace()`.
- **Import a snippet** (J2). Parse the pasted YAML, match each provider to a
  descriptor by `name`, and put unknown keys in `extra`.
  - Flag non-string keys and quoted placeholders.
  - Flag a legacy `template:` key, and offer the `templates.path` migration.
- **Measured normalisations** apply only inside blocks we must re-serialize:
  flow-list spacing, explicit `null`, trailing spaces, mixed indentation
  (ADR-0003). The review step shows them.

## 5. Provider descriptors

### 5.1 Descriptor schema (v1)

A catalog file is YAML. `catalog/schema/descriptor.schema.json` validates it in
CI. Example (to be verified in Phase 1 against the key inventory in
`docs/research/providers-features-services.md`):

```yaml
descriptor_version: 1
id: PostgreSQL                         # unique catalog id
plugin: PostgreSQL                     # provider `name`: short name or dotted path
provider_type: feature                 # extends catalog/_base/feature.yaml
title: PostgreSQL / PostGIS table or view
summary: Publish a table or (materialized) view as OGC API - Features.
ogc_api: [features]
source: [pygeoapi/provider/sql.py]     # where keys were verified
docs: https://docs.pygeoapi.io/en/latest/publishing/ogcapi-features.html#postgresql
requires: [sqlalchemy, geoalchemy2, psycopg2]   # import names checked inside the sandbox image
capabilities: {bbox: true, datetime: true, properties: true, sortby: true, select_properties: true,
               skip_geometry: true, q: false, hits: true, cql: true, transactions: true, crs_native: true}
variants:
  - id: fields
    title: Connection details
    fields:
      - {key: data.host, type: string, required: true, source: "pygeoapi/provider/sql.py:NNN"}
      - {key: data.port, type: integer, default: 5432, source: "..."}
      - {key: data.dbname, type: string, required: true, source: "..."}
      - {key: data.user, type: string, required: true, source: "..."}
      - {key: data.password, type: secret, required: true, env_name: DB_PASSWORD, source: "..."}
      - {key: data.search_path, type: "string[]", default: [public], source: "..."}
  - id: dsn
    title: Connection string
    fields:
      - {key: data, type: secret, format: postgresql-dsn, required: true, env_name: DB_DSN, source: "..."}
fields:
  - {key: table, type: string, required: true, choices_from: introspect.tables, help: "Tables and views", source: "..."}
  - {key: geom_field, type: field, default: geom, choices_from: introspect.geometry_columns, source: "..."}
  - {key: options.connect_timeout, type: integer, unit: s, advanced: true, drift: passthrough, source: "..."}
probe: feature
examples:
  - {id: docs-dict-form, source: "docs/source/publishing/ogcapi-features.rst:NNN-NNN", provider: {...}}
```

**Field vocabulary:**
- **Scalar types:** `string`, `text`, `integer`, `number`, `boolean`, `enum`
  (with `choices`), `datetime`.
- **Lists:** `string[]`, `number[]`.
- **Widget types:**
  - `secret`: always an `${ENV}` placeholder
  - `path`: file or dir inside a data root, with `extensions`
  - `url`: https unless allowed
  - `crs` and `crs[]`: CRS URIs, checked with pyproj in the sandbox
  - `bbox`
  - `field` and `field[]`: an ordered pick from probe fields; `field_types`
    filters them, e.g. `title_field` offers only `string`
  - `object`: a key/value editor
  - `yaml`: a raw fragment with live validation
- **Per-field attributes:**
  - `title`, `help`, `required`, `default` (pygeoapi's behaviour when absent;
    must be cited), `suggest`
  - `choices_from` (`probe.fields`, `introspect.tables`, …) and `show_if`
    (condition on other fields)
  - `advanced`, `unit`, `min`, `max`, `pattern`, `format`
  - `emit` (`if_set` | `always`), `env_name`
  - `drift` (`checked` | `passthrough` | `docs_only`)
  - `source` (required on hand-written descriptors), `docs`, `deprecated`
- **Descriptor-level:**
  - `variants`
  - `examples`: copied from pygeoapi docs or tests, each with a `source`
    range; these drive the golden tests
  - `probe` (`feature` | `record` | `coverage` | `edr` | `tile` | `map` | `none`)
  - `origin` (`core` | `plugin` | `created`), `version` (created providers)
  - `verdict` (`ok` | `known-issue` + `note`, e.g. for broken org plugins)
  - `geoconnex` hints

### 5.2 Base descriptors
Common keys come from `catalog/_base/<type>.yaml`, cited to `BaseProvider.__init__`
and the API (notes §1.7, §3.2).
- **`feature`**: `id_field`, `title_field` (string fields only),
  `uri_field` (Geoconnex checks), `time_field`, `properties` (ordered, unique,
  at least 1), `crs` (list of URIs), `storage_crs`, `always_xy`, `count`,
  `editable` (only when `capabilities.transactions`),
  `include_extra_query_parameters`, `languages`, `options`.
- **`tile`** and **`map`**: `format: {name, mimetype}` (required) and `options`.
  Tile adds `options.zoom.min/max` and `options.schemes`.
- **`coverage`**: `format` (required).
- **`edr`**: `x_field`, `y_field` and `time_field` where the provider reads them.
- **`record`** and **`stac`**: the matching subsets.
- A provider descriptor may override a base field's `default` or `help`, or hide
  it.

### 5.3 Compilation
The backend compiles each descriptor, merged with its base, into:
- a **JSON Schema** (draft 2020-12) over the *flat* form values (dotted keys),
  with `oneOf` for variants, `x-secret: true`, and `x-source` citations
- a **UI schema**: ordered groups (basic, then advanced), widget ids,
  `choices_from` bindings, `show_if`, help and docs links

The frontend renders from the UI schema and validates inline with ajv against
the JSON Schema (ADR-0004). The backend validates the same JSON Schema with
`jsonschema`. The renderer then maps flat values to the nested provider dict.

### 5.4 Availability badge
`requires` import names are checked **inside the sandbox image** once per image
digest, and cached. The badge describes the runtime that will serve the data,
not the builder's venv. For example: "ready", "missing `psycopg2`", or
"needs a derived image with `pygeoapi-plugins`".

### 5.5 Drift check, citations, extra catalogs
- **Drift check** (`tools/drift_check.py`, ADR-0010):
  - AST alias tracking across the provider class's MRO, compared with the
    descriptor's keys
  - reports missing and invented keys
  - `drift: passthrough` needs a citation
  - runs in CI and whenever pygeoapi is bumped; it also covers Mode B
    scaffolds' own descriptors
- **Citations:** `tools/check_citations.py` checks descriptor `source` fields as
  well as docs.
- **Extra catalogs:** directories listed in settings, or shipped by a Python
  package through the `pygeoapi_provider_builder.catalogs` entry point group.
  Our own discovery mechanism; pygeoapi itself has none.
- **Generic fallback:** any dotted path without a descriptor gets `type`,
  `name`, `data`, key/value `options` and a raw YAML fragment editor. It's
  validated by schema and probe only.

### 5.6 Coverage plan

| Tier | Providers | Tests | Phase |
|---|---|---|---|
| 1 | CSV, GeoJSON, PostgreSQL | golden + contract + e2e | 1 |
| 1 | SQLiteGPKG, OGR, ESRI, rasterio, xarray, xarray-edr, MVT-tippecanoe, MVT-proxy, WMSFacade, TinyDBCatalogue | golden + contract + integration (pygeoapi serves it) | 5 |
| 2 | the other 20 core providers | golden + schema validation; integration where cheap | 5 |
| Org | pygeoapi-plugins 0.6.0 (dotted paths; verdicts shown; broken ones flagged `known-issue`) | golden; live where fixtures exist | 5 |
| Created | Mode B output | the harness | 2–4 |

SQLiteGPKG could move into Phase 1 cheaply, since success criterion 1 is "I have a
GeoPackage" (question Q6).

## 6. Mode A: Configure

### 6.1 J1 wizard

Eight steps, each a route with a focusable heading.

1. **Start.** New collection, or add a provider to an existing one (J3).
2. **Backend.** A searchable catalog grouped by intent:

   | Intent | Provider type |
   |---|---|
   | vector features | feature |
   | gridded coverages | coverage |
   | EDR time series and positions | edr |
   | vector tiles | tile |
   | maps | map |
   | catalog records | record |
   | STAC | stac |

   Each card shows the OGC API it enables, its capabilities, availability, a
   docs link, origin, and for created providers the version and last harness
   result. "Create a new provider →" opens Mode B.
3. **Connect.** The descriptor form: inline validation, advanced options
   collapsed, and `path` pickers limited to data roots.
4. **Test and inspect** (sandbox job):
   - Every probe starts with `load_plugin`. Some providers load fine and then
     fail on the first query (CSV with a bad `id_field`, OGR with a bad layer),
     so the query step always runs.

   | Provider type | Probe |
   |---|---|
   | feature, record | `fields` plus `query(limit=5)` |
   | coverage | `fields` plus a tiny `subsets` or bbox query |
   | EDR | `get_fields()`, `get_query_types()` and a minimal query (`locations`, then `position` at the first location) |
   | tile | `get_tiling_schemes()` and one tile at min zoom |
   | map | a 64×64 image |

   - It returns field names and types, sample records, bbox and time range
     (from the sample, labelled as such, or whole-dataset when cheap), and
     `id_field` stats (duplicates, nulls).
   - Errors are shown in plain language (§6.3), with the raw traceback behind
     *Details*.
5. **Map fields.** Pickers for `id_field` (warn on duplicates or nulls),
   `title_field` (string fields only), `time_field`, `uri_field`, x/y columns
   (CSV), and an ordered `properties` allow-list. Next to them: a sample table
   and a map (MapLibre).
6. **Describe.**
   - **Resource id:** a slug; unique; not a sub-route word; warns that renaming
     breaks links and Geoconnex PIDs.
   - **Metadata:** title, description, keywords, links (each with a title).
   - **Extents:** draw the bbox or "use data extent"; temporal as begin/end
     timestamps or open.
   - **Optional:** i18n strings and the linked-data context.
   - **Geoconnex (when on):**
     - require `uri_field`, and check that every sampled value is present,
       unique and starts with `https://geoconnex.us/`
     - offer a JSON-LD template: a generated `templates.path` directory with
       `collections/items/item.jsonld`. Never the legacy `template:` key
       (notes §8)
7. **Review.** The YAML fragment, a diff against the target config (including
   any re-serialization normalisations), and results from the four layers
   (§6.2), with each finding linked to its field.
8. **Publish and verify** (§6.4). It ends with working links, or a precise to-do
   list, such as "set `PYGEOAPI_OBS_DB_PASSWORD` on the server, then restart".

### 6.2 Validation layers

Errors block publishing; warnings don't. Each finding carries a layer,
severity, code, JSON pointer to the form field, message, fix hint and raw
details.

1. **Descriptor schema.** The compiled JSON Schema, plus cross-field rules
   (variant completeness, x and y together, unknown-key "did you mean").
2. **pygeoapi schema on the merged config.** pygeoapi's own JSON Schema, run
   with `Draft202012Validator.iter_errors`. The `anyOf` branch is chosen by the
   resource `type`, and `uri`/`date-time` format checks are added, since
   pygeoapi skips them (notes §6.1). Placeholders use the validation environment
   (§3).
3. **Runtime rules.** A cited rule table, `backend/.../rules.py`, with one test
   per rule. Proposed severity is error unless noted (question Q3).

   | Rule | Why (notes) |
   |---|---|
   | resource `type` present | §1.3: 500 on `/collections` |
   | link `title` present | §1.5: collection vanishes |
   | temporal `begin` and `end` both present, unquoted RFC 3339 or null | §1.4 |
   | slug id, not a sub-route | §1.2 |
   | one provider per `type`; data provider is the default; no tile or map default | §7 |
   | `format` on tile, map and coverage providers | §1.7, §5.3 |
   | `title_field` names a string field | research config-model §15 |
   | `crs` is a list of URIs; `storage_crs` parses | §1.7 |
   | `server.languages` set | §1.6 (the server won't start) |
   | `metadata.identification.terms_of_service` and `metadata.license.url` present | §6.2 (OpenAPI generation fails) |
   | *(warning)* `server.limits.default_items` set | items HTML returns 500 without it |
   | *(warning)* `server.admin: true` with secrets in the config | §10: `/openapi` leak |

4. **Sandbox checks.**
   - the probe result for each provider
   - `pygeoapi openapi generate` on the merged config, with the fail flag **on**
     and to a temp file
   - the last traceback line mapped to plain language

### 6.3 Plain-language errors
An error catalog (`backend/.../errors.yaml`) maps (provider type, exception
class, message regex) to a message and a fix hint. It's built from the raw texts
recorded in the research notes, and each entry has a fixture test. Examples:

| Situation | Message |
|---|---|
| Connection refused | "Couldn't reach db.example.org:5432. Check the host, port and firewall." |
| Auth failed | "The database rejected the user name or password." |
| Missing file | "No file at /data/obs.csv, as the server sees it." |
| `InvalidPluginError` | "pygeoapi has no provider called 'X'. Did you mean a dotted path?" |

### 6.4 Publish targets

| Target | Write | Reload | Verify | Rollback |
|---|---|---|---|---|
| **Export** | copy or download the fragment or merged config | — | — | — |
| **Local file** | surgical patch; ETag; backup; atomic write. **OpenAPI is written first** (generated in the sandbox), then the config | per target: `none` (show instructions), `command` (e.g. `docker compose restart pygeoapi`), `signal` (HUP to gunicorn), or `watch` (server runs `--reload --reload-extra-file`, so the config write triggers it) | poll `/collections/{id}` (**not** only `/collections`, which hides failures, notes §1.5) and the type's data URL, with a timeout | one click: restore the backup of config and OpenAPI, then reload |
| **Admin API** (guarded) | `POST` or `PUT /admin/config/resources/{id}`, one write per publish | only valid if the target restarts workers after each write, so the builder requires `reload: watch` or `command` | as for the local file, plus re-read `GET /admin/config/resources/{id}` | `PUT` the previous definition, or `DELETE` a new one |
| **Git PR** | clone, surgical patch, branch, commit, push, `gh pr create` | after merge (outside the builder) | CI in the config repo runs our validator (we ship a GitHub Action snippet) | revert PR |

- **Guardrails for the Admin API:**
  - It refuses to publish when the target reports `server.admin: true` with
    secret-bearing providers, unless you acknowledge the `/openapi` leak.
  - It warns that the Admin API rewrites the whole file and drops comments.
- **Publish log.** Every publish appends `{time, target, resource_id,
  provider_id@version, config_hash, backup}` to a JSON Lines log (ADR-0013).

### 6.5 J2 and J3
- **J2** (load, list, edit, duplicate, delete, import):
  - Load from a file target or through the Admin API (read-only GET).
  - The list shows each provider summary and a health check (a
    `/collections/{id}` request).
  - Editing reuses J1's forms. Unknown keys go to `extra` and are preserved by
    the surgical edits.
  - Delete removes the resource's line span after confirmation.
  - "Import YAML" fills the form from a snippet.
- **J3** (add a provider to an existing collection) enforces the multi-provider
  rules in §6.2 and explains them. For example: "a collection can have one
  feature provider and one tile provider; the feature provider stays the
  default."

## 7. Mode B: Create

### 7.1 Where the spec lives (ADR-0005)
- `data:` is a path to the spec file (`data: /specs/state-wells/1.2.0/spec.yaml`),
  or a package resource (`data: pkg:state_wells_provider/spec.yaml`, resolved
  with `importlib.resources`).
- The engine also accepts an inline mapping under `data`, but Mode A never
  emits one by default.
- **Why:**
  - Specs are versioned artifacts reviewed in Git.
  - One spec can back several collections.
  - `data` already means "where the data comes from".
  - The pygeoapi config stays readable.
  - A packaged spec pins the version to the installed package.
- The engine loads spec files with `pygeoapi.util.yaml_load`, so `${VAR}`
  behaves exactly as in the config (notes §2).

### 7.2 Spec schema v1
The JSON Schema lives at `runtime/pygeoapi_spec_providers/schemas/spec-v1.json`.
Common keys: `spec_version: 1`, `template`, `id` (slug), `title`, `description`,
`version` (semver).

**Placeholder style.** `${VAR}` must be a plain block-style scalar, because spec
files are loaded with pygeoapi's `yaml_load` (ADR-0016). The illustrative spec in
PROMPT.md writes `auth: {…, value: "${STATE_WELLS_API_KEY}"}`. With `yaml_load`
that parses, but the value stays the literal text `${STATE_WELLS_API_KEY}`.
Without the quotes, inside `{…}`, it's a YAML `ParserError`. Only block style
resolves the key (all three executed). The spec validator rejects the first two
forms with a fix hint.

**`http-features`**
```yaml
template: http-features
source:
  url: https://api.example.gov/v2/wells           # https unless the settings allow http
  query: {format: json}                            # static params
  headers: {Accept: application/json}              # static, never secrets
  auth:                                            # header | query | bearer | basic; secrets from env only
    type: header
    name: X-API-Key
    value: ${STATE_WELLS_API_KEY}                  # plain scalar: yaml_load skips quoted or {flow} placeholders
  timeout_s: 20
  retries: {max: 2, backoff_s: 1, on_status: [429, 502, 503, 504]}
  rate_limit: {requests: 5, per_s: 1}              # per process
  cache_ttl_s: 300                                  # per process, in memory
  max_response_bytes: 20000000
  error_path: $.error                               # HTTP 200 with an error body (ESRI style)
records: $.data.items                               # RFC 9535 JSONPath (ADR-0006)
id: {from: well_id}                                 # field name or relative JSONPath; unique
geometry: {type: point, x: longitude, y: latitude, crs: http://www.opengis.net/def/crs/OGC/1.3/CRS84}
                                                    # point | wkt {field} | geojson {field} | none
properties:
  - {name: well_id, type: string}
  - {name: depth_ft, type: number, from: depth}
  - {name: last_measured, type: datetime, from: lastMeasurementDate, format: iso8601}  # iso8601|epoch_s|epoch_ms|strftime
time_field: last_measured
title_field: well_name
pagination:
  style: offset                                     # none | offset | page | next_link | cursor
  offset_param: offset
  limit_param: limit
  max_page_size: 500
  # page: {page_param, page_start, size_param}
  # next_link: {next: "$.links[?@.rel=='next'].href" | "header:Link"}
  # cursor: {cursor_param, cursor_path}
  total: $.meta.total                               # optional, gives numberMatched
filters:                                            # pushed down where mapped; otherwise applied locally
  bbox: {param: bbox, format: "{minx},{miny},{maxx},{maxy}"}
  datetime: {start_param: from, end_param: to, format: iso8601}
  properties: {county: county_name}
  sortby: {param: sort, format: "{property}:{order}", orders: {asc: asc, desc: desc}, properties: [depth_ft]}
  q: {param: search}
item: {url: "https://api.example.gov/v2/wells/{id}", record: $.data}   # optional direct get()
limits: {max_records_scanned: 10000, max_pages: 50}
```

**`sql-features`**
```yaml
template: sql-features
source:
  dialect: postgresql                               # postgresql | mysql | sqlite
  connection:
    host: db.example.org
    port: 5432
    dbname: gis
    user: reader
    password: ${WELLS_DB_PASSWORD}
  # or dsn: ${WELLS_DB_DSN}; or sqlite: {path: /data/x.sqlite} (inside a data root)
  connect_timeout_s: 5
  statement_timeout_s: 10
query: |
  SELECT w.well_id, w.name, w.geom, m.depth_ft, m.measured_at
  FROM wells w JOIN measurements m ON m.well_id = w.well_id
id: well_id
geometry: {column: geom, crs: http://www.opengis.net/def/crs/EPSG/0/4326, encoding: native}
                                                    # native | wkb | wkt | geojson | xy {x, y}
time_field: measured_at
title_field: name
properties: []                                      # optional allow-list; default is the introspected columns
limits: {max_rows: 10000}
```
- **Why a subquery.** pygeoapi's own SQL providers only reflect a table or view
  (`table:` can't hold a SELECT: research providers-features-services §1.1). The engine wraps the
  query instead:
  `SELECT * FROM (<query>) AS src WHERE <bound filters> ORDER BY <allow-listed columns> LIMIT :l OFFSET :o`.
- It runs behind the guard in ADR-0007.

**`timeseries-edr`**
```yaml
template: timeseries-edr
locations:
  from: http                                       # http | sql | provider
  http: {…same keys as http-features source/records/id/geometry/properties…}
  # provider: {name: CSV, data: /data/stations.csv, id_field: id, geometry: {x_field: lon, y_field: lat}}
  title_field: name
parameters:
  - id: water_level
    label: Water level
    unit: {symbol: m, label: metre}                # becomes x-ogc-unit (required, notes §5.2)
    observed_property: http://vocabulary.odm2.org/variablename/waterLevel/    # optional (Geoconnex)
    unit_uri: http://qudt.org/vocab/unit/M                                    # optional (Geoconnex)
observations:
  from: http                                       # http | sql
  http:
    url: https://api.tidesandcurrents.noaa.gov/api/prod/datagetter
    query: {product: "{parameter}", station: "{location_id}", begin_date: "{start:%Y%m%d}",
            end_date: "{end:%Y%m%d}", units: metric, time_zone: gmt, datum: MLLW, format: json}
    records: $.data
    time: {from: t, format: "%Y-%m-%d %H:%M", tz: UTC}
    value: {from: v, type: number}
    parameter_values: {water_level: water_level}  # spec parameter id → upstream value
  # sql: {query: "SELECT t, v FROM obs WHERE station = :location_id AND param = :parameter
  #                AND t BETWEEN :start AND :end ORDER BY t", time: t, value: v}
  default_window: P7D                              # when no datetime is given
  max_window: P31D
query_types: [locations, position, area, cube]
position: {snap_radius: {value: 1, units: km}}
limits: {max_locations: 50, max_observations: 10000}
```
- **Responses:** `locations` without an id returns a GeoJSON FeatureCollection;
  otherwise CoverageJSON, following the `sensorthings_edr` shape: a
  `CoverageCollection` of `PointSeries` with collection-level `parameters`,
  and range keys equal to parameter ids.
- **Not implemented in v1:** `instances`, because of the 0.24 instance-endpoint
  bugs (notes §5.2).

**Strict placeholder formatter** (URL templates and `format` strings):
- only `{name}` tokens, from a fixed set per context (`id`, `location_id`,
  `parameter`, `start`, `end`, `minx`, …)
- strftime specs allowed only on datetimes, from a directive allow-list
- values are URL-encoded; query parameters go through httpx `params`
- no attribute or index access, and never `str.format`
- unknown tokens are validation errors

### 7.3 Runtime package: `pygeoapi-spec-providers`
- **Dependencies:** pygeoapi (pinned), httpx, python-jsonpath, SQLAlchemy
  (already a pygeoapi dependency), sqlglot, shapely and pyproj (already
  pygeoapi dependencies). Each is justified in ADRs 0006 and 0007.
- **Classes** (`name:` in config):
  - `pygeoapi_spec_providers.http.HTTPFeatureProvider`
  - `pygeoapi_spec_providers.sql.SQLFeatureProvider`
  - `pygeoapi_spec_providers.timeseries.TimeSeriesEDRProvider`
- **The EDR class** defines `locations`, `position`, `area` and `cube` in its
  own body, and sets `self.query_types` per instance from the spec (ADR-0009).
- **Constructor.** It loads and validates the spec, then builds `_fields` (with
  `x-ogc-unit` for EDR). It's cheap, because pygeoapi instantiates providers per
  request (notes §3.1): spec parsing and HTTP clients are cached per process,
  keyed by spec path and mtime.
- **HTTP `query()` pipeline:**
  1. plan pushdown
  2. fetch pages (timeouts, retries, rate limit, TTL cache, size cap, redirects
     only to allowed hosts)
  3. extract records with JSONPath
  4. map records to Features, coercing types
  5. apply local filters (bbox with shapely, datetime, properties, `q`), sort,
     `select_properties`, `skip_geometry`
  6. slice to offset and limit; `numberMatched` from `total` or a complete scan
  7. always include `features`, even for hits (notes §5.1)
- **`get()`** uses `item.url` when set, else a capped scan. An unknown id raises
  `ProviderItemNotFoundError`.
- **Errors** are raised as `Provider*Error` with `user_msg` (notes §4). An
  unsupported `filter=` (CQL) is rejected with `ProviderInvalidQueryError`
  rather than ignored.
- **Delivery.** The package has its own tests, version and changelog. It's
  installed into the pygeoapi image with `/venv/bin/python3 -m pip install`
  (notes §9.2).

### 7.4 J4: create from a template
1. **Source.** Pick a template and give a URL and auth (the secret goes in as a
   test value, in memory only), or a DB connection and query.
2. **Sample.** A sandbox job fetches it (SSRF rules, §9). The backend computes
   suggestions from the JSON, which is only data:
   - the record array (the largest array of objects; names like
     `items`/`features`/`data`/`results`)
   - id candidates (unique, non-null)
   - geometry: a GeoJSON geometry, lat/lon pairs by name and range, or WKT by
     pattern
   - ISO or epoch datetimes, and property types
   - pagination hints: `next` links, the `Link` header, `total`/`count`,
     offset/page parameters in the URL, cursor tokens
3. **Map.** A collapsible JSON tree where clicking a node sets a path (keyboard
   operable), a table and a map that update live, and a confirm-or-adjust step
   for each suggestion.
4. **Filters.** Pushdown settings per filter. The UI states the cost of local
   filtering, e.g. "bbox is applied locally: up to 10,000 records scanned per
   request."
5. **Harness.** The checklist streams results. Fix and rerun.
6. **Save.**
   - Name and version it. That writes `workspace/providers/<id>/` (a Git repo)
     with `spec.yaml`, a generated `descriptor.yaml`, `CHANGELOG.md`, and
     harness results.
   - "Publish a collection with this provider →" opens J1 pre-filled.

### 7.5 J5: code workspace
- **Scaffolds for every type:** feature, record, coverage, EDR (pick query
  types), tile (MVT or raster) and map.
- **Generation:**
  - Jinja2 templates plus signatures and docstrings introspected from the
    **installed** pygeoapi base classes. The backend's pygeoapi version must
    equal the sandbox image's, checked at startup.
  - Stubs raise `NotImplementedError`.
  - The stubs already encode the 0.24 contract: `_fields` populated in
    `__init__`, `features` on hits, `user_msg` on errors, EDR methods in the
    class body with `**kwargs`, `x-ogc-unit`, tile `__repr__` and
    `get_tiles_service` returning `links`.
- **Package layout** (borrowing the cookiecutter's checklist, not its code):
  - `pyproject.toml` (hatchling; `pygeoapi==0.24.0`; ruff and pytest config)
  - `src/<pkg>/provider.py`, `src/<pkg>/descriptor.yaml`
  - `tests/test_contract.py` (the harness pytest plugin; stubs `xfail`) and
    `tests/fixtures/` (no network)
  - `examples/pygeoapi-config.snippet.yml` (with `${ENV}`), `README.md`,
    `CHANGELOG.md`, `.gitignore`, `Dockerfile.test`
    (`FROM geopython/pygeoapi:0.24.0`, installed with `/venv/bin/python3 -m pip`)
  - `.provider-builder.json` (answer replay, for regenerating or upgrading the
    scaffold)
- **Editing.** CodeMirror in the browser. `ruff check` and `ruff format` run in
  the backend as a subprocess over stdin; ruff never executes code. The
  workspace can also sync from a local folder or a Git remote, and the harness
  works the same either way.
- **Safe generated code.** Spec values are embedded in generated code only as
  Python literals, via `repr()` or AST construction. Identifiers are checked
  with `str.isidentifier()` plus a keyword check.

### 7.6 Eject
- **Result:** spec → a readable module that subclasses pygeoapi base classes
  directly, with **no runtime-package dependency**.
- **Readable output:**
  - Simple JSONPaths compile to plain indexing (`doc['data']['items']`).
  - Complex paths keep `python-jsonpath` as a dependency.
  - HTTP uses `requests`, which pygeoapi already depends on.
- **One-way:** the UI warns before ejecting, and afterwards the code is the
  source of truth.
- **Equivalence test:** for each template, the ejected provider and the spec
  engine return identical results on the fixtures (the mock API, SQLite and
  PostGIS).

### 7.7 Contract harness
Package `pygeoapi-provider-harness`. It runs inside the sandbox, and its checks
are pure functions over a loaded provider or an in-process `API`.

| Level | Checks |
|---|---|
| L1 load | `load_plugin` accepts `provider_def` (dotted `name`); `isinstance` checks; constructor is idempotent (two loads give equal `fields`) |
| L1 fields | `p.fields` is non-empty, dict of dicts with `type`, equals `get_fields()`; configured `id_field`/`title_field`/`time_field`/`uri_field`/`properties` exist; `title_field` is a string; EDR fields have `x-ogc-unit` |
| L1 features/records | FeatureCollection shape; `numberReturned == len(features)`; `numberMatched >= numberReturned`; `limit` honoured; `offset` pages disjoint and stable; `hits` has `features: []` and a correct `numberMatched`. Each of `bbox`, `datetime_`, `properties`, `sortby`, `select_properties`, `skip_geometry`, `q` works, or is declared unsupported; the descriptor's `capabilities` must match (declared but broken is a fail; working but undeclared is a warning). `get()` round-trips; an unknown id raises `ProviderItemNotFoundError`; errors are `Provider*Error` with `user_msg`. Transactions when `editable` |
| L1 EDR | query types non-empty, ⊆ the spec's or descriptor's, without `items`; each type called with the API's 17 kwargs returns CoverageJSON valid against the vendored OGC CoverageJSON schema (pinned); response parameters ⊆ `get_fields()`; `locations` without an id is a FeatureCollection |
| L1 coverage, map, tile | coverage: `_coverage_properties`, `axes`, `format`, a subset returns CoverageJSON and native bytes. Map: bytes of the requested size (PNG/JPEG decode). Tile: `tile_type`, `get_tiles_service()['links']`, tiling schemes, one tile's bytes decode (MVT) |
| L2 API in-process | build `API(config, openapi)` for a one-collection config; `/collections/{id}` returns 200 (the only call that instantiates coverage, tile, map and EDR providers for metadata); `/items`, `/items/{id}`, `/queryables`, `/schema` return 200; `get_oas()` with the fail flag contains `/queryables` (proving `_fields`) |
| L3 live | start gunicorn **inside the same container** (pinned image) on 127.0.0.1, then request JSON, HTML and JSON-LD for the type's endpoints |
| L4 optional | OGC CITE Features or EDR via TEAM Engine in a companion container. The images are amd64-only, so emulated and slow on Apple silicon; opt-in |

Results form a checklist (`pass`/`fail`/`warn`/`skip`/`unsupported`), each with a
plain explanation, a fix hint and details. The same checks ship as a pytest
plugin, so generated packages run them in CI.

### 7.8 J6: package, deliver, version
- **Gate:** the harness must pass (warnings are allowed).
- **Package contents:**
  - template-built: `spec.yaml` + `descriptor.yaml` + README + CHANGELOG +
    `pyproject.toml` depending on `pygeoapi-spec-providers`, so it can use
    `data: pkg:…`
  - code-built: the §7.5 package
- **Delivery:** a zip, a Git commit or PR to a remote (the route to production),
  or a test image `FROM geopython/pygeoapi:0.24.0` built in the sandbox builder.
  Builds execute build-backend code, so they're sandboxed and get no secrets.
- **Versions:** semver with Git tags. Editing a version that's in the publish
  log creates a new version. Mode A shows which collections use which version
  (ADR-0013).

## 8. Sandbox runner

### 8.1 Phase 1: subprocess worker
`python -m pygeoapi_provider_harness.worker` runs in a separate process with the
project venv (pygeoapi 0.24.0):
- JSON job on stdin, JSON result on stdout, size-capped
- a wall-clock timeout (kill the process group)
- `RLIMIT_CPU`
- secrets passed as environment variables to that process only
- only core providers, so no user code runs yet

It's *isolation from crashes and hangs*, not a security boundary. The UI says so.

### 8.2 Phase 2 onward: Docker
- **Images:**
  - `provider-builder/sandbox:0.24.0` is `FROM geopython/pygeoapi:0.24.0`, plus
    the harness and runtime packages via `/venv/bin/python3 -m pip install`,
    `USER 10001:0`, and `ENTRYPOINT` set to the worker. It **bypasses**
    `/entrypoint.sh`, which regenerates OpenAPI and aborts on any failing
    provider (notes §9.2).
  - A per-provider-version image, `FROM sandbox`, adds the code-built package.
    It's built once, keyed by content hash. Building needs network (PyPI), but
    running doesn't.
- **Run flags:**
  - `--rm --read-only --tmpfs /tmp:size=256m`
  - `--cap-drop ALL --security-opt no-new-privileges`
  - `--pids-limit 256 --memory 1g --cpus 1 --user 10001:0`
  - a backend-enforced timeout (then `docker kill`)
  - mounts: the provider's workspace read-only (Mode B), and data roots
    read-only **at their server paths** (Mode A file providers), so the config
    holds the paths production will see. Nothing else.
- **Egress:** a per-run `docker network create --internal` network (no route
  out), plus a small **egress gateway** container on that network and on the
  default bridge.
  - For each declared `host:port`, the gateway runs a TCP forwarder. The sandbox
    gets `--add-host host:<gateway-ip>`.
  - This works for HTTPS (TLS passes through end to end, so certificates verify)
    and for database protocols, which HTTP proxies can't carry.
  - The gateway resolves each declared host itself and refuses link-local and
    metadata addresses (`169.254.0.0/16`, `fd00:ec2::254`), plus private ranges
    unless the host is on the admin allowlist (e.g. `postgis:5432` in dev).
  - Undeclared names either don't resolve or have no route.
- **Results:** JSON on stdout (capped); stderr captured for *Details*.
- **Where the backend runs:** it talks to Docker through the Docker SDK. If the
  app itself runs in a container (Phase 6), socket access is root-equivalent, so
  the plan uses rootless Docker or a separate runner service (risk R4).
- **Tests** (Phase 2, extended in Phase 6):
  - A provider that connects to an undeclared host, `host.docker.internal`,
    `169.254.169.254` or the Docker socket is stopped.
  - One that reads outside its workspace, writes to a read-only mount, forks
    until it hits the limits, or sleeps past the timeout is stopped.
- **Not yet decided:** Docker Desktop on this Mac is old (CLI 20.10.23, Compose
  2.15.1) and the daemon was stopped (questions Q4 and Q5).

## 9. Security

| Threat | Mitigation | Test |
|---|---|---|
| SSRF or file read via probes and samples | host allowlist; block link-local and metadata; private ranges opt-in; data roots with `realpath` plus `commonpath` checks (symlinks resolved, `..` rejected); timeouts everywhere | unit + sandbox tests |
| Arbitrary code in custom providers | only in sandbox containers (§8.2); never imported by the backend; production only via a reviewed package (Git PR + CI) | sandbox escape tests (Phase 6) |
| Code injection through specs or eject | the engine has no `eval`, `exec` or Jinja over spec values; strict placeholder formatter; eject embeds literals via `repr()`/AST | fuzz tests of the formatter and codegen |
| SQL injection or abuse | ADR-0007: sqlglot full-tree check, function denylist, bound parameters, read-only transaction, statement timeout, least-privilege role | unit + PostGIS fixture |
| Secret leakage | secrets are `${ENV}` only; test values live in memory for one request; masked in the UI; scrubbed from logs, tracebacks and harness output (redact known test values); never in browser storage or Git (pre-commit check in workspaces); warning on the Admin API `/openapi` leak (notes §10) | tests that grep outputs for canary secrets |
| CSRF and DNS rebinding against 127.0.0.1 | ADR-0012: Host allowlist, Origin check, custom header on state-changing requests | tests |
| Unauthenticated Admin API | the backend is the only caller; README says it must sit behind auth; guardrails in §6.4 | — |
| Invalid config left on disk | validate before writing; atomic replace; post-publish verification with one-click rollback | integration |

## 10. Backend API

- **Style:** JSON everywhere; errors follow RFC 9457 problem details.
- **Jobs:** long operations run as jobs:

  | Endpoint | Returns |
  |---|---|
  | `POST …` | `202 {job_id}` |
  | `GET /api/jobs/{id}` | status and result |
  | `GET /api/jobs/{id}/events` | server-sent events |

- **Types:** FastAPI's OpenAPI document generates the TypeScript types
  (ADR-0011).

| Area | Endpoints |
|---|---|
| Meta | `GET /api/health`, `GET /api/settings` (read-only in v1: data roots, allowlists, targets, Geoconnex on/off) |
| Catalog | `GET /api/catalog?type=&q=`, `GET /api/catalog/{id}` (descriptor + compiled schema + UI schema), `GET /api/catalog/{id}/availability` |
| Mode A | `POST /api/render`, `POST /api/validate`, `POST /api/probe` (job), `GET /api/fs?root=&path=` (data-root browser) |
| Config and targets | `GET /api/targets`, `GET /api/targets/{t}/config` (text, ETag, parsed summary), `GET /api/targets/{t}/resources`, `GET /api/targets/{t}/resources/{id}` (form state + extra), `POST /api/import` (YAML snippet → form state), `GET /api/targets/{t}/resources/{id}/health` |
| Publish | `POST /api/publish` (job: diff, write, OpenAPI, reload, verify), `POST /api/publish/{pid}/rollback`, `GET /api/publish/log` |
| Mode B | `POST /api/samples` (job), `POST /api/specs/validate`, `POST /api/specs/preview` (job), `GET/POST /api/workspace`, `GET/PUT /api/workspace/{id}/files/{path}`, `POST /api/workspace/{id}/lint`, `…/format`, `…/harness` (job), `…/eject`, `…/package` (job), `…/versions`, `GET /api/workspace/{id}/usage` |

## 11. Frontend

- **Stack:** React 19, TypeScript (strict), Vite, React Router, TanStack Query,
  openapi-fetch, CodeMirror 6 (YAML and Python modes, lint from the backend),
  MapLibre GL, and ajv for inline validation.
  - Pin known-good majors in Phase 1: TypeScript 7 and ESLint 10 are new.
- **Routes:**
  - `/` for home
  - `/configure/new/:step`
  - `/configure/:target/resources` and `/configure/:target/resources/:id`
  - `/create/new`, `/create/:provider/{template|code|test|ship}`
  - `/catalog`
- **Form renderer** (ADR-0004): widgets for secret, path (data-root browser),
  field picker, ordered field list, CRS picker, bbox map with numeric inputs,
  and a JSON-tree path picker.
- **Maps:** MapLibre with a no-network default style (a blank background plus
  data). OSM raster tiles are optional, via settings.
- **Accessibility (WCAG 2.1 AA):**
  - every control labelled, with `fieldset`/`legend` for groups
  - an error summary at the top of each step linking to fields;
    `aria-invalid` and `aria-describedby`
  - on step change, focus moves to the step heading
  - everything keyboard-operable, including the JSON tree (tree role, arrow
    keys) and the bbox (numeric inputs alongside the map)
  - axe checks in Playwright; contrast tokens in light and dark themes

## 12. Testing

`make check` runs everything below and fails on any failure.

| Suite | Contents | From phase |
|---|---|---|
| Lint and types | ruff + mypy (backend, runtime, harness); eslint + `tsc --strict` | 1 |
| Docs | `tools/check_citations.py --strict docs/pygeoapi-notes.md` and descriptor `source` fields | 0 |
| Catalog | every file validates against `descriptor.schema.json`; drift check; examples render | 1 |
| Renderer | golden files for form state → YAML for each Tier-1 provider; env-var naming; placeholder style | 1 |
| Round trip | corpus of pygeoapi test configs + Geoconnex examples + fixtures: no-op save is byte-identical; property-based random edits touch only their lines; comments, order, unknown keys and `${VAR}` preserved | 3 (skeleton in 1) |
| Contract | every generated resource merged into a base config passes the pygeoapi-schema and runtime-rule layers, and `pygeoapi openapi generate` (fail flag on) in the pinned image | 1 |
| Runtime engine | mock API (FastAPI app with offset, page, next-link and cursor pagination, auth, error bodies, slow responses, 429) + SQLite + PostGIS fixture | 2, 4 |
| Eject equivalence | ejected vs spec-driven results identical on fixtures | 4 |
| Scaffolds | every type's scaffold installs, passes `ruff` and runs its contract tests (`xfail` for stubs) | 4 |
| Harness self-tests | CSV and GeoJSON pass; deliberately broken providers (string field shape, empty `_fields`, no `features` on hits, `get()` returning `None`, EDR without `x-ogc-unit`) fail with the expected messages | 2 |
| Sandbox | undeclared host, metadata IP, `host.docker.internal`, reads outside the workspace, timeouts, limits | 2 |
| Integration | docker compose (`geopython/pygeoapi:0.24.0` + PostGIS + mock API): publish through each target, reload, fetch data; fixtures from pygeoapi `tests/data` (`obs.csv`, `ne_110m_lakes.geojson`, `poi_portugal.gpkg`, …) | 1, 3 |
| E2E (Playwright) | CSV and PostGIS happy paths; wrong password gives a friendly error; editing keeps unknown keys; mock JSON API → harness → publish → items served; axe | 1–4 |
| Manual | `.claude/launch.json` dev servers; walk both modes in the browser pane; screenshots at desktop and mobile widths | each phase |

## 13. Milestones

Each phase ends with all checks passing, a demo in the browser pane, a summary
and a stop for your go-ahead.

**Phase 1: walking skeleton (Mode A)**
1. **Tooling:** uv workspace; Node via Homebrew; `make check`; CI workflow;
   `.claude/launch.json`.
2. **Backend skeleton:**
   - settings (targets, data roots, allowlist)
   - Host, Origin and CSRF middleware
   - catalog loader and compiler
   - descriptor schema; `_base/feature.yaml`; CSV, GeoJSON and PostgreSQL
     descriptors (cited); drift check v0
3. **Core logic:** the renderer; validation layers 1–3; the error catalog (first
   entries); the subprocess worker with probes; jobs with SSE; the export
   target.
4. **Frontend:** the app shell; the J1 wizard (all 8 steps); the form renderer
   and widgets (secret, path, field picker, ordered list, bbox map); YAML
   preview; sample table and map.
5. **docker compose:** `geopython/pygeoapi:0.24.0` (with `run-with-hot-reload`),
   PostGIS seeded with pygeoapi's fixture, and the exported config mounted.
6. **Tests:** unit, golden, contract (OpenAPI generation in the image),
   integration (pygeoapi serves `/collections/{id}/items`), and E2E for CSV and
   PostGIS plus the wrong-password case.

**Exit:** `make check` passes. The demo is CSV → export → pygeoapi serves items,
then the same for PostGIS, then a wrong password with its friendly error.

**Phase 2: Mode B core**
- spec schema v1; the runtime package with `http-features`; the mock API
- the Docker sandbox, gateway and images, with Mode A's probe moved onto it
- harness L1–L3 and its pytest plugin
- J4 end to end, with the generated descriptor and the hand-off to J1

**Exit:** the mock JSON API becomes a live collection without writing code. NOAA
CO-OPS stations work as the real-API demo (Q7).

**Phase 3: publishing and editing**
- the file target (surgical edits, ETag, backups, atomic write, OpenAPI then
  config, reload, verify, rollback)
- the guarded Admin API target
- J2, J3 and the runtime rules for multiple providers
- the publish log and created-provider versions
- the round-trip corpus

**Exit:** open, edit and save a commented config, with a documented minimal
diff and a byte-identical no-op save. Break a publish and roll it back.

**Phase 4: Mode B breadth**
- the `sql-features` and `timeseries-edr` templates, with CoverageJSON schema
  validation in the harness
- J5 (scaffolds for all types, CodeMirror, ruff, folder and Git sync)
- eject, with equivalence tests
- J6 (package, deliver, test image, versions)

**Exit:** a multi-table join is published and passes the harness. CO-OPS water
levels are served as EDR. A scaffolded provider of each type installs and
passes `ruff`.

**Phase 5: catalog breadth**
- Tier 1 complete with integration tests; Tier 2 descriptors
- org plugins (dotted paths, verdicts, derived image)
- the drift check in CI
- previews for coverage, EDR, tiles and maps
- the Geoconnex options (`uri_field` checks, a JSON-LD template library,
  `templates.path` generation, ODM2 and QUDT fields, RDF preview)

**Exit:** every Tier-1 provider finishes J1 without YAML, and pygeoapi serves
the result in CI.

**Phase 6: hardening**
- a security review, including sandbox escape attempts
- an accessibility audit
- `docs/adding-a-provider.md` and `docs/creating-a-provider.md`
- an app Dockerfile, with the runner-service decision (R4)
- a README quickstart a new teammate can follow in 10 minutes

## 14. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Surgical YAML patching is harder than estimated (block scalars, anchors, comments between keys) | DoD for lossless editing | round-trip corpus + property tests from Phase 1; fall back to re-serializing the smallest block and document the diff |
| R2 | Deployments run the IoW fork, not upstream 0.24 | wrong short names, `template:`, plugin behaviour | Q1; the sandbox base image is a setting, and the catalog can hold fork-specific descriptors |
| R3 | Docker Desktop is old or stopped; Apple-silicon-only images | sandbox and CITE | Q4/Q5; Phase 1 needs Docker only for compose tests; CITE is opt-in (amd64 emulation) |
| R4 | The backend's Docker socket access is root-equivalent | security of the deployed app | local-only in v1; rootless Docker or a runner service in Phase 6 |
| R5 | pygeoapi quirks change in a later release | rules and contracts drift | everything is cited to 0.24.0; a bump runs the citation, drift, golden and harness suites |
| R6 | Heavy dependencies (GDAL, xarray, psycopg2 builds) differ between the image and local venvs | "works in probe, fails in prod" | all provider loading happens in the pinned image from Phase 2; Phase 1's subprocess worker is labelled |
| R7 | CoverageJSON validity has no upstream validator | EDR harness false passes | vendor the OGC CoverageJSON schema (pinned) plus structural checks from sensorthings_edr |
| R8 | Scope: six phases, three templates, 33+ descriptors | schedule | a working product at every phase end; Tier 2 descriptors are generated from the research inventories, then reviewed |
| R9 | Env-var re-typing (`123456` becomes an int) surprises users | runtime errors in production | warn in the env-var list; the harness runs with typed values; the engine reuses `yaml_load` for parity |
| R10 | New frontend majors (TypeScript 7, ESLint 10, Vite 8) have rough edges | tooling churn | pin exact versions; upgrade deliberately |

## 15. Open questions

These are bundled into the Phase 0 confirmation message.

- **Q1. Which pygeoapi do your deployments run?** Upstream
  `geopython/pygeoapi:0.24.0`, or the Internet of Water fork (the Geoconnex
  examples and pygeoapi-plugins use it)? This decides the sandbox base image,
  the plugin short names and whether `template:` works.
- **Q2. The §1 parameters.** Confirm them, or reply "defaults".
- **Q3. Validation strictness.** Should runtime-rule violations (each a
  reproduced 500 or 400) **block** publishing (recommended), or only warn? And
  allow i18n `title_field` and link titles, which work at runtime but fail
  pygeoapi's schema?
- **Q4. Docker.** OK for me to start Docker Desktop when needed, and to suggest
  updating it (CLI 20.10.23, Compose 2.15.1)?
- **Q5. Node.** OK to `brew install node` at the start of Phase 1?
- **Q6. SQLiteGPKG in Phase 1**, for the "I have a GeoPackage" criterion?
- **Q7. Real keyless API for the Mode B demo:** NOAA CO-OPS (stations → Features,
  water levels → EDR). Both work without a key (checked 2026-09-23). OK?
- **Q8. Admin API target.** Keep it in Phase 3 with the guardrails in §6.4, or
  defer it to v2 given its lost-update and secret-leak behaviour?
- **Q9. Hierarchical ids** (`a/b`, documented by pygeoapi): opt-in in v1, or
  slugs only?
- **Q10. The harness in generated packages.** Is it OK for generated packages
  to depend on `pygeoapi-provider-harness`, published to PyPI eventually (under
  which org?), or should their tests be fully self-contained?
- **Q11. Upstream fixes.** Should we report the pygeoapi bugs we found (schema
  typo, link `title`, temporal `KeyError`, `options` `$ref`, EDR instance
  errors, the Admin API issues) upstream?
- **Q12. Git hosting** for created providers and config PRs: which GitHub org or
  repos?
