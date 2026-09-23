# Research: cgs-earth `pygeoapi-plugins` and `pygeoapi-geoconnex-examples`

Phase 0 research for the plugin catalog and the "Geoconnex options" feature of pygeoapi Provider Builder.
Status: **first draft**. Items marked *(pending)* still need an execution check.

## Sources and citation conventions

| Prefix in citations | Repo / clone | Commit |
|---|---|---|
| *(no prefix)*, e.g. `pygeoapi/linked_data.py:212` | core pygeoapi **0.24.0** at `vendor/pygeoapi` (byte-identical to the project `.venv` install) | `f765a64` (tag 0.24.0) |
| `pygeoapi-plugins:` | `https://github.com/cgs-earth/pygeoapi-plugins` at `vendor/pygeoapi-plugins` | `8041f004a98d2d557e07362ecb8062772bf1ac0b`, 2026-07-28T10:46:30-04:00, "Back to dev" |
| `pygeoapi-geoconnex-examples:` | `https://github.com/cgs-earth/pygeoapi-geoconnex-examples` at `vendor/pygeoapi-geoconnex-examples` | `76207c459d5cd89900df0050749e86bd5fbec56e`, 2024-07-24T16:51:39-04:00, "Fix config to point to new resource (#3)" |
| `pygeoapi-iow:` | `https://github.com/internetofwater/pygeoapi` branch `dev` at `vendor/pygeoapi-iow`. This is the fork the plugins are developed against. I cloned it only for comparison. | `6393ef943ea60565a4fb2b32c50f7cf2b15d9250`, 2026-06-11T15:31:11-06:00, version `0.24.dev0` (`pygeoapi-iow:pygeoapi/__init__.py:33`) |

"(executed)" means I ran it. "(read)" means I got it from reading the source. The scratch venv is `<scratchpad>/org-plugins/venv`, with `pygeoapi==0.24.0` and `pygeoapi-plugins` 0.6.0 from PyPI.

---

## Part 1: `cgs-earth/pygeoapi-plugins`

### 1.1 Packaging, PyPI and Git

- **PyPI latest is `0.6.0`**, uploaded 2026-07-28T14:44:08Z (wheel) (executed, `https://pypi.org/pypi/pygeoapi-plugins/json`). Release history: 0.1.0 (2024-03-25), 0.2.0 (2024-09-12), 0.3.0 (2025-07-22), 0.4.0 (2025-09-09), 0.5.dev0 (2025-10-29), 0.5.0 (2026-03-09), 0.6.0 (2026-07-28). Git has a tag `0.4.1` that was never published to PyPI (executed).
- PyPI `requires_python` is `>=3.12`. Only 0.6.0 files carry it; older files have `None` (executed). Source: `pygeoapi-plugins:pyproject.toml:6`.
- PyPI `requires_dist` is `geoalchemy, geopandas, numpy, psycopg2, pyarrow, pygeoapi, pygeofilter[backend-sqlalchemy], redis>=7.4.0, requests, shapely>=2.1.0, sparqlwrapper, sqlalchemy`, plus extras `dev` (pytest, pytest-cov, pre-commit) and `gdal` (GDAL) (executed). In source these are at `pygeoapi-plugins:pyproject.toml:20` through `:30` and `:36`.
- **`pygeoapi` is not pinned** (`pygeoapi-plugins:pyproject.toml:25`). For development, uv redirects it to the IoW fork: `pygeoapi = { git = "https://github.com/internetofwater/pygeoapi.git", branch="dev" }` (`pygeoapi-plugins:pyproject.toml:58`). The lockfile pins fork commit `a85c2f76…`, version `0.24.dev0` (`pygeoapi-plugins:uv.lock:875`–`877`). **Upstream pygeoapi 0.24.0 is therefore not what the plugin authors test against.** PyPI metadata does not carry `tool.uv.sources`, so `pip install pygeoapi-plugins` resolves upstream pygeoapi.
- **Git HEAD vs PyPI:** tag `0.6.0` is commit `44dec399` (2026-07-28T10:42:24-04:00). `git diff 0.6.0 HEAD` only touches the version strings in `pyproject.toml` and `uv.lock`, and HEAD says `0.7.dev0` (`pygeoapi-plugins:pyproject.toml:3`) (executed). The installed 0.6.0 wheel's `pygeoapi_plugins/` is identical to HEAD's (`diff -r`, executed). So **HEAD = 0.6.0 for all code**.
- **Install:** `uv pip install pygeoapi==0.24.0 pygeoapi-plugins` resolves to 55 packages, including `pygeoapi-plugins==0.6.0`, `sqlalchemy 2.0.54`, `geoalchemy2 0.20.0`, `redis 8.1.0`, `sparqlwrapper 2.0.0`, `rdflib 7.6.0` and `psycopg2 2.9.13` (executed). **Gotcha:** `psycopg2` is built from source. On macOS the install failed twice, first with "pg_config executable not found" and then with `ld: library 'ssl' not found`. It succeeded with `PATH=/opt/homebrew/opt/libpq/bin:$PATH LDFLAGS=-L/opt/homebrew/opt/openssl@3/lib CPPFLAGS=-I/opt/homebrew/opt/openssl@3/include` (executed). Our install flow must surface these system prerequisites.
- **Short names only exist in the fork.** The README says so: "The provider names listed in the table are only accessible in internetofwater/pygeoapi, otherwise the full python path is required" (`pygeoapi-plugins:README.md:16`). The fork registers `CKAN`, `FileSystemXML`, `GeoPandas`, `MVT-PostgreSQL`, `MVT-PostgresCache`, `MVT-PostgresCacheFS`, `PsuedoPostgreSQL` (sic) and `SPARQL` (`pygeoapi-iow:pygeoapi/plugin.py:78`–`85`), plus processes `Intersector` and `SitemapGenerator` (`pygeoapi-iow:pygeoapi/plugin.py:94`–`95`). Core 0.24's `PLUGINS['provider']` has none of these (`pygeoapi/plugin.py:40`–`74`). `load_plugin` raises `InvalidPluginError` for an unknown name without a dot (`pygeoapi/plugin.py:118`–`121`). **With 0.24 the catalog must use dotted paths.**
- **CI coverage:** CI runs `uv sync` against the fork (`pygeoapi-plugins:.github/workflows/main.yml:47`). It then runs the intersect, geopandas, jsonfg, postgresql (core provider), mvt_postgresql, sitemap and xml tests (`pygeoapi-plugins:.github/workflows/main.yml:56`–`63`). The SPARQL test is commented out (`pygeoapi-plugins:.github/workflows/main.yml:62`). **`tests/test_ckan_provider.py` and `tests/test_mvt_cache_provider.py` are not run in CI, and `PseudoPostgreSQLProvider` has no test at all.** `tests/test_postgresql_provider.py` imports the core `PostgreSQLProvider` (`pygeoapi-plugins:tests/test_postgresql_provider.py:47`).

### 1.2 Inventory

Every class imports cleanly with pygeoapi 0.24.0 (executed; MRO checked by import).

