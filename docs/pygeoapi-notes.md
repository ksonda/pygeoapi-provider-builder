# pygeoapi 0.24.0: notes for the Provider Builder

**Pinned version:** pygeoapi **0.24.0** (PyPI, 2026-07-28, `requires_python >=3.12`).
Source: tag `0.24.0`, commit `f765a64`, cloned at `vendor/pygeoapi`. The installed
wheel is byte-identical to the tag's `pygeoapi/` tree (`diff -rq`, 2026-09-23).

**Citations.**
- `path:line` is relative to the pygeoapi repo root.
- `repo:path:line` points into another repo cloned under `vendor/`.
- `tools/check_citations.py` checks every citation against the checkouts. Run
  it with `--strict` for this file.

**Evidence.**
- *Executed* means a script ran against 0.24.0.
- *Read* means taken from the source.
- The detailed evidence, including experiment logs and per-provider key tables,
  is in [`docs/research/`](research/). Each section below links to its source
  note.

---

## 0. Verdicts on PROMPT.md §2

✓ confirmed · ⚠ true, with a catch that affects our design · ✗ wrong for 0.24.0.

| # | Claim in PROMPT.md | Verdict |
|---|---|---|
| 1 | Top-level sections are `server`, `logging`, `metadata`, `resources` | ✓ Also an optional `pubsub` section (§1.1) |
| 2 | Resource ids are used as-is in `/collections/{id}` | ⚠ No normalisation, escaping or validation at all. Some ids are unreachable (§1.2) |
| 3 | Resource `type` is one of `collection`, `stac-collection`, `process` | ⚠ A schema typo lets a resource with **no** `type` pass validation, and that breaks `/collections` with a 500 (§1.3) |
| 4 | A collection requires `title`, `description`, `keywords`, `extents`, `providers`; `extents` needs `spatial.bbox`; `crs` defaults to CRS84 | ⚠ The CRS84 default exists only in the schema's documentation, not in code (§1.4) |
| 5 | Optional `links` (each needs `type`, `rel`, `href`), `linked-data.context`, `visibility`, dataset-level `templates` | ⚠ Code also requires a link `title`. `templates` isn't in the schema, and only its `path` works (§1.5, §8) |
| 6 | `title`, `description`, `keywords` can be i18n dicts | ✓ (§1.6) |
| 7 | Provider `type` is one of 7 values; `type`, `name`, `data` are required | ✓ (§1.7) |
| 8 | Common optional provider keys, including `validator` and `formatters` | ✗ **`validator` doesn't exist in 0.24.0.** `formatters` is a *resource* key. `uri_field`, `always_xy` and top-level `x_field`/`y_field`/`z_field` are read by code but aren't in the schema (§1.7) |
| 9 | CSV uses `geometry: {x_field, y_field}` | ✓ (§1.7) |
| 10 | The schema doesn't forbid extra provider keys | ✓ It forbids extra keys **nowhere**, and one surprise: `options` is typed with PostgreSQL's option schema for *every* provider (§1.8) |
| 11 | `yaml_load` substitutes `${VAR}` and `${VAR:-default}`; an undefined var raises | ⚠ Only in plain (unquoted, non-block) scalars, and the result is re-typed: `PASSWORD=123456` becomes an int (§2) |
| 12 | The `PLUGINS['provider']` registry lists 33 short names | ✓ Exactly the list in PROMPT.md (§3.1) |
| 13 | A dotted `name` is imported as a class path; an unknown short name raises `InvalidPluginError` | ⚠ An unimportable dotted path raises `ModuleNotFoundError`/`AttributeError`, not `InvalidPluginError` (§3.1) |
| 14 | Base classes `BaseProvider`, `BaseEDRProvider`, `BaseTileProvider`, `base_mvt` | ⚠ `BaseTileProvider` is **not** a `BaseProvider` subclass. Coverage and map providers subclass `BaseProvider` directly (§3.2) |
| 15 | `BaseProvider` methods and `Provider*Error` exceptions | ⚠ A client sees an error's text only if it's passed as `user_msg=` (§4) |
| 16 | Provider keys are documented only in modules and rst, which disagree (e.g. ESRI `crs: 4326`) | ✓ `crs: 4326` fails the schema, and `esri.py` never reads `crs` (§1.7) |
| 17 | Features `query()` signature, `@crs_transform`, `numberReturned`/`numberMatched` | ⚠ The API passes exactly 13 kwargs. `features` must be present even for `hits`. `_fields` must be filled in the constructor (§5.1) |
| 18 | `get()` raises `ProviderItemNotFoundError` for an unknown id | ✓ Returning `None` instead gives HTTP 400, not 404 (§5.1) |
| 19 | `get_fields()` returns `{'name': {'type': 'string'}}` in code but `{'name': 'string'}` in the docs | ✓ The docs' shape crashes `/queryables`, `/schema` and OpenAPI generation (§5.1) |
| 20 | EDR query types are registered by `__init_subclass__`; older releases used `register()` | ⚠ Confirmed for 0.24.0, and only methods in the class's *own* body count. The older mechanism is unverified: the clone is shallow (§5.2) |
| 21 | EDR `query()` dispatches on `query_type`; `get_parameters()` builds CoverageJSON parameters | ⚠ Every field needs `x-ogc-unit`, or the collection disappears (§5.2) |
| 22 | `pygeoapi config validate` = `validate_config` | ⚠ It's a schema check only: no provider loading, no `format` checks, and often misleading messages (§6.1) |
| 23 | `pygeoapi openapi generate` loads providers, so it can double as a smoke test | ⚠ Only with the default flag on, and it skips hidden collections and needs metadata keys the schema calls optional (§6.2) |
| 24 | The server reads `PYGEOAPI_CONFIG` once; `gunicorn --reload --reload-extra-file` reloads | ⚠ The reload restarts workers, but **OpenAPI is not regenerated** (§9.1) |
| 25 | Admin API routes, 400 on duplicate POST, validate then regenerate OpenAPI, no auth, keeps `${VAR}`, likely drops comments | ⚠ All true, but its in-memory config is never refreshed, so later writes **silently undo earlier ones**. It drops comments. With `server.admin: true`, **resolved secrets appear in the public `/openapi`** (§10) |
| 26 | byteroad/pygeoapi_config configures only WMSFacade, PostgreSQL, MVT-proxy | ✓ (§11) |
| 27 | cgs-earth/pygeoapi-plugins provides CKAN, GeoPandas, SPARQL, PseudoPostgreSQL, MVT providers | ⚠ It's tested against the **Internet of Water fork**, and its short names exist only there. With upstream 0.24 we must use dotted paths, and two providers are broken (§11) |
| 28 | The Geoconnex examples use a resource-level `template:` key | ⚠ It's a legacy fork feature that core 0.24 ignores. The 0.24 equivalent is `templates.path` (§8) |
| 29 | The USGS cookiecutter targets Python 3.9 | ✓ It's also stale (2022) and isn't a provider scaffold (§11) |
| 30 | OGC CITE suites run in Docker via TEAM Engine | ⚠ The images are amd64-only (§11) |