| Class | `name:` (dotted, works in 0.24) | Type | Direct base | Fork short name | 0.24 verdict |
|---|---|---|---|---|---|
| `CKANProvider` | `pygeoapi_plugins.provider.ckan.CKANProvider` | feature | core `BaseProvider` | `CKAN` | likely works *(pending live check)* |
| `GeoPandasProvider` | `pygeoapi_plugins.provider.geopandas_.GeoPandasProvider` | feature | core `BaseProvider` | `GeoPandas` | works with bugs *(pending)* |
| `SPARQLProvider` | `pygeoapi_plugins.provider.sparql.SPARQLProvider` | feature (wrapper) | core `BaseProvider` | `SPARQL` | likely works (`get`), with gotchas |
| `PseudoPostgreSQLProvider` | `pygeoapi_plugins.provider.postgresql.PseudoPostgreSQLProvider` | feature | core `PostgreSQLProvider` | `PsuedoPostgreSQL` | **broken** (`/items`) |
| `MVTPostgreSQLProvider_` (trailing `_`) | `pygeoapi_plugins.provider.mvt_postgresql.MVTPostgreSQLProvider_` | tile (MVT) | core `MVTPostgreSQLProvider` | `MVT-PostgreSQL` | likely works (needs PostGIS) |
| `MVTCacheProvider` | `pygeoapi_plugins.provider.mvt_cache.MVTCacheProvider` | tile | `MVTPostgreSQLProvider_` | none | abstract. Do not list in the catalog. |
| `MVTPostgresFilesystem` | `pygeoapi_plugins.provider.mvt_cache.MVTPostgresFilesystem` | tile | `MVTCacheProvider` | `MVT-PostgresCacheFS` | likely works |
| `MVTPostgresRedis` | `pygeoapi_plugins.provider.mvt_cache.MVTPostgresRedis` | tile | `MVTCacheProvider` | none | **broken on first cache write** |
| `MVTPostgresCache` | `pygeoapi_plugins.provider.mvt_cache.MVTPostgresCache` | tile | `MVTCacheProvider` | `MVT-PostgresCache` | likely works |
| `FileSystemXMLProvider` | `pygeoapi_plugins.provider.filesystem_xml.FileSystemXMLProvider` | stac | core `FileSystemProvider` | `FileSystemXML` | likely works |

The README names `pygeoapi_plugins.provider.mvt_postgresql.MVTPostgreSQLProvider_` with the trailing underscore (`pygeoapi-plugins:README.md:147`). The class is defined as `class MVTPostgreSQLProvider_(MVTPostgreSQLProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:74`).

Keys that core `BaseProvider.__init__` reads for **every** feature-style provider (`pygeoapi/provider/base.py:62`–`87`):
`name`, `type`, `data` (all required, otherwise `RuntimeError('name/type/data are required')`, `pygeoapi/provider/base.py:67`), `editable`, `count`, `options`, `id_field`, `uri_field`, `x_field`, `y_field`, `z_field`, `time_field`, `title_field`, `properties`, `file_types`, `include_extra_query_parameters` and `storage_crs`. The per-provider tables below repeat only the ones each provider actually uses.

---

### `CKANProvider` — `pygeoapi_plugins.provider.ckan.CKANProvider`