---

## 1. Config model

Source note: [research/config-model.md](research/config-model.md).

### 1.1 Top level
- The schema is draft 2020-12 (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:1`).
- It requires `server`, `logging`, `metadata` and `resources` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:752`).
- An optional `pubsub` section is declared at `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:170`.
- There's no `additionalProperties` anywhere in the schema (§1.8).

### 1.2 Resource ids
- Any key is accepted: `patternProperties` `^.*$` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:372`).
- Lookup is exact and case-sensitive, with no normalisation.
- Links are built by f-string with no percent-encoding (`pygeoapi/api/collection.py:251`).
- Routes take the id as a `path` converter, so `a/b` ("hierarchical collections") works (`pygeoapi/flask_app.py:234`).
- Executed results:
  - An id whose last segment is a sub-route, such as `lakes/items`, can't be reached.
  - Spaces, `?` and `#` produce broken self-links.
  - Unquoted YAML keys `123:` or `yes:` become int or bool keys and return 404.
- **Builder rule:** slug ids by default (`[A-Za-z0-9._-]`, alphanumeric ends), `/` only
  by explicit opt-in, never a trailing sub-route segment, and always emitted as YAML strings.

### 1.3 Resource `type`
- The resource schema is an `anyOf` of two branches (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:374`). Branch 0 has `type` ∈ {`collection`, `stac-collection`} (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:383`) and a `required` list (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:663`).
- The process branch misspells `required` as `requred:` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:693`). So `{}`, or a resource with no `type`, passes `validate_config`.
- At runtime `filter_dict_by_key_value` does `v[key] == value` over every resource (`pygeoapi/util.py:571`). A resource with no `type` makes `/collections` and `/conformance` return 500. *Executed.*
- **Builder rule:** always require `type`.

### 1.4 `extents`
- **`spatial.bbox`:** 4 to 6 numbers, the only required key of `spatial` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:462`). Order and range aren't checked.
- **`spatial.crs`:** the CRS84 default is documentation only. `gen_collection` copies `crs` only when present (`pygeoapi/api/collection.py:107`), and dataset JSON-LD `spatial` becomes `null` without it (`pygeoapi/linked_data.py:147`). **Builder rule:** always write `crs`.
- **`temporal`:** `validate_datetime` indexes both `te['begin']` and `te['end']` (`pygeoapi/api/__init__.py:1191`). Executed:
  - A missing `end` gives 500 on any `datetime=` query.
  - A quoted or date-only `begin` gives 400 "Configured times should be RFC3339".
  - **Builder rule:** always write both keys, each an unquoted RFC 3339 timestamp or `null`.

### 1.5 `links`
- The schema requires only `type`, `rel` and `href` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:439`).
- But `gen_collection` reads `link['title']` unconditionally (`pygeoapi/api/collection.py:150`). Without it, executed:
  - the collection silently vanishes from `/collections` (the exception is swallowed at `pygeoapi/api/__init__.py:955`)
  - `/collections/{id}` returns 500