- **Provider type(s):** `feature` (README example `type: feature`, `pygeoapi-plugins:README.md:27`). Base: `class CKANProvider(BaseProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:46`).
- **Dependencies:** `requests` (`from requests import Session`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:32`) and core `crs_transform` (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:41`).
- **`data` forms:** a URL string for the CKAN `datastore_search` action, e.g. `https://catalog.newmexicowaterdata.org/api/3/action/datastore_search` (`pygeoapi-plugins:README.md:29`). It is fetched with `self.http.get(url, params=params)` (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:253`).

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name` | string | yes | — | dotted path above | `pygeoapi/provider/base.py:63` |
| `type` | string | yes | — | `feature` | `pygeoapi/provider/base.py:64` |
| `data` | string (URL) | yes | — | CKAN `datastore_search` endpoint | `pygeoapi/provider/base.py:65`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:207` |
| `resource_id` | string (CKAN resource UUID) | **yes** (KeyError) | — | sent as `resource_id` on every request | `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:61`, `:251` |
| `id_field` | string | **yes in practice** | none | popped from each record to become the feature `id` (KeyError if unset) | `pygeoapi/provider/base.py:72`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:282` |
| `x_field` | string | **yes unless every request skips geometry** | none | **top-level** key, not `geometry.x_field`. Cast with `float()`. | `pygeoapi/provider/base.py:74`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:290` |
| `y_field` | string | same as `x_field` | none | top-level key | `pygeoapi/provider/base.py:75`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:291` |
| `properties` | list[str] | no | `[]` | restricts CKAN `fields`. `id_field`, `x_field` and `y_field` are always added. | `pygeoapi/provider/base.py:79`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:184`–`190` |
| `storage_crs` | URI | no | CRS84 | used by `@crs_transform` | `pygeoapi/provider/base.py:86` |
| `uri_field` / `title_field` | string | no | none | read by core; used by the HTML and JSON-LD layers, not by the provider | `pygeoapi/provider/base.py:73`, `:78` |

- **Capabilities:** `query` handles `offset`/`limit`, `resulttype=hits` (`include_total`, `pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:196`–`197`), property filters (CKAN `filters` JSON, `:193`–`194`), `sortby` (`:199`–`200`), `q` (CKAN full-text, `:202`–`203`), `select_properties`, `skip_geometry` and CRS output via `@crs_transform` (`:87`). It **ignores `bbox` and `datetime_`**: they are accepted but `_load` never uses them. The README matrix agrees ("BBox ❌ Datetime ❌", `pygeoapi-plugins:README.md:11`). If CKAN returns fewer records than `limit`, the provider keeps paging (`:223`). There are no transactions.
- **`get_fields()`** returns CKAN's own field descriptors keyed by field id, `{field.pop('id'): field ...}` (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:83`). The `type` values are CKAN/Postgres types such as `text` and `numeric` (the tests assert `fields['DAMNAME']['type'] == 'text'`, `pygeoapi-plugins:tests/test_ckan_provider.py:55`). These are **not JSON-Schema types** (`string`/`number`) as 0.24 expects (`pygeoapi/provider/base.py:99`).
- **Docs/README:** `pygeoapi-plugins:README.md:18`–`42`. Docker example: `pygeoapi-plugins:docker/examples/ckan/ckan.config.yml:104`–`110`.
- **Tests:** `pygeoapi-plugins:tests/test_ckan_provider.py:35`–`47` (a live public CKAN, New Mexico reservoirs). Not run in CI.
- **Errors:** `ProviderConnectionError` on a non-2xx response or `success: false` (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:257`, `:267`). `ProviderQueryError` on bad JSON (`:263`). `get()` unpacks `[feature] = [...]` (`:146`), so an unknown id raises a plain `ValueError` rather than `ProviderItemNotFoundError`, which surfaces as a 500 instead of a 404.
- **Gotchas:** mutable default arguments. `_get_response(self, url, params={})` mutates the shared default dict (`pygeoapi-plugins:pygeoapi_plugins/provider/ckan.py:242`, `:251`), and `select_properties.extend(required)` mutates the default list (`:186`). In `get_fields()`, the `fields` parameter built from `properties` is never passed to the request (`:76`–`82`). The test fixture uses `name: pygeoapi_plugins.provider.ckan.CKAN`, which is not a class (`pygeoapi-plugins:tests/test_ckan_provider.py:40`). The tests only instantiate `CKANProvider` directly.
- **0.24 compatibility:** imports resolve (executed), `crs_transform` exists in 0.24 (`pygeoapi/crs.py:301`), and `query`/`get` accept `**kwargs`. Main issue: non-JSON-Schema field types, which may confuse queryables and schema pages. **Verdict: likely works** *(pending: live read-only check against the endpoint its tests use)*.

---

### `GeoPandasProvider` — `pygeoapi_plugins.provider.geopandas_.GeoPandasProvider`

- **Provider type(s):** `feature` (`pygeoapi-plugins:README.md:120`). Base: `class GeoPandasProvider(BaseProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:98`).
- **Dependencies:** `geopandas`, `pandas` and `shapely` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:31`–`35`), plus core `crs_transform` (`:47`). Pip names: `geopandas` (pulls `pandas` and `pyogrio`) and `shapely>=2.1.0`.
- **`data` forms:** anything `geopandas.read_file()` accepts, such as a local path or URL to CSV, GeoPackage or GeoJSON (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:235`). The README example uses a remote GPKG URL (`pygeoapi-plugins:README.md:123`) and says any GeoPandas-supported format works (`pygeoapi-plugins:README.md:114`). The whole dataset is loaded into memory at construction.

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name`, `type` | string | yes | — | | `pygeoapi/provider/base.py:63`–`64` |
| `data` | string (path/URL) | yes | — | passed to `geopandas.read_file` | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:235` |
| `id_field` | string | **yes** (KeyError) | — | column is cast to `str`. "`id_field` is the only field that is required to be labeled" (`pygeoapi-plugins:README.md:116`). | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:251` |
| `geometry` | object | no | — | if present, **both** sub-keys are required (KeyError otherwise) | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:191` |
| `geometry.x_field` | string | if `geometry` | — | point X column (CSV style, as in core CSV `provider_def['geometry']['x_field']`, `pygeoapi/provider/csv_.py:61`) | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:193` |
| `geometry.y_field` | string | if `geometry` | — | point Y column | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:194` |
| `time_field` | string | no | auto | if absent, uses a `LOADDATE` column, else the first column whose first value is a date or datetime, else none | `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:107`–`118` |
| `properties` | list[str] | no | `[]` | output property allow-list | `pygeoapi/provider/base.py:79`, `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:451` |
| `editable` | bool | no | `false` | enables core transactions (create/update/delete are implemented) | `pygeoapi/provider/base.py:69` |
| `storage_crs` | URI | no | CRS84 | | `pygeoapi/provider/base.py:86` |
| `uri_field` / `title_field` | string | no | none | core HTML/JSON-LD only | `pygeoapi/provider/base.py:73`, `:78` |

- **Capabilities:** property filters by string equality (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:372`–`380`), `hits`, bbox via `df['geometry'].intersects` (`:388`–`391`), multi-key `sortby` (`:397`–`416`), `datetime_` instants, prefixes and ranges (`:127`–`184`), `select_properties`, `skip_geometry` and CRS output (`@crs_transform`, `:318`). Transactions `create`/`update`/`delete` exist (`:514`–`577`) but only change the in-memory frame; nothing is written back to `data`. `q` raises `NotImplementedError` (`:351`–`354`).
- **`get_fields()`** maps pandas dtypes `float64→number`, `int64→integer`, `object→string` and everything else to `string` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:290`–`297`). The output shape is `{'name': {'type': ...}}` (`:299`–`301`), which matches 0.24.
- **Docs/README:** `pygeoapi-plugins:README.md:112`–`127`.
- **Tests/fixtures:** `pygeoapi-plugins:tests/test_geopandas_provider.py:42`–`71` uses `tests/data/obs.csv`, `station_list.csv` and `hu02.gpkg`. The HUC2 fixture has a `uri` column with Geoconnex PIDs such as `https://geoconnex.us/ref/hu02/07` (`pygeoapi-plugins:tests/test_geopandas_provider.py:197`–`202`). Runs in CI.
- **Errors:** `ProviderNoDataError` (file not found, `pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:237`; empty frame, `:281`), `ProviderInvalidDataError` (read failure, `:241`), `ProviderQueryError` (bad date range, bbox, sort order or missing geometry config: `:161`, `:393`, `:405`, `:446`) and `ProviderItemNotFoundError` (`:506`).
- **Gotchas (read):**
  - **Non-point geometries are emitted as a JSON string.** `feature['geometry']['coordinates'] = shapely.to_geojson(row[self.geometry_col])` puts a whole serialized GeoJSON geometry string in `coordinates` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:439`–`441`). *(pending: executed check)*
  - `get()` returns no `geometry` member and copies the raw row, including the shapely geometry, into `properties` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:508`–`512`).
  - `resulttype=hits` returns before the bbox and datetime filters are applied, so hit counts ignore bbox and datetime (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:382`–`386`).
  - Paging slices only after every row has been materialized with `iterrows` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:421`, `:474`–`476`), so it is O(n) per request.
  - Test-specific casts are hard-coded: `stn_id` becomes int64 and `value` becomes float64 whenever those columns exist (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:256`–`259`).
  - The geometry-column auto-detect checks `hasattr(col, 'geom_type')` on the column name, so it never matches (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:215`). Only a column literally named `geometry` is found (`:208`).
  - Top-level `x_field`/`y_field` are ignored. Only `geometry.x_field`/`geometry.y_field` are read (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:191`–`197`).
- **0.24 compatibility:** imports resolve (executed), `get_fields` shape matches, and `crs_transform` exists (`pygeoapi/crs.py:301`). **Verdict: works for point/CSV data. Non-point output is malformed** *(pending: executed load_plugin and query on the test fixtures)*.

---

### `SPARQLProvider` — `pygeoapi_plugins.provider.sparql.SPARQLProvider`

- **Provider type(s):** `feature`. It is a wrapper around another feature provider. Base: `class SPARQLProvider(BaseProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:72`). The wrapped provider is created with `load_plugin('provider', _provider_def)` after renaming `sparql_provider` to `name` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:89`–`91`).
- **Dependencies:** `SPARQLWrapper` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:30`; pip `sparqlwrapper`, which pulls `rdflib`), core `load_plugin` (`:34`) and `is_url` (`:40`).
- **`data` forms:** whatever the wrapped provider accepts. The whole provider definition is copied to the wrapped provider (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:89`).

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name`, `type`, `data` | string | yes | — | `data` goes to the wrapped provider | `pygeoapi/provider/base.py:63`–`65` |
| `sparql_provider` | string | **yes** (KeyError) | — | wrapped provider: a core short name (`CSV`, `GeoJSON`, `SQLiteGPKG`, …) or a dotted path | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:90` |
| `<wrapped provider keys>` | — | per wrapped provider | — | e.g. `id_field`, `geometry.x_field`/`y_field`, `table`. Passed through unchanged. | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:89` |
| `sparql_query` | object | yes in practice | `{}` | SPARQL settings | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:96` |
| `sparql_query.endpoint` | string (URL) | yes in practice | none | SPARQL endpoint, queried with POST and JSON results | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:98`–`100` |
| `sparql_query.bind` | object | **yes** (AttributeError) | — | how a feature is bound to a SPARQL subject | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:117` |
| `sparql_query.bind.name` | string | yes | — | feature property holding the subject. A URL is wrapped as `<…>`. Otherwise use `prefix:field`; the value is joined to the prefix. | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:118`, `:271`–`292` |
| `sparql_query.bind.variable` | string | **yes** | — | SPARQL variable, e.g. `'?subject'` (the leading `?` is stripped) | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:119` |
| `sparql_query.where` | list | **yes** (TypeError) | — | triple patterns. Each is a dict `{subject?, predicate, object}` (subject defaults to `?<variable>`) or a 3-token string `'S P O'`; anything else is skipped with a warning. | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:121`–`130`, `:244`–`248` |
| `sparql_query.prefixes` | map prefix→`<IRI>` | no | built-in owl, xsd, rdfs, rdf, foaf, dc, dbpedia, skos | **replaces** the built-in prefix block entirely | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:112`–`115`, `:45`–`57` |
| `sparql_query.select` | string | no | `*` | SELECT projection (may contain aggregates) | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:102`–`103` |
| `sparql_query.filter` | list[str] | no | `[]` | raw clauses (FILTER or BIND) joined into the WHERE block | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:132` |
| `sparql_query.groupby` | string | no | none | `GROUP BY …` | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:133`–`135` |
| `sparql_query.limit` | int > 0 | no | none | `LIMIT n`. Invalid or ≤ 0 values mean no limit. | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:104`–`110` |
| `sparql_query.convert` | bool | no | `True` | read but **unused** | `pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:97` |

- **Capabilities:** `query()` forwards every argument **positionally** to the wrapped provider and adds **no** SPARQL context. `/items` enrichment is a commented-out TODO (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:179`–`206`). Only `get()` (single item) runs SPARQL and merges the results into `properties` (`:218`–`230`). Multi-valued results become lists, or `{'datasets': [...]}` when several keys have equal-length lists (`:452`–`480`). Values starting with `{` or `[` are parsed as JSON. Values containing `|` or `, ` are split into lists (`:430`–`450`).
- **Docs/README:** `pygeoapi-plugins:README.md:51`–`110` (current key layout). The README describes `endpoint` as "The SPARQL variable representing the graph IRI in the query" (`pygeoapi-plugins:README.md:100`). That is wrong: it is the endpoint URL. The docker config `pygeoapi-plugins:docker/pygeoapi.config.yml:84`–`197` also uses the current layout. **`pygeoapi-plugins:docker/examples/sparql/sparql.config.yml:112`–`117` uses stale keys (`sparql_endpoint`, `sparql_subject`, `sparql_predicates`) that the code no longer reads** (grep in `sparql.py` finds none, executed).
- **Tests/fixtures:** `pygeoapi-plugins:tests/test_sparql_provider.py:35`–`59` (DBpedia with CSV), `:62`–`85` (**`https://graph.geoconnex.us`** with GeoPandas: HUC containment via `hyf:containingCatchment`) and `:88`–`147` (**Geoconnex graph** with GeoJSON: mainstem to monitoring locations and datasets, using `hyf:referencedPosition/hyf:HY_IndirectPosition/hyf:linearElement`, `schema:variableMeasured`, `schema:unitText` and `schema:measurementTechnique`, `:107`–`118`). These tests are disabled in CI (`pygeoapi-plugins:.github/workflows/main.yml:62`).
- **Errors:** `ProviderQueryError` on SPARQL failure (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:401`), `ProviderNoDataError` on merge `TypeError` (`:355`), and `KeyError`, `AttributeError` or `TypeError` for missing `sparql_provider`, `bind` or `where` (`:90`, `:118`, `:122`).
- **Gotchas:**
  - **Positional pass-through breaks with `GeoPandasProvider`.** SPARQL passes `(offset, limit, resulttype, bbox, datetime_, properties, sortby, select_properties, skip_geometry, q)` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:179`–`191`). GeoPandas' signature is `(offset, limit, resulttype, identifier, bbox, datetime_, properties, select_properties, sortby, skip_geometry, q)` (`pygeoapi-plugins:pygeoapi_plugins/provider/geopandas_.py:319`–`331`), so `bbox` lands in `identifier` and so on. Core CSV (`pygeoapi/provider/csv_.py:233`–`235`) and SQL (`pygeoapi/provider/sql.py:145`–`160`) match the order.
  - `get()` drops kwargs such as `crs_transform_spec` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:218`), so there is no CRS support.
  - In `_clean_subj`, if the bound value is not a URL and `bind.name` has no `prefix:`, `subj` is never assigned and the method raises `UnboundLocalError` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:271`–`294`).
  - `create()` calls `self.p.creat(...)`, a typo, so it raises `AttributeError` (`pygeoapi-plugins:pygeoapi_plugins/provider/sparql.py:411`–`412`).
  - Every `get` makes a live SPARQL call to a third-party endpoint.
- **0.24 compatibility:** imports resolve (executed) and `load_plugin` and `is_url` exist (`pygeoapi/plugin.py:97`, `pygeoapi/util.py:445`). **Verdict: likely works for `get` when wrapping CSV, GeoJSON or SQL providers. `query` is broken when wrapping GeoPandas** *(pending: offline init and query with CSV)*.

---

### `PseudoPostgreSQLProvider` — `pygeoapi_plugins.provider.postgresql.PseudoPostgreSQLProvider`

- **Provider type(s):** `feature`. Base: `class PseudoPostgreSQLProvider(PostgreSQLProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:71`), which uses core `pygeoapi.provider.sql.PostgreSQLProvider` (`:44`).
- **Dependencies:** `sqlalchemy` (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:41`–`42`). Everything else is inherited from core SQL: `geoalchemy2`, `psycopg2` and `pygeofilter` (`pygeoapi/provider/sql.py:50`–`53`).
- **`data` forms (core):** a connection string (`if isinstance(connection_data, str):`, `pygeoapi/provider/sql.py:597`) or a dict (`:603`–`617`).

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name`, `type` | string | yes | — | | `pygeoapi/provider/base.py:63`–`64` |
| `data` | string DSN or object | yes | — | | `pygeoapi/provider/sql.py:128` |
| `data.host` | string | dict form | none | | `pygeoapi/provider/sql.py:604` |
| `data.port` | int/str | no | `5432` | | `pygeoapi/provider/sql.py:605`, `:797` |
| `data.dbname` / `data.database` | string | dict form | none | either name works | `pygeoapi/provider/sql.py:606`–`607` |
| `data.user` | string | dict form | none | | `pygeoapi/provider/sql.py:603` |
| `data.password` | string | dict form | none | | `pygeoapi/provider/sql.py:610` |
| `data.search_path` | list[str] | no | `['public']` | schemas to search | `pygeoapi/provider/sql.py:614`–`616` |
| `data.query` | — | no | none | stored as `db_query` | `pygeoapi/provider/sql.py:609` |
| `table` | string | **yes** | — | | `pygeoapi/provider/sql.py:115` |
| `id_field` | string | **yes** | — | | `pygeoapi/provider/sql.py:116` |
| `geom_field` | string | no | `geom` | | `pygeoapi/provider/sql.py:117` |
| `options` | object | no | `{}` | DBAPI connect args (schema lists `connect_timeout`, `tcp_user_timeout`, `keepalives*`), `search_path`, and pool keys `pool_size` (5), `max_overflow` (10), `pool_recycle` (-1), `pool_timeout` (30), `pool_pre_ping` (true) | `pygeoapi/provider/sql.py:127`, `:635`–`641`; `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:725`–`751` |
| `time_field` | string | no | none | datetime filter column | `pygeoapi/provider/base.py:77`, `pygeoapi/provider/sql.py:538` |
| `count` | bool | no | `true` | **ignored.** The plugin always counts. | `pygeoapi/provider/base.py:70` |
| `storage_crs` | URI | no | CRS84 | | `pygeoapi/provider/base.py:86` |
| env `PSUEDO_COUNT_LIMIT` (sic) | int | no | `5000000` | row estimate above which the estimate is used. The README calls it `PSEUDO_COUNT_LIMIT` (`pygeoapi-plugins:README.md:48`). | `pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:47` |

- **Capabilities:** same query features as core PostgreSQL (README matrix `pygeoapi-plugins:README.md:12`), plus an estimated `numberMatched` from `EXPLAIN` through a plpgsql `count_estimate` function created on every query (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:48`–`66`, `:202`–`209`). It falls back to an exact count when CQL (`filterq`) or `hits` is used (`:151`–`154`). There is no transaction override.
- **Docs:** `pygeoapi-plugins:README.md:44`–`49`, with no YAML example. **Tests:** none.
- **Errors:** errors from the pseudo-count are caught and it falls back to `results.count()` (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:158`–`164`).
- **Gotchas (read):**
  1. `query()` calls `self._get_crs_transform(crs_transform_spec)` (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:179`). **Core 0.24's SQL provider has no such method.** It uses `get_transform_from_spec(crs_transform_spec)` (`pygeoapi/provider/sql.py:221`), and `_get_crs_transform` exists only in core OGR (`pygeoapi/provider/ogr.py:386`) (grep, executed). The fork does not define it in `sql.py` either (grep, executed). So every `resulttype=results` query raises `AttributeError`.
  2. `s.execute(COUNT_FUNCTION)` passes a raw string (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:207`), which SQLAlchemy 2.x rejects. The broad `except` then silently falls back to an exact count (`:162`–`164`), so the estimate probably never runs. *(pending: executed check)*
  3. An env var value is a `str`, so `matched < PSUEDO_COUNT_LIMIT` becomes `int < str` and raises `TypeError` whenever the variable is set (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:47`, `:211`).
  4. The compiled SQL, with literal binds, is embedded in single quotes (`pygeoapi-plugins:pygeoapi_plugins/provider/postgresql.py:208`). That breaks on quotes and is an injection risk.
  5. It needs `CREATE FUNCTION` rights.
- **0.24 compatibility: broken** for `/items` (AttributeError) *(pending: `hasattr` check, executed)*.

---

### `MVTPostgreSQLProvider_` — `pygeoapi_plugins.provider.mvt_postgresql.MVTPostgreSQLProvider_`

- **Provider type(s):** `tile` (README `type: tile`, `pygeoapi-plugins:README.md:146`). Base: core `MVTPostgreSQLProvider` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:51`, `:74`). That class in turn inherits `BaseMVTProvider` and `PostgreSQLProvider` (`pygeoapi/provider/mvt_postgresql.py:58`).
- **Dependencies:** `geoalchemy2.functions` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:34`–`45`), `sqlalchemy` (`:47`–`48`) and `pygeofilter` ECQL parser (`:49`), plus core `get_srid`, `url_join` and `human_size` (`:54`–`56`). Needs a PostGIS database.
- **`data` forms:** same as core PostgreSQL (DSN or dict; see the table above).

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| inherited SQL keys | — | — | — | `data`, `data.*`, `table`, `id_field`, `geom_field`, `options` (DB), `storage_crs` (see PseudoPostgreSQL table) | `pygeoapi/provider/sql.py:115`–`128` |
| `format.name` | string | **yes** | — | e.g. `pbf` | `pygeoapi/provider/tile.py:58` |
| `format.mimetype` | string | **yes** | — | e.g. `application/vnd.mapbox-vector-tile` | `pygeoapi/provider/tile.py:59` |
| `options` | object | **yes** (AttributeError if missing) | — | tile options | `pygeoapi/provider/tile.py:60`–`61` |
| `options.zoom.min` / `options.zoom.max` | int | **yes** (KeyError) | — | zoom range, also used for limits and TileJSON | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:133`–`134`, `pygeoapi/provider/tile.py:151` |
| `options.schemes` | list[str] | no | both schemes | restrict tile matrix sets. **See the gotcha below.** | `pygeoapi/provider/tile.py:61` |
| `layer` | string | no | `table` | MVT layer name | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:93` |
| `disable_at_z` | int | no | `6` | zoom below which `tile_threshold` or `min_pixel` filtering is applied | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:94`, `:317` |
| `simplify_geometry` | bool | no | **`True`** | simplification tolerance `min(1/10**(z//2), 0.1)` | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:95`, `:322`–`325` |
| `simplify_method` | enum | no | `ST_SimplifyPreserveTopology` | one of `ST_Simplify`, `ST_SimplifyPreserveTopology`, `ST_SimplifyVW` or `ST_SnapToGrid`, else `RuntimeError` | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:97`–`108`, `:61`–`67` |
| `tile_threshold` | string (ECQL, may contain `{z}`) | no | none | attribute filter at low zoom. `{z}` is replaced by `z or 1`. | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:111`, `:359`–`362` |
| `min_pixel` | int | no | `512` | drop features whose bbox area is below tile area / `min_pixel²` (used when there is no `tile_threshold`) | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:116`, `:366`–`369` |
| `tile_limit` | int | no | `0` (off) | max features per tile. Largest bbox first; random order for points. | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:119`–`129` |
| `tile_size` | number (MB) | no | `0` (off) | max tile size. Re-renders with a proportionally smaller limit. | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:131`, `:194`–`214` |

- **Capabilities:** WebMercatorQuad and WorldCRS84Quad (core `get_tiling_schemes`, `pygeoapi/provider/mvt_postgresql.py:104`–`113`). TileJSON 3.0.0 vendor metadata (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:219`–`279`), also attached to the HTML metadata (`:281`–`288`). Zoom-dependent filtering, simplification and size limits as above.
- **Docs/README:** `pygeoapi-plugins:README.md:133`–`229`. README errors: `mix_pixel` (`pygeoapi-plugins:README.md:150`) should be `min_pixel`. "The fifth configuration option `tile_limit`" (`pygeoapi-plugins:README.md:219`) actually describes `tile_size` (`:228`). The README presents simplification as opt-in, but the code default is `True`.
- **Tests/fixtures:** `pygeoapi-plugins:tests/test_mvt_postgresql_provider.py:41`–`63` needs PostGIS loaded with `tests/data/hotosm_bdi_waterways.sql.gz` (`pygeoapi-plugins:.github/workflows/main.yml:51`). Runs in CI against the fork.
- **Errors:** `RuntimeError` for a bad `simplify_method` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:108`) and `ProviderTileNotFoundError` out of limits (`:169`). At init it queries `ST_GeometryType` of the first row (`:120`–`124`), so an **empty table raises `TypeError` at startup**.
- **Gotchas:** the constructor runs a DB query. An `options.schemes` **list** is not filtered out of the DB options (only dict values are, `pygeoapi/provider/sql.py:623`–`627`). It is forwarded to `functools.cache`d `get_engine(**connect_args)` (`pygeoapi/provider/sql.py:644`–`653`), which should fail on the unhashable list. This is a core-0.24 issue and also affects core `MVT-postgresql`. *(pending: executed check)*
- **0.24 compatibility:** imports resolve (executed). The core API calls `p.get_tiles(layer=…, tileset=…, z=…, y=…, x=…, format_=…)` (`pygeoapi/api/tiles.py:244`–`245`), and the plugin signature absorbs `format_` via `**kwargs` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_postgresql.py:144`–`146`). `get_envelope(z, y, x, tileset)` is a core staticmethod (`pygeoapi/provider/mvt_postgresql.py:273`). `get_metadata` goes through core `BaseMVTProvider.get_metadata` (`pygeoapi/provider/base_mvt.py:186`). `_get_cql_filters` exists (`pygeoapi/provider/sql.py:495`). **Verdict: likely works** (not executed; needs PostGIS).

---

### `MVTPostgresFilesystem` — `pygeoapi_plugins.provider.mvt_cache.MVTPostgresFilesystem`

- **Provider type(s):** `tile`. Base: `MVTCacheProvider` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:187`), which extends `MVTPostgreSQLProvider_` (`:50`, `:56`). All `MVTPostgreSQLProvider_` keys apply.
- **Dependencies:** `redis` is imported at module level (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:34`), so it is needed even for the filesystem cache. Also `pathlib` and `sqlalchemy`.
- **`data` forms:** as for PostgreSQL.

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| (all `MVTPostgreSQLProvider_` keys) | | | | | see above |
| `disable_cache_at_z` | int | no | `6` | cache only tiles with `z <` this value | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:65`, `:91` |
| `cache_directory` | path | no | `/tmp/mvt_cache` | stored as `<dir>/<layer>/<tileset>/<z>/<y>/<x>.pbf` | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:198`–`200`, `:246`–`250` |
| `pre_cache` | bool | no | `false` | render every tile from `options.zoom.min` up to `min(zoom.max, disable_cache_at_z)` at startup | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:202`–`204`, `:116`–`142` |

- **Docs:** `pygeoapi-plugins:README.md:237`–`248`. **Tests:** `pygeoapi-plugins:tests/test_mvt_cache_provider.py:73`–`131` (not in CI). **Errors:** as for the parent. Write `TypeError`s are logged (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:240`–`242`).
- **Gotchas:** `pre_cache` blocks startup and can render many tiles. A cache write failure trips `assert self.save_tiles_to_cache(...)` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:112`). There is no cache invalidation.
- **0.24 compatibility: likely works** (same basis as the parent).

### `MVTPostgresRedis` — `pygeoapi_plugins.provider.mvt_cache.MVTPostgresRedis`

- **Provider type(s):** `tile`. Base: `MVTCacheProvider` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:253`). **Dependencies:** `redis` (`Redis(host=host, port=port)`, `:266`). **`data`:** as for PostgreSQL.

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| (all `MVTPostgreSQLProvider_` keys) + `disable_cache_at_z` | | | | | see above; `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:65` |
| `redis_host` | string | no | `localhost` | README uses `${REDIS_HOST}` (`pygeoapi-plugins:README.md:274`) | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:264` |
| `redis_port` | int | no | `6379` | README uses `${REDIS_PORT}` (`pygeoapi-plugins:README.md:275`) | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:265` |

- There are no password, DB-index or TLS options and no `pre_cache`. Keys look like `layer.tileset.z.y.x.pbf` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:300`–`302`).
- **Docs:** `pygeoapi-plugins:README.md:265`–`276`. **Tests:** `pygeoapi-plugins:tests/test_mvt_cache_provider.py:189`–`215` (not in CI).
- **Gotcha / bug:** `save_tiles_to_cache` returns `None` on success. There is no `return True` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:282`–`298`). The caller does `assert self.save_tiles_to_cache(...)` (`:112`), so the first cacheable tile request raises `AssertionError` (unless Python runs with `-O`). *(pending: executed mock check)*
- **0.24 compatibility: broken** (plugin bug, independent of 0.24).

### `MVTPostgresCache` — `pygeoapi_plugins.provider.mvt_cache.MVTPostgresCache`

- **Provider type(s):** `tile`. Base: `MVTCacheProvider` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:305`). **Dependencies:** `sqlalchemy` Table/MetaData (`:35`–`44`). **`data`:** as for PostgreSQL. The DB user needs CREATE and INSERT rights.

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| (all `MVTPostgreSQLProvider_` keys) + `disable_cache_at_z` | | | | | see above; `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:65` |
| `mvt_table` | string | no | `<table>_mvt` | cache table. Created in the first schema of `search_path`. | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:316`, `:384` |
| `force_create` | bool | no | `false` | drop and recreate the cache table at startup | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:321`, `:403`–`404` |
| `pre_cache` | bool | no | `false` | as for the filesystem cache | `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:324`–`326` |