- **Builder rule:** require a link `title`, and always write `length` for `rel: enclosure`, which otherwise triggers a HEAD request on every describe (`pygeoapi/api/collection.py:158`).

### 1.6 i18n
- `title`, `description` and `keywords` are `i18n_string`/`i18n_array` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:697`), i.e. a string or a dict keyed by language tag.
- Resolution is `l10n.translate` (`pygeoapi/l10n.py:214`).
- `server.languages` (or `language`) is **required at runtime**, though the schema doesn't require it. `get_locales` raises `LocaleError` (`pygeoapi/l10n.py:444`). *Executed.*
- The schema types link `title`/`href` and provider `title_field` as plain strings, although code translates them (`pygeoapi/api/itemtypes.py:680`). An i18n dict there fails validation but works at runtime.

### 1.7 Provider keys
- **Schema:**
  - `type` enum of 7 (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:522`)
  - `data` is a string or an object (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:540`)
  - required keys are `type`, `name`, `data` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:640`)
- **`BaseProvider.__init__`** raises `RuntimeError('name/type/data are required')` (`pygeoapi/provider/base.py:67`). It reads:
  - `editable` and `count` (via `str2bool`) (`pygeoapi/provider/base.py:69-70`)
  - `options` (default `None`), `id_field`, `uri_field` (`pygeoapi/provider/base.py:71-73`)
  - `x_field`/`y_field`/`z_field`, `time_field` and `title_field` (`pygeoapi/provider/base.py:74-78`)
  - `properties`, `file_types` and `include_extra_query_parameters` (`pygeoapi/provider/base.py:79-81`)
  - `storage_crs`, parsed with pyproj (`pygeoapi/provider/base.py:86`)
- **Read by the API, not the provider:**
  - `crs`, the list of output CRSs (`pygeoapi/crs.py:111`)
  - `always_xy` (`pygeoapi/crs.py:556`)
  - `geom_field` (`pygeoapi/api/itemtypes.py:499`)
  - `languages` (`pygeoapi/l10n.py:473`)
  - `default` (`pygeoapi/provider/__init__.py:81`)
  - `format.name`/`format.mimetype` for coverage, map and tile providers (`pygeoapi/api/coverages.py:193`)
- **`validator`** appears nowhere in 0.24.0 code, docs or tests (grep, *executed*). **`formatters`** is a resource-level list (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:644`), loaded by `get_dataset_formatters` (`pygeoapi/util.py:779`).
- `uri_field`, `always_xy`, top-level `x_field`/`y_field`/`z_field`, `geom_field`, `file_types` and `languages` aren't in the schema. They're accepted untyped.
- **CSV** uses `geometry.x_field`/`geometry.y_field` (`pygeoapi/provider/csv_.py:61-62`), declared in the schema at `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:559-566`.
- **ESRI `crs: 4326`** (docs example, `docs/source/publishing/ogcapi-features.rst:166`):
  - It fails the schema, because `crs` must be a list of URIs (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:608`).
  - If forced through, `get_supported_crs_list` iterates it and requests fail with 500. *Executed.*
  - `esri.py` never reads `crs`. It sends `outSR` from `storage_crs` (`pygeoapi/provider/esri.py:64`).
  - **Correct 0.24 form:** omit `crs`, or give a list of CRS URIs, and set `storage_crs` for a non-CRS84 service.
- **Tile and map providers need `format: {name, mimetype}`** in practice. `is_vector_tile` indexes it when a tile provider is the collection default (`pygeoapi/api/collection.py:82`), and map links read it (`pygeoapi/api/collection.py:362`).

### 1.8 What the schema doesn't check
- It has no `additionalProperties` at any level. Typos like `titel:` are silently ignored, which is why descriptors must flag unknown keys.
- `validate_config` calls `jsonschema_validate(...)` with no `format_checker`, so `format: uri` and `format: date-time` aren't enforced (`pygeoapi/config.py:83`).
- **Surprise:** provider `options` is `oneOf` the PostgreSQL options schema (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595`). So `options: {pool_size: "x"}` fails even on a CSV provider. *Executed.*

## 2. Environment variables (`yaml_load`)

Source note: [research/config-model.md §6](research/config-model.md).

- **Regex:** `r'.*?\$\{(?P<varname>\w+)(:-(?P<default>[^}]*))?\}'` (`pygeoapi/util.py:143`).
- **Mechanism:** a PyYAML implicit resolver on a `SafeLoader` subclass (`pygeoapi/util.py:167-170`). Substitution happens per **plain** scalar after parsing, so:
  - quoted `"${X}"` and block scalars `|` / `>` are **not** substituted
  - flow collections `[${A}]` don't even parse
  - keys *are* substituted
  - there's no nesting
  - a default applies only when the variable is **unset** (an empty value stays empty)
  - *executed matrix* in the source note
- **Missing variable:** `EnvironmentError` (i.e. `OSError`), "Could not find the 'NAME' environment variable" (`pygeoapi/util.py:158-159`).
- **Re-typing:** the whole substituted string goes through `get_typed_value` (`pygeoapi/util.py:165`). So `port: ${PORT}` becomes an int (good), but so does `PASSWORD=123456`, and `1.10` becomes the float `1.1`.
- **`get_config(raw=True)`** uses plain `yaml.safe_load` and keeps placeholders (`pygeoapi/config.py:58`). The Admin API uses this.
- **Builder rules:**
  - Emit placeholders only as plain block-style scalars.
  - Never resolve placeholders when loading a config for editing.
  - The runtime package's `${VAR}` resolver should reuse `pygeoapi.util.yaml_load` itself on spec files, so its semantics are identical (see PLAN.md §7).

## 3. Plugins and base classes

Source note: [research/provider-contract.md §1–2, §8–9](research/provider-contract.md).

### 3.1 Loading
- `PLUGINS` has five categories: `provider`, `formatter`, `process`, `process_manager` and `pubsub` (`pygeoapi/plugin.py:39`).
- The 33 provider short names are exactly those in PROMPT.md §2.
- `load_plugin` handles names as follows:
  - An undotted name that isn't registered raises `InvalidPluginError` (`pygeoapi/plugin.py:118`).
  - A dotted name is split with `rsplit('.', 1)` and imported (`pygeoapi/plugin.py:123`).
  - The class is instantiated with the whole provider dict (`pygeoapi/plugin.py:133`).
  - An unimportable module or a missing class propagates `ModuleNotFoundError` or `AttributeError`.
- There's no entry-point discovery for providers. Third-party providers, including everything Mode B creates, are referenced by dotted path.
- Providers are instantiated **per request**, and for OpenAPI generation. Coverage, tile, map and EDR providers are also instantiated with `load_plugin` on every `/collections` request (`pygeoapi/api/collection.py:333`). Constructors must be cheap.

### 3.2 Base classes
- **`BaseProvider`** (`pygeoapi/provider/base.py:48`) serves features, records, coverages and maps. Its methods raise `NotImplementedError` by default.
- **`fields`** is a property that returns `self._fields`. It never calls `get_fields()` (`pygeoapi/provider/base.py:106`), so **constructors must store `_fields`**.
- **`BaseEDRProvider(BaseProvider)`** (`pygeoapi/provider/base_edr.py:42`) adds query-type registration and `get_parameters()` (§5.2).
- **`BaseTileProvider`** (`pygeoapi/provider/tile.py:44`) is **not** a `BaseProvider` subclass. It reads `format.name`, `format.mimetype` and `options.schemes`, so `options` is effectively required (`pygeoapi/provider/tile.py:58-61`).
- **`BaseMVTProvider(BaseTileProvider)`** (`pygeoapi/provider/base_mvt.py:43`) sets `tile_type = 'vector'`. Subclasses must implement `__repr__` and `get_tiles_service`, which has to return `{'links': […]}`.

## 4. Errors and HTTP status

Source note: [research/provider-contract.md §3](research/provider-contract.md).

- The root is `GenericError`, with `http_status_code`, `ogc_exception_code` and `default_msg`. Its `message` property returns `user_msg` or `default_msg` (`pygeoapi/error.py:51`).
- `ProviderX('text')` shows the client only `default_msg`. To expose the text, pass `user_msg=`. This matters for generated code.
- **Status mapping** (defined at `pygeoapi/provider/base.py:322-387`):

  | Exception | HTTP status |
  |---|---|
  | `ProviderGenericError`, `ProviderConnectionError`, `ProviderQueryError`, `ProviderInvalidDataError` | 500 |
  | `ProviderTypeError` | 400 |
  | `ProviderInvalidQueryError` | 400 `InvalidQuery` |
  | `ProviderItemNotFoundError` | 404 |
  | `ProviderNoDataError` | 204, no body |
  | `ProviderRequestEntityTooLargeError` | 413 |

- A non-`Provider*` exception from `query()` isn't caught: it's a bare 500 with no OGC body. *Executed.*
- **Plain-language messages** must therefore be derived from the exception class *and* the raw driver text. The provider notes record the raw texts for bad credentials, unreachable hosts and missing tables per provider.

## 5. Provider contract (what Mode B must produce)

### 5.1 Features and records

Source note: [research/provider-contract.md §5–6](research/provider-contract.md).

- **What the API passes.** It calls `p.query(...)` with exactly 13 keywords (`pygeoapi/api/itemtypes.py:557`): `offset`, `limit`, `resulttype`, `bbox`, `datetime_`, `properties`, `sortby`, `select_properties`, `skip_geometry`, `q`, `language`, `filterq` (a pygeofilter AST) and `crs_transform_spec`. A compatible signature is the one in `pygeoapi/provider/geojson.py:166`.
- **What `query()` must return.** The provider supplies `type`, `features`, `numberReturned`, and `numberMatched` when counting. The API adds links and `timeStamp`, and it never computes counts.
  - `features` must exist even for `resulttype='hits'`: the API reads `content['features']` (`pygeoapi/api/itemtypes.py:615`). *Executed:* a `KeyError` when missing.
- **`get(identifier, **kwargs)`** must raise `ProviderItemNotFoundError` for an unknown id. Returning `None` yields 400. With `uri_field` set, the item's `@id` and self links use that property (`pygeoapi/api/itemtypes.py:949`).
- **`get_fields()` values must be dicts with a `type`**, as in core (`pygeoapi/provider/geojson.py:95`). The docs' `{'name': 'string'}` shape (`docs/source/plugins.rst:177`) crashes `/queryables`, `/schema` and OpenAPI. *Executed.*
- **If `_fields` is left empty,** queryables are empty, property filters are silently dropped, and `sortby`/`properties=` return 400. *Executed.*
- **`@crs_transform`** (`pygeoapi/crs.py:301`) rewrites geometries when `crs_transform_spec` is set. `bbox` reaches the provider already in storage CRS.
- **Transactions** (`editable: true`):
  - `create` and `update` receive the raw request **bytes**.
  - `create` returns the new id.
  - `delete` is preceded by a `get`.

### 5.2 EDR

Source notes: [research/provider-contract.md §7](research/provider-contract.md), [research/providers-coverage-edr.md](research/providers-coverage-edr.md).

- **Registration.** `__init_subclass__` (`pygeoapi/provider/base_edr.py:58`) sets `query_types` from methods named in `EDR_QUERY_TYPES` that are defined in `cls.__dict__`, the class's own body (`pygeoapi/provider/base_edr.py:61`). If none are found, it raises `ProviderInvalidDataError` at class definition (`pygeoapi/provider/base_edr.py:66`). *Executed matrix:*
  - a grandchild class that defines nothing raises
  - a grandchild that defines only `area` stops advertising an inherited `position`
  - `position = Engine.position` in the class body works
  - `@classmethod` query methods aren't registered
- **`get_parameters()`** reads each field's `x-ogc-unit` with no default (`pygeoapi/provider/base_edr.py:139`). A missing unit makes `/collections/{id}` return 500 and drops the collection from `/collections`. *Executed.*
- **The API passes 17 keywords** to the query method: `query_type`, `instance`, `format_`, `datetime_`, `select_properties`, `wkt`, `z`, `bbox`, `within`, `within_units`, the four corridor parameters, `limit`, `location_id` and `crs_transform_spec` (`pygeoapi/api/environmental_data_retrieval.py:417`).
- **Unsupported types are rejected up front:** `query_type not in p.get_query_types()` returns 400 (`pygeoapi/api/environmental_data_retrieval.py:292`). Since `get_query_types()` returns `self.query_types`, an instance can narrow the advertised types.
- **`parameter-name` is validated with `p.get_fields()`** (`pygeoapi/api/environmental_data_retrieval.py:395`), so `get_fields()` must be implemented.
- **`instances()`/`instance()` return, rather than raise, `NotImplementedError()`** (`pygeoapi/provider/base_edr.py:81`). An un-overridden `instance()` therefore accepts every id. No choice of `instance()` behaviour gives clean errors on both instance endpoints in 0.24.0 (source note §7.3).
- **pygeoapi never validates CoverageJSON.** The harness must bring its own schema.
- **Models for our time-series template:** `sensorthings_edr.py` (locations → FeatureCollection; `CoverageCollection` of `PointSeries`) and `xarray_edr.py`.

### 5.3 Coverages, maps and tiles

Source notes: [research/provider-contract.md §8–9](research/provider-contract.md), [research/providers-tiles-maps-records-stac.md](research/providers-tiles-maps-records-stac.md).

- **Coverage**
  - Needs `_fields`, `axes`, and `_coverage_properties` (`width`, `height`, `resx`, `resy`), which collection metadata reads (`pygeoapi/api/collection.py:313`).
  - `format` is required (`pygeoapi/api/coverages.py:193`).
  - `query()` gets `bbox`, `datetime_`, `format_`, plus `bbox_crs`, `properties` and `subsets` when requested.
- **Map**
  - `query()` gets `format_`, `style`, `crs`, `'bbox-crs'` (a hyphenated key), `transparent`, `width`, `height`, `bbox` and `datetime_` (`pygeoapi/api/maps.py:245`), and returns image bytes.
- **Tile**
  - The API calls `get_tiles_service`, `get_tiling_schemes` and `get_tiles(layer=…, tileset=…, z, y, x, format_=…)` (`pygeoapi/api/tiles.py:240`), with z/y/x as strings.
  - `None` means 204.
  - `get_metadata(**kwargs)` is called with keywords.

### 5.4 Quirks of the Phase 1 core providers

Found while writing the CSV, GeoJSON, SQLiteGPKG and PostgreSQL descriptors,
running their probes, and (Phase 2) running the contract harness on them.

- **SQLiteGPKG writes to its file when it loads.**
  - It opens the file read-write with `sqlite3.connect(self.data)` (`pygeoapi/provider/sqlite.py:181`).
  - For a GeoPackage it then runs `SELECT AutoGPKGStart()` (`pygeoapi/provider/sqlite.py:220`), which refreshes SpatiaLite's `vgpkg_<table>` wrapper tables inside the file. pygeoapi's own `poi_portugal.gpkg` fixture already contains `vgpkg_poi_portugal`.
  - *Executed:* loading that fixture once changed 121 bytes of it, the SQLite change counter and schema cookie among them. Its list of tables was the same before and after.
  - On a read-only file, `AutoGPKGStart()` returns 0 and the provider raises `InvalidPluginError` with no message (`pygeoapi/provider/sqlite.py:227`). A missing file raises the same bare error (`pygeoapi/provider/sqlite.py:184`).
  - So the pygeoapi server needs write access to the file and its folder. The builder probes a temporary copy (`probe.copy_data` in the descriptor) and explains the read-only case (error rule `gpkg-readonly`). `tools/vendor.py` reports a fixture changed this way.
- **SQLiteGPKG reports only some column types.** `get_fields()` maps `INTEGER` and `REAL` to `number` (`pygeoapi/provider/sqlite.py:98`), and `TEXT…` and `BLOB` to `string` (`pygeoapi/provider/sqlite.py:100`). Columns declared any other way (`DATE`, `DATETIME`, `BOOLEAN`, `INT`, `DOUBLE`, …) are left out. They're missing from `/queryables` and from the builder's field pickers, which use the same list, and no column is ever an `integer`.
- **CSV doesn't keep the order of `properties`.** With `properties` set, each feature's properties come from `set(self.properties) | set(select_properties)` (`pygeoapi/provider/csv_.py:180`), so their order is arbitrary and can change when the server restarts. The CSV descriptor's help text says so.
- **GeoJSON ignores `q` and wants string ids.** Its `query()` accepts `q` (`pygeoapi/provider/geojson.py:168`) but never passes it on to `_load` (`pygeoapi/provider/geojson.py:189`), so a search returns everything. Its `get()` compares `str(feature.get('id')) == identifier` (`pygeoapi/provider/geojson.py:214`), which works because the API passes the URL segment, a string. Calling it with an int finds nothing. *Executed:* the Phase 2 contract harness reported both (it calls `get()` with a string, as the API does).
- **PostgreSQL reads two undocumented `data` keys.** `database` is an alias of `dbname`, and `query` is stored but never used (`pygeoapi/provider/sql.py:607`, `pygeoapi/provider/sql.py:609`). The descriptor leaves both out, with reasons (`drift_ignore`, ADR-0010).

## 6. Validation and tooling

### 6.1 `validate_config` and `pygeoapi config validate`
- `validate_config(instance_dict)` returns `True` or raises `jsonschema.ValidationError` (`pygeoapi/config.py:74`). It raises only jsonschema's single `best_match` error.
  - For resource-level mistakes that message is misleading: a missing `title` is reported as `'collection' is not one of ['process']`. *Executed.*
  - **Builder rule:** run `Draft202012Validator(...).iter_errors`, pick the `anyOf` branch by the resource's `type`, and map each error's `absolute_path` to a form field.
- It doesn't resolve env vars. A placeholder in an integer field fails as a string. The CLI (`pygeoapi config validate -c FILE`) loads with `yaml_load`, so it resolves them (`pygeoapi/config.py:105`).
- It isn't called at server startup. The server boots with schema-invalid configs and fails per request.

### 6.2 `pygeoapi openapi generate CONFIG_FILE`
- `CONFIG_FILE` is positional. `--fail-on-invalid-collection` defaults to on (`pygeoapi/openapi.py:1096-1097`).
- With the flag on, a provider that can't load fails the run (exit 1). *Executed:* a missing CSV file, an unknown short name, an unimportable dotted path.
- With `--no-fail-on-invalid-collection`, one broken collection silently drops the item paths of **every** feature/record collection. Each API module's `get_oas_30` runs in one `try`, and the error is only logged when `fail_on_invalid_collection` is false (`pygeoapi/openapi.py:553-566`). Never publish OpenAPI produced that way.
- It skips `visibility: hidden` collections (`pygeoapi/openapi.py:737`).
- It indexes `metadata.identification.terms_of_service` and `metadata.license.url` directly (`pygeoapi/openapi.py:272`, `pygeoapi/openapi.py:276`), although the schema calls them optional.
- For feature and record collections it calls `load_plugin` to read `fields` (`pygeoapi/api/itemtypes.py:1094`). A provider pointing at a missing GeoJSON file still generated. So "generate OK" is necessary for a healthy config, but not proof that it works.

## 7. Multiple providers per collection

- `get_provider_by_type` returns the **first** provider of a type, and raises `ProviderTypeError` when there's none (`pygeoapi/provider/__init__.py:51`, `pygeoapi/provider/__init__.py:66`).
- `get_provider_default` returns the first provider with `default`, else `providers[0]` (`pygeoapi/provider/__init__.py:81`, `pygeoapi/provider/__init__.py:86`).
- Each API uses the first provider of its type:
  - items: `feature`, else `record`
  - tiles: `tile`
  - maps: `map`
  - EDR: `edr`
- The default provider only decides how the collection *describes* itself.
- **J3 rules:**
  1. At most one provider per `type`.
  2. Distinct types may coexist.
  3. The data provider stays the default.
  4. Never make a `tile` or `map` provider the default.
  5. `tile` and `map` providers need `format`.
  6. Provider order is significant, so a lossless editor must preserve it.

## 8. Linked data, templates and Geoconnex

Source notes: [research/config-model.md §10](research/config-model.md), [research/org-plugins-and-geoconnex.md](research/org-plugins-and-geoconnex.md).

- **`templates` is read per dataset** via `get_dataset_templates`, falling back to `server.templates` (`pygeoapi/api/__init__.py:625`).
  - Only `path` is honoured. The search path is `[path, package templates, '.']` (`pygeoapi/util.py:476`), so a directory can override just `collections/items/item.jsonld`.
- **Core 0.24 never reads a singular `template:` key** (grep, *executed*). It's a legacy Internet of Water fork feature.
- **`uri_field`:**
  - A single item's `@id` is the `uri_field` value, with no URL check (`pygeoapi/linked_data.py:212`).
  - In `/items`, each `@id` is used only if `is_url` (`pygeoapi/linked_data.py:226-229`).
  - `uri_field: nosuchfield` makes the item's JSON-LD return 500. *Executed.*
- **Geoconnex check:**
  - Every sampled `uri_field` value must exist, be unique and be an absolute `https://geoconnex.us/…` URL.
  - Setting `uri_field` also rewrites item self links.
- **JSON-LD reaches HTML pages only client-side.** `_base.html` fetches `?f=jsonld` with XHR and injects a `<script type="application/ld+json">` tag (`pygeoapi/templates/_base.html:127`). Crawlers that don't run JavaScript won't see it.
- Neither Geoconnex repo references **ODM2 or QUDT**, so our EDR parameter links have no precedent to copy.

## 9. Server lifecycle, reload and the Docker image

Source note: [research/admin-api-and-runtime.md §3–4](research/admin-api-and-runtime.md).

### 9.1 Lifecycle and reload
- The config and OpenAPI document are read once, at import (`pygeoapi/flask_app.py:55-56`).
- The OpenAPI file must be generated beforehand. Nothing generates it at server start except the Docker entrypoint and Admin API writes.
- **Reload:** `gunicorn --reload --reload-extra-file $PYGEOAPI_CONFIG` restarts workers when the file changes (`docker/entrypoint.sh:151`). It does **not** regenerate OpenAPI, so a publisher that writes the file must regenerate OpenAPI first, then trigger the reload.
- *Executed (Phase 3, `tests/test_publish_integration.py`):* with `run-with-hot-reload`, the reloader also picks up a config replaced atomically (temp file, then `os.replace`), and the server serves the OpenAPI document written just before it.

### 9.2 The `geopython/pygeoapi:0.24.0` image
Read from the `Dockerfile` in Phase 0, then checked with `docker run` in Phase 1
(2026-10-07).
- Versions: Python 3.12.3, GDAL 3.8.4, SpatiaLite 5.1.0 and SQLite 3.45.1.
  psycopg2, GeoAlchemy2, rasterio, xarray and requests are installed; httpx
  isn't.
- Base and Python:
  - `FROM ubuntu:noble` (`Dockerfile:37`), with GDAL, rasterio, Fiona, psycopg2, xarray and more from apt and the ubuntugis-unstable PPA.
  - A venv at `/venv` with `--system-site-packages` (`Dockerfile:133`). Extra packages go in with `/venv/bin/python3 -m pip install`.
- Runtime:
  - It runs as **root**, on port 80, with no `HEALTHCHECK`.
  - The entrypoint runs `openapi generate … --fail-on-invalid-collection` at every start (`docker/entrypoint.sh:87`), so a provider that fails to load stops the container.
- **Sandbox consequences** (done for Phase 1's probes, ADR-0008):
  - Bypass the entrypoint for probes.
  - Run as a non-root UID. The image's `chmod -R g=u /pygeoapi` makes that workable.

## 10. Admin API

Source note: [research/admin-api-and-runtime.md §1–2](research/admin-api-and-runtime.md).

- **Routes**, enabled by `server.admin: true` (`pygeoapi/flask_app.py:61`):
  - `GET|PUT|PATCH /admin/config` (`pygeoapi/flask_app.py:580`)
  - `GET|POST /admin/config/resources` (`pygeoapi/flask_app.py:601`)
  - `GET|PUT|PATCH|DELETE /admin/config/resources/<resource_id>` (`pygeoapi/flask_app.py:619`)
  - Starlette and Django mis-wire POST (source note §5).
- **Validation and writes.** Writes run `validate_config` (`pygeoapi/api/admin.py:128`) and then `yaml_dump` (`pygeoapi/api/admin.py:141`). That's PyYAML `yaml.dump(..., sort_keys=False, indent=4)` (`pygeoapi/util.py:224`), which **drops comments, quoting, flow style and anchor names**. Placeholders are restored from `get_config(raw=True)` by a JSON-Patch step (`pygeoapi/api/admin.py:132`), which can clobber a user's edit.
- **Stale state.** Writes start from the config loaded at import, which is never refreshed. *Executed:*
  - a second write silently undoes the first
  - a new resource isn't served until workers restart
  - after one POST containing `${VAR}`, every later write in that worker returns 500
- **Secrets leak.** With `server.admin: true`, OpenAPI generation embeds the **entire resolved config**, secrets included, as a request example (`pygeoapi/openapi.py:819`). It's served publicly at `/openapi`. *Executed, for the file.*
- **No authentication** (`docs/source/admin-api.rst:29`).
- **Builder consequences:**
  - The Admin API target is safe only when the server restarts after each write.
  - Flag any target with `server.admin: true` whose config holds secrets.
  - Write files ourselves for the file target.

## 11. Prior art

Source notes: [research/prior-art.md](research/prior-art.md), [research/org-plugins-and-geoconnex.md](research/org-plugins-and-geoconnex.md).

- **byteroad/pygeoapi_config** (QGIS plugin):
  - It configures only WMSFacade, PostgreSQL and MVT-proxy.
  - It validates with hand-written dataclass rules, and checks pygeoapi's schema only in CI, against pygeoapi `master`.
  - Its round trip is lossy: it dropped `pubsub`, resource `limits` and `editable`, replaced `${DEFAULT_ITEMS}` with `10`, and removed comments (*executed*).
  - **Copy:** its before-save diff with confirmation. **Avoid:** its typed-model round trip, and errors that aren't linked to fields.
- **cgs-earth/pygeoapi-plugins 0.6.0:**
  - It doesn't pin pygeoapi and is developed against `internetofwater/pygeoapi` `dev` (`pygeoapi-plugins:pyproject.toml:58`).
  - Its short names (`CKAN`, `GeoPandas`, …) exist only in the fork (`pygeoapi-plugins:README.md:16`). Upstream 0.24 needs dotted paths such as `pygeoapi_plugins.provider.ckan.CKANProvider`.
  - Verdicts: `PseudoPostgreSQLProvider` and `MVTPostgresRedis` are broken; the others work or likely work. Details are in the source note.
  - `psycopg2` builds from source on macOS.
- **USGS pygeoapi-plugin-cookiecutter:** a stale 2022 Hypermodern-Python fork pinning `pygeoapi ^0.11` and Python 3.9, with an empty process module. Borrow its checklist (nox sessions, `src/` layout, changelog, answer replay), not its code.
- **OGC CITE:**
  - TEAM Engine's REST API runs a suite with `…/rest/suites/{ets}/run?iut=…` (Basic auth `ogctest:ogctest`).
  - The images are **amd64-only**.
  - `server.url` must be the URL the TEAM Engine container can reach.
  - EDR also needs `apiDefinition`.
  - pygeoapi runs no CITE job in CI (`docs/source/ogc-compliance.rst:10`).
- **Nothing to reuse for Mode B's engine.** No generic spec-driven HTTP or SQL provider for pygeoapi exists (GitHub search, 2026-09-23). `internetofwater/pgedr` (EDR over PostgreSQL) is worth reviewing for the time-series template.

## 12. Open items

To be checked when Docker is available, or in the phase noted.
1. **Resolved in Phase 1:** the exact Python and GDAL versions inside
   `geopython/pygeoapi:0.24.0` (§9.2).
2. Starlette and Django Admin API behaviour, and the Admin API's PUT/PATCH/DELETE diffs (Phase 3; the source note marks them pending).
3. Executed checks for tiles, maps, records and STAC. The code reading is complete, but the runs are pending (Phase 5).
4. Live checks of the pygeoapi-plugins providers against PostGIS, Redis and a CKAN endpoint (Phase 5).
5. Whether older releases used an EDR `register()` decorator. This matters only if we ever target a version before 0.24.
6. Whether today's `internetofwater/pygeoapi:latest` still honours `template:` (open question for the user).