- Cache table columns are `layer`, `tilematrixset`, `z`, `x`, `y` and `tile` (bytea), with a composite primary key and a lookup index (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:386`–`401`).
- **Docs:** `pygeoapi-plugins:README.md:250`–`263`. **Tests:** `pygeoapi-plugins:tests/test_mvt_cache_provider.py:161`–`186` (not in CI).
- **Gotcha:** `create_cache_table` is wrapped in `functools.cache` (`pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:376`). A DB error while caching is logged and returns `False` (`:369`–`371`), which then trips the `assert` at `:112`.
- **0.24 compatibility: likely works.**

*(`MVTCacheProvider` itself, `pygeoapi-plugins:pygeoapi_plugins/provider/mvt_cache.py:56`, is abstract: its `get_tiles_from_cache` and `save_tiles_to_cache` raise `NotImplementedError` at `:163` and `:184`. Exclude it from the catalog.)*

---

### `FileSystemXMLProvider` — `pygeoapi_plugins.provider.filesystem_xml.FileSystemXMLProvider`

- **Provider type(s):** `stac`, used under a `stac-collection` resource. Core STAC loads it with `get_provider_by_type(..., 'stac')` (`pygeoapi/api/stac.py:153`–`164`) and calls `get_data_path` (`pygeoapi/api/stac.py:183`). Base: `class FileSystemXMLProvider(FileSystemProvider):` (`pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:42`).
- **Dependencies:** standard library `xml.etree` (`pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:33`) and core util helpers (`:37`).
- **`data` forms:** a local directory path. It must exist (`pygeoapi/provider/filesystem.py:58`–`61`). Children are resolved by string concatenation `self.data + dirpath` (`pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:63`).

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name`, `type` (`stac`), `data` | string | yes | — | | `pygeoapi/provider/base.py:63`–`65` |
| `file_types` | list[str] | **yes in practice** | `[]` | extensions treated as items, e.g. `.xml` | `pygeoapi/provider/base.py:80`, `pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:102` |

- **Capabilities:** browses a directory of sitemap XML files, listing `_sitemap.xml` first (`pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:129`–`132`). An XML file becomes a Feature whose `properties.links` has one `rel: child` link per text line, typed `application/ld+json` (`:216`–`236`). Intended to pair with the sitemap process. There is no README section and no tests.
- **Errors:** `ProviderNotFoundError` (`pygeoapi-plugins:pygeoapi_plugins/provider/filesystem_xml.py:118`).
- **0.24 compatibility:** imports resolve (executed). `file_modified_iso8601`, `get_path_basename` and `url_join` exist in 0.24 (`pygeoapi/util.py:311`, `:396`, `:632`). **Verdict: likely works** *(pending: executed temp-dir check)*.

---

### 1.3 Processes and formatters (out of scope, list only)

- Formatters exported from `pygeoapi-plugins:pygeoapi_plugins/formatter/__init__.py:33`–`44`: `pygeoapi_plugins.formatter.JSONFG` (`JSONFGFormatter`), `.Parquet`, `.Shapefile`, `.GPKG`, `.KML`, `.PGDUMP` and `.XML`. All import against 0.24 (executed). Each is referenced from a resource-level `formatters:` list (`pygeoapi-plugins:README.md:343`–`350`), which the 0.24 schema declares (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:644`–`662`).
- Processes: `pygeoapi_plugins.process.intersect.IntersectionProcessor` (`pygeoapi-plugins:pygeoapi_plugins/process/intersect.py:128`). It needs GDAL (`from osgeo import gdal, ogr`, `:32`), and the import failed without the `gdal` extra (executed). `pygeoapi_plugins.process.sitemap.SitemapProcessor` (`pygeoapi-plugins:pygeoapi_plugins/process/sitemap.py:139`) raises `RuntimeError: PYGEOAPI_CONFIG environment variable not set` at import (executed).

---

## Part 2: `cgs-earth/pygeoapi-geoconnex-examples`

### 2.1 What's in the repo

One config (`pygeoapi.config.yml`), two Jinja JSON-LD templates (`jsonld/hydrologic-location.jsonld` and `jsonld/ref-gages.jsonld`), sample data (`data/data.csv`, `.geojson`, `.gpkg`) and a `docker-compose.yml` that runs **`internetofwater/pygeoapi:latest`** (the fork image, `pygeoapi-geoconnex-examples:docker-compose.yml:4`). Compose sets env `PYGEOAPI_URL=http://localhost:5000` (`pygeoapi-geoconnex-examples:docker-compose.yml:10`) and mounts `./jsonld` into the package's default template dir, `/pygeoapi/pygeoapi/templates/jsonld` (`pygeoapi-geoconnex-examples:docker-compose.yml:14`). The config header says "LAST UPDATE: 2021-06-04" (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:2`). The README says the fork "includes modifications that enable the injection of custom templated JSON-LD into the script headers of the HTML pages" (`pygeoapi-geoconnex-examples:README.md:7`).

### 2.2 Example resources

All five are `type: collection` with a `feature` provider, **`template: jsonld/hydrologic-location.jsonld`**, and **no `uri_field`, no `title_field` and no `linked-data`** (executed parse). The only env var is `server.url: ${PYGEOAPI_URL}` (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:36`).

| Resource | Data source | `name:` | `id_field` | Other provider keys | `template:` | Loads in core 0.24? |
|---|---|---|---|---|---|---|
| `demo-csv` | `/data/data.csv` (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:107`) | `CSV` (`:106`) | `EXISTING_INFO_ID` (`:108`) | `geometry.x_field: LONGITUDE`, `geometry.y_field: LATITUDE` (`:109`–`111`) | `:90` | yes (core `CSV`, `pygeoapi/plugin.py:42`) |
| `demo-geojson` | `/data/data.geojson` (`:135`) | `GeoJSON` (`:134`) | `EXISTING_INFO_ID` (`:136`) | — | `:118` | yes (`pygeoapi/plugin.py:49`) |
| `demo-gpkg` | `/data/data.gpkg` (`:160`) | `SQLiteGPKG` (`:159`) | `EXISTING_INFO_ID` (`:161`) | `table: data` (`:162`) | `:143` | yes (`pygeoapi/plugin.py:66`) |
| `demo-esri` | ArcGIS FeatureServer `…/GSP_Monitoring_Data/FeatureServer/0` (`:186`) | `ESRI` (`:185`) | `EXISTING_INFO_ID` (`:187`) | — | `:169` | yes (`pygeoapi/plugin.py:47`) |
| `demo-ckan` | `https://data.ca.gov/api/3/action/datastore_search` (`:211`) | **`CKAN`** (`:210`) | `SITE_CODE` (`:213`) | `resource_id: ab3f524c-…` (`:212`), top-level `x_field: LONGITUDE`, `y_field: LATITUDE` (`:214`–`215`) | `:194` | **no.** `CKAN` is a fork-only short name, so `InvalidPluginError` (`pygeoapi/plugin.py:118`–`121`). Use `pygeoapi_plugins.provider.ckan.CKANProvider`. |

Conflict: the README's `demo-ckan` snippet uses `resource_id: 72612518-…` and `id_field: EXISTING_INFO_ID`, and shows `uri_field: EXAMPLE_URI_FIELD_RENAME_AS_NECESSARY` (`pygeoapi-geoconnex-examples:README.md:135`–`137`). The config uses `ab3f524c-…` and `SITE_CODE`, and has no `uri_field` (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:212`–`213`).

### 2.3 Validation with 0.24 `validate_config` (executed)

The file was loaded with `pygeoapi.util.yaml_load` with `PYGEOAPI_URL=http://localhost:5000` set. `validate_config` runs `jsonschema_validate` (`pygeoapi/config.py:74`–`83`).

| Config | Result | Reason |
|---|---|---|
| `pygeoapi-geoconnex-examples:pygeoapi.config.yml` | **FAIL** (1 error) | `metadata.contact`: `'name' is a required property` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:362`–`363`). The config's contact block has no `name` (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:73`–`81`). After adding `contact.name` it **PASSES**. |
| `pygeoapi-plugins:docker/pygeoapi.config.yml` | PASS | — |
| `pygeoapi-plugins:docker/examples/ckan/ckan.config.yml` | FAIL | same missing `contact.name` |
| `pygeoapi-plugins:docker/examples/sparql/sparql.config.yml` | FAIL | same missing `contact.name` (stale SPARQL keys are not detected) |
| `pygeoapi-plugins:tests/pygeoapi-test-config.yml` | FAIL | `processor` is a string (`pygeoapi-plugins:tests/pygeoapi-test-config.yml:140`); the schema requires an object with `name` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:678`–`688`) |

So **`template:` passes validation, but only because resource objects are open.** The schema has no `additionalProperties: false` there. An invented resource key, a resource-level `templates` and a provider `uri_field` were all accepted too (executed). The deprecated `server.limit` (`pygeoapi-geoconnex-examples:pygeoapi.config.yml:43`; "DEPRECATED: use limits instead", `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:67`–`70`) is also accepted. **`validate_config` checks neither provider names nor provider-specific keys**, which is why the `CKAN` short name passes.

### 2.4 `template:` vs core 0.24 `templates`

- **Core 0.24 never reads a resource key `template`.** A grep of the core Python for `'template'` or `"template"` finds nothing (executed).
- Core 0.24 reads resource-level **`templates`** (plural, a dict with `path`) in `get_dataset_templates()`: `templates = self.config['resources'][dataset].get('templates')` and `return templates or self.tpl_config['server']['templates']` (`pygeoapi/api/__init__.py:624`–`627`). It also reads server-level `server.templates.path` and `static` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:101`–`110`; default set at `pygeoapi/api/__init__.py:541`–`542`). Resource-level `templates` is **not declared in the 0.24 schema**, though it is tolerated (see 2.3).
- JSON-LD rendering uses **fixed file names**: `render_j2_template(cls.tpl_config, templates, 'collections/items/item.jsonld', ldjsonData)` for one item and `'collections/items/index.jsonld'` for `/items` (`pygeoapi/linked_data.py:255`–`264`). The search path is `[<templates.path>, TEMPLATESDIR, '.']` (`pygeoapi/util.py:476`–`488`), so a custom directory overrides only the files it contains.
- The fork's current `dev` also reads only `templates` (`pygeoapi-iow:pygeoapi/api/__init__.py:693`). Its `linked_data.py` is identical to core 0.24's (`diff`, executed). **So `template:` is a legacy fork feature from roughly 2021.** In 0.24 (and fork dev) it is silently ignored, and items get the default `item.jsonld`, which is just `{{ to_json(data, …) }}` (`pygeoapi/templates/collections/items/item.jsonld:1`). *(Open: whether today's `internetofwater/pygeoapi:latest` image still honours `template:`.)*
- **0.24 equivalent:** save the Geoconnex template as `<dir>/collections/items/item.jsonld` and set `resources.<id>.templates: {path: <dir>}`. *(pending: executed render with the real 0.24 `geojson2jsonld`)*

### 2.5 What the templates produce and how `@id` works in core

**`@id` in core 0.24 (read):**
- Single item: `uri = content['properties'].get(p.uri_field) if p.uri_field else f'{collections_url}/{dataset}/items/{identifier}'` (`pygeoapi/api/itemtypes.py:949`–`950`). That value is passed to `geojson2jsonld` (`pygeoapi/api/itemtypes.py:1027`–`1028`), which sets `data['@id'] = identifier` (`pygeoapi/linked_data.py:212`). **The item's self and alternate links also use `uri`** (`pygeoapi/api/itemtypes.py:969`–`979`), so they point at the PID resolver. A missing `uri_field` value gives `@id: null` and links like `None?f=json`.
- `/items`: for each feature the `@id` is `feature[id_field]` or `properties[id_field]`, where `id_field = p.uri_field or 'id'` (`pygeoapi/api/itemtypes.py:722`–`724`, `pygeoapi/linked_data.py:226`–`227`). The value is used **only if `is_url(...)`** (scheme and netloc present, `pygeoapi/util.py:455`–`457`). Otherwise it falls back to `{ds_url}/items/{id}` (`pygeoapi/linked_data.py:228`–`229`). Each feature gets `type: schema:Place` (`pygeoapi/linked_data.py:237`).
- Geometry is encoded three ways: GeoJSON, `gsp:hasGeometry` with a `gsp:asWKT` literal, and `schema:geo` (GeoCoordinates for points, GeoShape line or polygon otherwise) (`pygeoapi/linked_data.py:269`–`360`).
- The default `@context` is `schema`, `gsp` and `type→@type`, plus `linked-data.context` (`pygeoapi/linked_data.py:194`–`202`, `:247`–`250`; schema `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:400`–`413`). **The plugins' docker configs use a legacy resource-level `context:`** (`pygeoapi-plugins:docker/pygeoapi.config.yml:67`–`70`), which core 0.24 does not read. It reads only `linked-data.context`.
- HTML: `uri_field` values become links in the items table (`pygeoapi/templates/collections/items/index.html:140`–`146`). `title_field` replaces the id column (`pygeoapi/api/itemtypes.py:679`–`683`). **JSON-LD reaches the HTML only on the client side**: `_base.html` fetches `?f=jsonld` with XHR and appends a `<script type="application/ld+json">` (`pygeoapi/templates/_base.html:127`–`147`). Crawlers that do not run JavaScript will not see it.

**`hydrologic-location.jsonld` (read):**
- Context: `schema`, `skos`, `hyf`, `name`, `gsp`, `sameAs`, `related`, `description`, `image` (`pygeoapi-geoconnex-examples:jsonld/hydrologic-location.jsonld:2`–`17`).
- `"@id": "{{ data["@id"] }}"` (`:18`). `@type` is HY_Features `HY_HydrometricFeature` and `HY_HydroLocation` (`:19`–`22`).
- `name` comes from `PROGRAM_SITE_ID` (`:23`), `description` from `SITE_TYPE at LOCATION_DESCRIPTION` (`:24`) and `schema:provider` from `PROGRAM_NAME` (`:25`). `hyf:HY_HydroLocationType: hydrometricStation` (`:26`).
- `sosa:isFeatureOfInterestOf` holds `schema:url` = `URL_SITE` (`:27`–`33`).
- **`schema:geoWithin` is hard-coded to `https://geoconnex.us/ref/states/06`** (California) (`:34`).
- `geo` and `gsp:hasGeometry` come from the core-generated `data['schema:geo']` and `data['gsp:hasGeometry']` (`:35`–`36`).
- Issues (read): the `sosa:` prefix is never declared, and `geo` is not a term in this context (it is in ref-gages, `pygeoapi-geoconnex-examples:jsonld/ref-gages.jsonld:12`). String fields are interpolated raw into quotes, so a `"` in the data produces invalid JSON. *(pending: executed rdflib parse)*

**`ref-gages.jsonld` (read):** the config does not reference it (grep, executed). Same header, plus `name`/`description` from `data.name` and `data.description` (`pygeoapi-geoconnex-examples:jsonld/ref-gages.jsonld:24`–`25`). If `nhdpv2_REACHCODE` or `mainstem_uri` is present, it emits `hyf:referencedPosition`: an `HY_IndirectPosition` with `hyf:linearElement` `https://geoconnex.us/nhdplusv2/reachcode/{{ data.nhdpv2_REACHCODE }}` and a distance measure (`:26`–`46`), and/or a mainstem `hyf:linearElement` (`:47`–`53`).

**"Geoconnex-ready", from the repos:**
1. A valid JSON-LD object **and** "a PID redirect in geoconnex namespace" (`pygeoapi-geoconnex-examples:README.md:29`).
2. The PID stored in a data field and set as the provider's `uri_field` (`pygeoapi-geoconnex-examples:README.md:108`). Values look like `https://geoconnex.us/ref/hu02/07` (`pygeoapi-plugins:tests/test_geopandas_provider.py:197`).
3. HY_Features and schema.org typing, WKT and schema geo, reference-feature links such as `schema:geoWithin` to `https://geoconnex.us/ref/...`, and `hyf:referencedPosition` to mainstems or reachcodes (templates above).
4. Dataset links (`sosa:isFeatureOfInterestOf`, or in the Geoconnex graph `schema:subjectOf` → dataset with `schema:variableMeasured`, `schema:unitText` and `schema:measurementTechnique`, `pygeoapi-plugins:tests/test_sparql_provider.py:108`–`118`).

### 2.6 ODM2 / QUDT

Case-insensitive grep for `odm2` and `qudt` over both repos (excluding `.git` and `uv.lock`) finds **nothing** (executed). Neither repo references ODM2 or QUDT vocabularies, so our "ODM2/QUDT links for EDR parameters" has no precedent here. *(pending: core 0.24 EDR parameter structure to anchor the design)*

### 2.7 Requirements for our "Geoconnex options" feature

*(draft, refined below)*
1. **uri_field assistant:** pick from the provider's `get_fields()`. Sample features and check that every value exists, is unique and passes core `is_url` (`pygeoapi/util.py:455`–`457`). In Geoconnex mode also require the `https://geoconnex.us/` prefix. Warn that `uri_field` also rewrites item links (`pygeoapi/api/itemtypes.py:949`–`979`).
2. **Never emit `template:` for 0.24.** Generate `<dir>/collections/items/item.jsonld` (and optionally `index.jsonld`) and set `resources.<id>.templates.path`. On import, detect `template:` and offer a migration.
3. **`linked-data.context` editor** (0.24 schema). Flag legacy resource-level `context:` and `geojsonld:`.
4. **Template library** with fixed versions of the two example templates (declare `sosa`, add the `geo` term, JSON-escape values, make `geoWithin` configurable).
5. **Preview:** render through the real 0.24 `geojson2jsonld` and parse the result as RDF to show triples and flag dropped terms.
6. **Catalog:** use dotted paths only. Map the fork short names on import. Show verdicts (Pseudo broken, Redis broken, GeoPandas geometry bug, SPARQL/GeoPandas incompatible).
7. **Gate:** `validate_config` plus a `load_plugin` dry run, because the schema does not validate provider names or keys.
8. **EDR ODM2/QUDT:** *(to be anchored in core 0.24 EDR parameter metadata)*.
