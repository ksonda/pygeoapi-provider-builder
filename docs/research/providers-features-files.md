# File-based feature providers — pygeoapi 0.24.0

Phase 0 research for the provider-descriptor catalog. Scope: `CSV`, `GeoJSON`,
`SQLiteGPKG`, `OGR`, `Parquet`, `TinyDB` (feature). `TinyDBCatalogue` (records) is
covered elsewhere; shared code is noted in the TinyDB section.

- **Ground truth:** pygeoapi 0.24.0 (tag commit f765a64), vendored at `vendor/pygeoapi`.
  Every citation is `path:line` relative to that repo root.
- **(executed)** means the result was observed by running the provider via
  `load_plugin('provider', def)` against `tests/data` fixtures. **(read)** means it was
  derived from reading the source only.
- Probes ran from the scratch dir (CWD outside the repo) with the project venv
  (`.venv`, Python 3.12.14: CSV, GeoJSON, TinyDB, SQLiteGPKG). Parquet and OGR ran in a
  scratch venv with `pygeoapi==0.24.0` (byte-identical to the vendored source), pyarrow
  25.0.1, geopandas 1.1.4, s3fs 2026.9.0 and GDAL Python bindings 3.7.0 built against
  Homebrew libgdal 3.7.1. SQLiteGPKG ran with Homebrew `mod_spatialite.dylib` (libspatialite 5.1.0).
- **Draft status:** first complete draft; sections are refined provider by provider.

---

## 0. Common keys every feature provider inherits (`BaseProvider.__init__`)

`BaseProvider.__init__` reads these keys (`pygeoapi/provider/base.py:51`). Provider
sections below list only provider-specific keys, plus common keys that a provider
interprets in its own way.

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name` | string | yes | — | Registry short name or dotted class path. A missing `name`/`type`/`data` raises `RuntimeError('name/type/data are required')`. An unknown short name raises `InvalidPluginError`. | `pygeoapi/provider/base.py:63`, `pygeoapi/provider/base.py:67`, `pygeoapi/plugin.py:118` |
| `type` | string | yes | — | `feature` for all six (the API also looks for `record`). | `pygeoapi/provider/base.py:64`, `pygeoapi/api/itemtypes.py:336` |
| `data` | string \| object | yes | — | Provider-specific. The schema allows a string or an object. | `pygeoapi/provider/base.py:65`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:540` |
| `editable` | bool | no | `false` | Read as-is, with no `str2bool`. It is only checked at API level before create/update/delete. | `pygeoapi/provider/base.py:69`, `pygeoapi/api/itemtypes.py:788` |
| `count` | bool | no | `true` | Passed through `str2bool`. A bool works, and so do the strings yes/true/t/1/on. Any other string counts as false. An **int or null crashes** with `AttributeError` at load (executed: `count: 0` and `count: null`). | `pygeoapi/provider/base.py:70`, `pygeoapi/util.py:246` |
| `options` | object | no | `None` | Stored. **None of the six file providers read it** (grep of each module). The schema constrains `options` to the PostgreSQL options shape through `oneOf`. | `pygeoapi/provider/base.py:71`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595` |
| `id_field` | string | provider-specific | `None` | Feature identifier column. How each provider uses it differs (see below). The API marks it `x-ogc-role: id` in queryables. | `pygeoapi/provider/base.py:72`, `pygeoapi/api/itemtypes.py:209` |
| `uri_field` | string | no | `None` | Used only by the API: JSON-LD `@id` and HTML. None of the six providers read it. | `pygeoapi/provider/base.py:73`, `pygeoapi/api/itemtypes.py:677` |
| `x_field` / `y_field` / `z_field` | string | no | `None` | Top-level keys. Only Parquet reads `x_field`/`y_field`, and there each may be a list of two names. CSV ignores them and uses `geometry.*` instead. | `pygeoapi/provider/base.py:74`, `pygeoapi/provider/base.py:76` |
| `time_field` | string | no | `None` | Honoured for `datetime` by Parquet and TinyDB only. The API marks it `primary-instant` in queryables. | `pygeoapi/provider/base.py:77`, `pygeoapi/api/itemtypes.py:211` |
| `title_field` | string | no | `None` | API/HTML only. | `pygeoapi/provider/base.py:78`, `pygeoapi/api/itemtypes.py:679` |
| `properties` | list[str] | no | `[]` | Allow-list of properties. CSV, GeoJSON and OGR honour it in output. SQLiteGPKG, Parquet and TinyDB ignore it in output. In all cases the API also uses it to filter queryables and `/schema`. The schema requires `minItems: 1` and `uniqueItems`. | `pygeoapi/provider/base.py:79`, `pygeoapi/api/itemtypes.py:191`, `pygeoapi/api/__init__.py:1079`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:601` |
| `file_types` | list | no | `[]` | Used by FileSystem/Azure only. Irrelevant here. | `pygeoapi/provider/base.py:80` |
| `include_extra_query_parameters` | bool | no | `false` | When true, the API forwards query params that are not in `get_fields()` to `query(properties=…)`. | `pygeoapi/provider/base.py:81`, `pygeoapi/api/itemtypes.py:431` |
| `storage_crs` | URI string | no | CRS84 (`http://www.opengis.net/def/crs/OGC/1.3/CRS84`) | Resolved with `get_crs()` **at load**, so a malformed URI fails the load with `CRSError`. The API transforms `bbox` into it before calling `query()`. | `pygeoapi/provider/base.py:86`, `pygeoapi/provider/base.py:87`, `pygeoapi/api/itemtypes.py:418`, `pygeoapi/crs.py:61` |

Keys read **outside** `BaseProvider` that still apply to every feature provider:

| Key | Type | Default | Meaning / notes | Source |
|---|---|---|---|---|
| `crs` | list[URI] | `[CRS84]` | Supported `crs`/`bbox-crs` values. CRS84 is appended if no default is listed (the config list is mutated in place). A `crs` query value outside the list returns 400. | `pygeoapi/crs.py:111`, `pygeoapi/crs.py:120`, `pygeoapi/crs.py:571` |
| `always_xy` | bool | `false` | Passed to the pyproj transformer used by `@crs_transform`. OGR does not use it (OGR builds its own OSR transform). | `pygeoapi/crs.py:556`, `pygeoapi/crs.py:593` |
| `storage_crs_coordinate_epoch` | number | — | Only echoed as `storageCrsCoordinateEpoch` in collection metadata. | `pygeoapi/api/collection.py:276` |
| `default` | bool | — | Picks the default provider among a resource's providers. Otherwise the first provider is the default. | `pygeoapi/provider/__init__.py:81` |
| `geom_field` | string | — | The API uses it to rename `geometry` in parsed CQL2 filters. None of the six providers accept `filterq`, but OGR does read `geom_field` for another purpose. | `pygeoapi/api/itemtypes.py:499` |

API behaviours that shape every descriptor (read):
- `query()` is always called with `offset, limit, resulttype, bbox, datetime_, properties, sortby, skip_geometry, select_properties, crs_transform_spec, q, language, filterq` (`pygeoapi/api/itemtypes.py:557`). Providers that do not name an argument swallow it via `**kwargs`. The API never refuses a `filter=` request for these providers: the filter is parsed and then silently ignored.
- Property query params are forwarded only if the name is in `p.fields` (or `include_extra_query_parameters`) (`pygeoapi/api/itemtypes.py:431`). A column missing from `get_fields()` is therefore not filterable over HTTP.
- `sortby` names are validated against `p.fields` (400 `bad sortby property`) before the provider sees them (`pygeoapi/api/itemtypes.py:453`).
- The `properties=` query parameter is validated against `p.properties ∪ p.fields` (`pygeoapi/api/itemtypes.py:468`).
- Transactions: the API passes the **raw request body (bytes)** to `create()`/`update()` (`pygeoapi/api/itemtypes.py:803`, `pygeoapi/api/itemtypes.py:822`, `pygeoapi/api/__init__.py:335`).

`Provider*Error` classes (for plain-language messages), all in `pygeoapi/provider/base.py`:
`ProviderGenericError` (:322, "generic error (check logs)"), `ProviderConnectionError`
(:327), `ProviderTypeError` (:332, 400), `ProviderInvalidQueryError` (:338, 400
InvalidQuery), `ProviderQueryError` (:345), `ProviderItemNotFoundError` (:350, 404),
`ProviderNoDataError` (:357, 204), `ProviderNotFoundError` (:364), `ProviderVersionError`
(:369), `ProviderInvalidDataError` (:374), `ProviderRequestEntityTooLargeError` (:379, 413).
`InvalidPluginError` lives in `pygeoapi/plugin.py:138`. It is **not** a `ProviderGenericError`, and SQLiteGPKG raises it.

---

## 1. Capability matrix (code, 0.24.0) vs docs matrix

Legend: Y = honoured, n = ignored (accepted, no effect), ✗ = raises, — = not applicable.
The docs matrix is at `docs/source/publishing/ogcapi-features.rst:19` (header), with rows
CSV :22, GeoJSON :26, OGR :29, Parquet :32, SQLiteGPKG :34, TinyDB :37.

| query arg / method | CSV | GeoJSON | SQLiteGPKG | OGR | Parquet | TinyDB |
|---|---|---|---|---|---|---|
| offset / limit | Y (in memory) | Y (in memory) | Y (SQL LIMIT/OFFSET) | limit Y; **offset only if `source_capabilities.paging: true`** | Y (streamed batches) | Y (in memory) |
| bbox | Y (point in box) | Y (shapely intersects) | Y (`Intersects(BuildMbr)`) | Y (`SetSpatialFilter`) | Y (**within**, needs bbox covering column or x/y fields) | Y (bounds overlap) |
| datetime_ | n | n | n | n | Y (`time_field`) | Y range / ✗ instant (see TinyDB) |
| properties (filter) | Y (string equality) | Y (`str()` equality) | Y (SQL `=`) | Y (OGR attribute filter) | Y (typed equality) | Y (typed equality) |
| sortby | n | n | n | n | n (not in signature) | Y (first key only) |
| select_properties | Y | Y | n | n | Y | n |
| skip_geometry | Y | Y | Y | Y | Y | n |
| q | n | n | n | n | n | ✗ (see TinyDB) |
| resulttype=hits | Y | Y | Y | Y | Y | Y |
| `count: false` suppresses numberMatched | Y | Y | Y | — (never sets it) | — (always sets a fake one) | Y |
| filterq / CQL2 | n | n | n | n | n | n |
| get() | Y | Y | Y | Y | Y (needs id_field) | Y (top-level `id` only) |
| create/update/delete | NotImpl | Y (API path broken, see GeoJSON) | NotImpl | NotImpl | NotImpl | Y |
| get_schema | NotImpl | NotImpl | NotImpl | NotImpl | NotImpl | NotImpl |
| get_domains | NotImpl | NotImpl | NotImpl | NotImpl | NotImpl | Y (first 10 records only) |
| `@crs_transform` | Y | Y | Y | no (native OSR transform) | Y | Y |
| whole file read per request | yes | yes | no | no | no | yes (TinyDB JSONStorage) |

---

## 2. Providers

### `CSV` — `pygeoapi.provider.csv_.CSVProvider`

- **Provider type(s) and base class:** `feature`. `class CSVProvider(BaseProvider)` (`pygeoapi/provider/csv_.py:48`). Registry entry `'CSV'` (`pygeoapi/plugin.py:42`). Any `type` string loads; the API only looks for `feature`/`record` providers.
- **Dependencies:** stdlib `csv`, and `shapely` (a core dependency) for bbox tests (`pygeoapi/provider/csv_.py:37`). No system libraries.
- **`data` forms:** a **string path only**. It is passed to builtin `open(self.data)` at load (`pygeoapi/provider/csv_.py:73`) and again on every request (`pygeoapi/provider/csv_.py:126`).
  - A relative path resolves against the **process CWD**. `tests/data/obs.csv` failed from the scratch CWD and worked from `vendor/pygeoapi` (executed).
  - URLs are not supported: an `https://…` value raises `FileNotFoundError` (executed). There is no zip/vsi support.
  - No `encoding=` is passed, so the locale default applies. A UTF-8 **BOM** turns the first header into `﻿id`, and queries then fail with `KeyError: 'id'` (executed).
  - The delimiter is fixed to `,`. A `;` file loads as a single column (executed).
  - A header-only file raises `StopIteration` at load (executed).
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data` | string (path) | yes | — | CSV file path, CWD-relative or absolute. | `pygeoapi/provider/csv_.py:73` |
  | `geometry.x_field` | string | **yes** | — | Column holding x/longitude. A missing `geometry` gives `KeyError: 'geometry'` at load (executed). | `pygeoapi/provider/csv_.py:61` |
  | `geometry.y_field` | string | **yes** | — | Column holding y/latitude. A missing key gives `KeyError: 'y_field'` at load (executed). | `pygeoapi/provider/csv_.py:62` |
  | `id_field` | string | effectively yes | — | Not checked at load. At query time it runs `row[self.id_field]`: with no `id_field` that is `KeyError: None`, with a wrong column name `KeyError: '<name>'` (executed). The column is typed `string` in fields. | `pygeoapi/provider/csv_.py:168`, `pygeoapi/provider/csv_.py:84` |
  | `properties` | list[str] | no | `[]` | Output allow-list, unioned with `select_properties`. It is iterated as a **`set`**, so the output order changes with `PYTHONHASHSEED` (executed). This contradicts "in order" in the docs. It does not filter `get_fields()`. | `pygeoapi/provider/csv_.py:180` |
  | `count` | bool | no | `true` | `numberMatched` (a full scan count) is present only when true. | `pygeoapi/provider/csv_.py:197` |
  | `x_field` / `y_field` (top-level) | — | — | — | **Ignored.** Without `geometry:` the provider fails with `KeyError: 'geometry'` (executed). | `pygeoapi/provider/csv_.py:61` |
  | `crs`, `storage_crs`, `always_xy` | common | no | CRS84 | Honoured via `@crs_transform`. Coordinates are assumed to be in `storage_crs`. Executed: `norwegian_urban_areas.csv` with storage 25833 returned CRS84 lon/lat, and CRS84 → EPSG:3857 worked. | `pygeoapi/provider/csv_.py:232` |
  | `editable` | bool | no | false | No effect: create/update/delete raise `NotImplementedError` (executed). | `pygeoapi/provider/base.py:192` |

- **Capabilities** (all executed on `obs.csv` unless noted):
  - **offset/limit:** yes. The provider reads every row into a list, then slices `[offset:offset+limit]` (`pygeoapi/provider/csv_.py:206`).
  - **bbox:** yes. A shapely point-in-box test (`pygeoapi/provider/csv_.py:227`), using only the first 4 values (`pygeoapi/provider/csv_.py:143`). A row counts as intersecting only if the coordinate key is absent (`None`) (`pygeoapi/provider/csv_.py:224`). An **empty-string** coordinate crashes the bbox query with `ValueError` (executed on `obs_malformatted.csv`).
  - **properties filter:** yes, **raw string equality** (`pygeoapi/provider/csv_.py:139`): `('stn_id','35')` matched 2 rows, while `('stn_id',35)` and `('value','93.90')` matched 0. An unknown column raises `ProviderInvalidQueryError('Invalid fieldname')` (`pygeoapi/provider/csv_.py:133`).
  - **datetime_, sortby, q, filterq:** ignored (not forwarded to `_load`, `pygeoapi/provider/csv_.py:253`).
  - **select_properties:** yes. An unknown name raises `ProviderQueryError()` with an empty message (`pygeoapi/provider/csv_.py:185`).
  - **skip_geometry:** yes; returns `geometry: null`.
  - **resulttype='hits':** yes. Returns `{type, features: [], numberMatched}`, with no `numberReturned`.
  - **get():** yes. It first runs a hits pass to size the scan (`pygeoapi/provider/csv_.py:124`), then a full scan. The identifier is compared as a string: `get(964)` (int) raises not found, while `get('964')` works.
  - **create/update/delete, get_schema, get_domains:** `NotImplementedError`.
  - **Memory:** the whole file is read and all features are built on each request.
- **Docs examples:**
  - `docs/source/publishing/ogcapi-features.rst:67` (the block starts at `:63`). Keys: `type`, `name: CSV`, `data: tests/data/obs.csv` (:68), `id_field: id` (:69), `geometry.x_field: long` / `geometry.y_field: lat` (:70–72), `crs` [28992, CRS84, 4326] (:73), `storage_crs: …/EPSG/0/28992` (:77). Note that `obs.csv` holds lon/lat, so declaring storage 28992 in the example is wrong for that file.
  - `docs/source/configuration.rst:256` shows `name: CSV`, `data: tests/data/obs.csv`, `id_field: id` with **no `geometry` block**. That snippet would fail with `KeyError: 'geometry'` (executed with the equivalent def).
- **Tests/fixtures:**
  - `tests/provider/test_csv__provider.py`: configs at :47 (obs), :61 (station_list), :75 (malformatted). Expected: 4 fields, `numberMatched` 5, first id `'371'`, station_list `numberMatched` 79.
  - Config blocks: `tests/pygeoapi-test-config.yml:149` (obs; `crs` + `storage_crs` CRS84), `tests/pygeoapi-test-config.yml:181` (norway_pop; `storage_crs` EPSG:25833, :190), `tests/pygeoapi-test-config-apirules.yml:153`, `tests/pygeoapi-test-config-admin.yml:135`, `tests/pygeoapi-test-config-envvars.yml:117`, `tests/pygeoapi-test-config-i18n.yml:181`, `tests/pygeoapi-test-config-failing-collection.yml:147`, `tests/pygeoapi-test-config-hidden-resources.yml:148`, `tests/pygeoapi-test-config-pubsub.yml:153`, `pygeoapi-config.yml:140`, `docker/default.config.yml:129`.
  - Fixtures: `tests/data/obs.csv` (5 rows), `obs_malformatted.csv` (rows 297/964 have bad coordinates), `station_list.csv` (79), `norwegian_urban_areas.csv` (15 rows, EPSG:25833).
- **Errors raised:**
  - `ProviderInvalidQueryError` for an unknown property filter (`pygeoapi/provider/csv_.py:135`).
  - `ProviderQueryError` for an unknown `select_properties`/`properties` column (`pygeoapi/provider/csv_.py:185`).
  - `ProviderItemNotFoundError` from `get` (`pygeoapi/provider/csv_.py:273`).
  - Raw errors, which surface as HTTP 500: `KeyError` (missing `geometry`/`x_field`/`y_field`, `id_field`, BOM), `FileNotFoundError`, `StopIteration` (empty file), and `ValueError` (bbox over an empty coordinate).
- **Gotchas / conflicts:**
  - **Confirmed:** CSV requires `geometry: {x_field, y_field}`. Top-level `x_field`/`y_field` are ignored (executed). The schema models the `geometry` object (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:559`).
  - `get_fields()` types come from the **first data row only** (`pygeoapi/provider/csv_.py:77`), and the x/y columns are excluded (`pygeoapi/provider/csv_.py:82`). As a result the coordinate columns can never be filtered over HTTP.
  - Booleans (`true`/`false`) are typed **`integer`**, because `bool` is an `int` subclass (`pygeoapi/util.py:120`, executed). Values with leading zeros stay strings.
  - Output values are typed **per cell**, so one column can mix types: `zip` came back as `"02134"` and `90210` (executed).
  - With a non-numeric x, the y column **leaks into properties**, because `row.pop(x)` raises before `pop(y)` runs (`pygeoapi/provider/csv_.py:159`). Executed: `lat` appeared in the properties of rows 297/964. Those rows emit `{"type":"Point","coordinates":null}`, which is invalid GeoJSON.
  - The docs matrix (`docs/source/publishing/ogcapi-features.rst:22`) matches the code.

### `GeoJSON` — `pygeoapi.provider.geojson.GeoJSONProvider`

- **Provider type(s) and base class:** `feature`. `class GeoJSONProvider(BaseProvider)` (`pygeoapi/provider/geojson.py:44`). Registry `'GeoJSON'` (`pygeoapi/plugin.py:49`). The docstring says "Not thread safe, a single server process is assumed" (`pygeoapi/provider/geojson.py:53`).
- **Dependencies:** stdlib `json`, plus `shapely` for bbox (`pygeoapi/provider/geojson.py:36`). No system libraries.
- **`data` forms:** a **string path** to a FeatureCollection file, guarded by `os.path.exists` (`pygeoapi/provider/geojson.py:84`, `pygeoapi/provider/geojson.py:110`). A relative path resolves against the CWD.
  - A **missing file is not an error**: it logs a warning, `get_fields()` returns `{}`, and every query returns an empty collection (`pygeoapi/provider/geojson.py:114`).
  - A URL or non-existent path therefore "works", but empty.
  - The file must be a `FeatureCollection` (`assert`, `pygeoapi/provider/geojson.py:120`).
  - Writes (create/update/delete) rewrite the file in place (`pygeoapi/provider/geojson.py:235`).
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data` | string (path) | yes | — | GeoJSON FeatureCollection file. | `pygeoapi/provider/geojson.py:110` |
  | `id_field` | string | recommended | — | When a feature has no top-level `id`, it is copied from `properties[id_field]` (`pygeoapi/provider/geojson.py:138`). It is also the key used by create/update/delete. | `pygeoapi/provider/geojson.py:139`, `pygeoapi/provider/geojson.py:229` |
  | `properties` | list[str] | no | `[]` | Output allow-list, unioned with `select_properties`. Keeps source order (dict comprehension). | `pygeoapi/provider/geojson.py:142` |
  | `count` | bool | no | `true` | `numberMatched` is present when `count` is true or on hits. | `pygeoapi/provider/geojson.py:191` |
  | `editable` | bool | no | false | Enables the API transaction routes. Their behaviour is covered under Capabilities. | `pygeoapi/api/itemtypes.py:788` |
  | `crs`, `storage_crs` | common | no | CRS84 | Honoured via `@crs_transform`. | `pygeoapi/provider/geojson.py:165` |
  | `storage_crs_coordinate_epoch`, `uri_field`, `title_field` | common | no | — | Appear in test/sample configs. The API reads them; the provider does not. | `tests/pygeoapi-test-config.yml:251`, `tests/pygeoapi-test-config.yml:373`, `pygeoapi-config.yml:180` |

- **Capabilities** (read, verified in §3 probes):
  - **offset/limit:** in-memory slice (`pygeoapi/provider/geojson.py:197`).
  - **bbox:** shapely `intersects`, first 4 values. A `null` geometry counts as intersecting (`pygeoapi/provider/geojson.py:157`).
  - **properties filter:** `str(value) == str(param)`. A feature lacking the property raises `KeyError` (`pygeoapi/provider/geojson.py:125`).
  - **datetime_, sortby, q, filterq:** ignored.
  - **select_properties / skip_geometry:** yes.
  - **hits:** yes (`pygeoapi/provider/geojson.py:194`).
  - **get():** compares `str(feature['id']) == identifier` (`pygeoapi/provider/geojson.py:214`).
  - **create/update/delete:** implemented against a **dict** item (`pygeoapi/provider/geojson.py:221`), but the API passes **bytes** (`pygeoapi/api/itemtypes.py:804`). See §3 for the executed result.
  - `create()` returns `None`, so the API `Location` header becomes `…/items/None` (`pygeoapi/api/itemtypes.py:815`).
  - `update()`/`delete()` compare `feature[id_field] == identifier` without `str()` (`pygeoapi/provider/geojson.py:248`), so integer ids never match a URL identifier (read).
  - **get_schema/get_domains:** not implemented.
  - **Memory:** the whole file is loaded and parsed on every call (`pygeoapi/provider/geojson.py:106`).
- **Docs examples:** `docs/source/publishing/ogcapi-features.rst:197` (block at :193). Keys: `type`, `name: GeoJSON`, `data: tests/data/file.json` (:198; **that file does not exist** in the repo), `id_field: id` (:199).
- **Tests/fixtures:**
  - `tests/provider/test_geojson_provider.py`. It writes `/tmp/test.geojson` (`tests/provider/test_geojson_provider.py:38`). Config at :63; transaction tests at :143, :151, :170 call the provider with dicts.
  - Config blocks: `tests/pygeoapi-test-config.yml:245` (lakes: `crs`, `storage_crs`, `storage_crs_coordinate_epoch: 2017.23`), `tests/pygeoapi-test-config.yml:370` (objects: `items.geojson`, `id_field: fid`, `uri_field: uri`), `pygeoapi-config.yml:177` (`title_field: name`), `tests/pygeoapi-test-config-apirules.yml:213`, `tests/pygeoapi-test-config-apirules.yml:305`, `tests/pygeoapi-test-config-enclosure.yml:137`, `tests/pygeoapi-test-config-failing-collection.yml:203`, `docker/default.config.yml:157`.
  - Fixtures: `tests/data/ne_110m_lakes.geojson`, `items.geojson`, `ne_110m_populated_places_simple.geojson`.
- **Errors raised:**
  - `ProviderItemNotFoundError` (`pygeoapi/provider/geojson.py:219`).
  - Raw errors: `AssertionError` (not a FeatureCollection), `KeyError` (property filter on a feature missing the property), `json.JSONDecodeError`, and `IndexError` in `get_fields()` on an empty FeatureCollection (`pygeoapi/provider/geojson.py:87`).
- **Gotchas:**
  - Field types come from the first feature only (`pygeoapi/provider/geojson.py:87`).
  - A missing file silently yields an empty collection. The probe step must check `get_fields()` is non-empty.
  - The docs matrix (`docs/source/publishing/ogcapi-features.rst:26`) claims transactions ❌, even though create/update/delete exist in code.

### `SQLiteGPKG` — `pygeoapi.provider.sqlite.SQLiteGPKGProvider`

- **Provider type(s) and base class:** `feature`. `class SQLiteGPKGProvider(BaseProvider)` (`pygeoapi/provider/sqlite.py:51`). Registry `'SQLiteGPKG'` (`pygeoapi/plugin.py:66`).
- **Dependencies:**
  - stdlib `sqlite3` (`pygeoapi/provider/sqlite.py:34`), which must be built with extension loading.
  - **SpatiaLite is required for both `.sqlite` and `.gpkg`.** It is loaded with `SELECT load_extension(SPATIALITE_EXTENSION)`, where `SPATIALITE_EXTENSION = os.getenv('SPATIALITE_LIBRARY_PATH', 'mod_spatialite.so')` (`pygeoapi/provider/sqlite.py:47`, `pygeoapi/provider/sqlite.py:197`). On macOS the env var must point at `mod_spatialite.dylib`.
  - GeoPackage support uses SpatiaLite's `AutoGPKGStart()` virtual tables (`pygeoapi/provider/sqlite.py:220`).
  - System packages: `libsqlite3-mod-spatialite` (Debian) or Homebrew `libspatialite`.
  - Docs: "Requires Spatialite installation" (`docs/source/publishing/ogcapi-features.rst:755`).
- **`data` forms:** a string path, checked with `os.path.exists` and then passed to `sqlite3.connect(self.data)` (`pygeoapi/provider/sqlite.py:180`). A relative path resolves against the CWD. A missing path raises **`InvalidPluginError`** (`pygeoapi/provider/sqlite.py:184`). No URI mode is used.
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data` | string (path) | yes | — | `.sqlite` (SpatiaLite) or `.gpkg` file. | `pygeoapi/provider/sqlite.py:180` |
  | `table` | string | **yes** | — | Table/layer name. A missing key raises `KeyError` (`pygeoapi/provider/sqlite.py:68`). It is interpolated **unquoted** into SQL: `PRAGMA table_info({table})`, the geometry-column lookup, and `FROM {table}`. For GPKG it is rewritten to the SpatiaLite virtual table `vgpkg_<table>`. | `pygeoapi/provider/sqlite.py:68`, `pygeoapi/provider/sqlite.py:234`, `pygeoapi/provider/sqlite.py:241`, `pygeoapi/provider/sqlite.py:264` |
  | `id_field` | string | **yes** | — | Load asserts it appears in `PRAGMA table_info` values, else `InvalidPluginError` (`pygeoapi/provider/sqlite.py:254`). It becomes the feature `id` (`pygeoapi/provider/sqlite.py:166`) and the key for `get()` (`pygeoapi/provider/sqlite.py:351`). | `pygeoapi/provider/sqlite.py:255` |
  | `count` | bool | no | `true` | Extra `SELECT COUNT(*)` per query. | `pygeoapi/provider/sqlite.py:301` |
  | `properties` | list | no | — | **Ignored in output**: all non-geometry columns are always selected (`pygeoapi/provider/sqlite.py:259`). It only filters queryables at API level. | `pygeoapi/provider/sqlite.py:261` |
  | `crs`, `storage_crs` | common | no | CRS84 | `@crs_transform`. The bbox is used as-is in `BuildMbr` with no SRID, so it must already be in storage CRS (the API does that). | `pygeoapi/provider/sqlite.py:135`, `pygeoapi/provider/sqlite.py:268` |
  | (geometry column) | — | — | auto | No config key. Auto-detected from `gpkg_geometry_columns.column_name` or `geometry_columns.f_geometry_column`. | `pygeoapi/provider/sqlite.py:217`, `pygeoapi/provider/sqlite.py:230` |

- **Capabilities** (read, verified in §3):
  - **offset/limit:** SQL `limit ? offset ?` (`pygeoapi/provider/sqlite.py:317`).
  - **bbox:** `Intersects(geom, BuildMbr(?,?,?,?))` (`pygeoapi/provider/sqlite.py:135`).
  - **properties filter:** `name=?` joined with AND (`pygeoapi/provider/sqlite.py:129`).
  - **skip_geometry:** yes.
  - **hits:** `COUNT(*)` (`pygeoapi/provider/sqlite.py:304`).
  - **datetime_, sortby, select_properties, q, filterq:** ignored.
  - **get():** yes.
  - **Transactions, get_schema, get_domains:** no.
  - Queries use `SELECT DISTINCT` (`pygeoapi/provider/sqlite.py:316`).
- **Docs examples:**
  - `docs/source/publishing/ogcapi-features.rst:763`: `data: ./tests/data/ne_110m_admin_0_countries.sqlite` (:764), `id_field: ogc_fid` (:765), `table: ne_110m_admin_0_countries` (:766).
  - `docs/source/publishing/ogcapi-features.rst:775`: `data: ./tests/data/poi_portugal.gpkg` (:776), `id_field: osm_id` (:777), `table: poi_portugal` (:778).
- **Tests/fixtures:**
  - `tests/provider/test_sqlite_geopackage_provider.py`: configs at :44 (sqlite) and :55 (gpkg). Expected fields for the sqlite file are **only** `{ogc_fid, scalerank}` (:69). Hits 177 (:128).
  - No `tests/*.yml` block uses SQLiteGPKG. Only `docker/default.config.yml:183` does.
  - Fixtures: `tests/data/ne_110m_admin_0_countries.sqlite`, `tests/data/poi_portugal.gpkg`.
- **Errors raised:**
  - `InvalidPluginError` for a missing file (:184), GPKG support not loaded (:227), and table/id_field not found (:257).
  - `ProviderConnectionError` when sqlite3 has no extension loading (:190), when spatialite cannot load (:200), when `PRAGMA` fails (:238), and when no geometry column is found (:250).
  - `ProviderItemNotFoundError` (:364).
- **Gotchas:**
  - `get_fields()` maps only declared types `INTEGER`/`REAL` → `number` and `TEXT*`/`BLOB` → `string` (`pygeoapi/provider/sqlite.py:98`). Other types (`VARCHAR`, `FLOAT`, `DOUBLE`, `DATE`, `MEDIUMINT`, …) are **dropped**, so those columns are neither queryables nor HTTP-filterable. They are still returned in properties.
  - `INTEGER` is reported as `number`, not `integer`.
  - The docs matrix (`docs/source/publishing/ogcapi-features.rst:34`) matches the code.

### `OGR` — `pygeoapi.provider.ogr.OGRProvider`

- **Provider type(s) and base class:** `feature`. `class OGRProvider(BaseProvider)` (`pygeoapi/provider/ogr.py:53`). Registry `'OGR'` (`pygeoapi/plugin.py:59`).
  - Driver-specific helpers: `ESRIJSON` → `ESRIJSONHelper`, `WFS` → `WFSHelper`, anything else → `CommonSourceHelper` (`pygeoapi/provider/ogr.py:69`).
- **Dependencies:**
  - `from osgeo import gdal, ogr, osr` (`pygeoapi/provider/ogr.py:40`). The pip package is `GDAL`, which must match the system libgdal version.
  - Docs: "Requires Python package gdal" (`docs/source/publishing/ogcapi-features.rst:302`) and GDAL 3+ (`docs/source/publishing/ogcapi-features.rst:309`).
  - System: libgdal with the needed drivers, including network drivers for WFS/ESRIJSON/`/vsicurl/`.
  - Importing the module sets `OGR_GEOJSON_MAX_OBJ_SIZE=20MB` unless already set (`pygeoapi/provider/ogr.py:74`).
- **`data` forms:** **must be an object** (`self.data_def = provider_def['data']`, then `.get`; `pygeoapi/provider/ogr.py:126`). `data.source` is passed verbatim to `driver.Open()` or `gdal.OpenEx()` (`pygeoapi/provider/ogr.py:201`, `pygeoapi/provider/ogr.py:185`), so any GDAL dataset name works. Seen in docs/tests:
  - CWD-relative paths (`./tests/data/poi_portugal.gpkg`).
  - `/vsizip/…zip` and `/vsicurl/https://…`.
  - `WFS:https://…` and `ESRIJSON:http…`.
  - Bare `https://…` ESRI query URLs.
  - `PG: host=… password=…` connection strings (these contain secrets).
  - For the data-root restriction, parse GDAL prefixes (`/vsi*/`, `DRIVER:`); a plain `realpath` check is not enough.
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data.source_type` | string (OGR driver short name) | **yes** | — | Passed to `GetDriverByName`, and selects the helper. An unknown driver raises bare `Exception('No Driver for Source: …')`. | `pygeoapi/provider/ogr.py:147`, `pygeoapi/provider/ogr.py:178` |
  | `data.source` | string | **yes** | — | GDAL dataset name or connection string (see above). May contain credentials (`PG:` password). | `pygeoapi/provider/ogr.py:201` |
  | `data.source_capabilities.paging` | bool | no | `{'paging': False}` | Enables offset paging: OGR SQL `LIMIT/OFFSET` for Common/ESRIJSON, and `OGR_WFS_*` config options for WFS. If `source_capabilities` is given without `paging`, it raises `KeyError`. | `pygeoapi/provider/ogr.py:140`, `pygeoapi/provider/ogr.py:303` |
  | `data.source_options` | map str→scalar | no | `{}` | Each pair becomes `gdal.SetConfigOption(k, str(v))`, which is **process-global**. | `pygeoapi/provider/ogr.py:134` |
  | `data.gdal_ogr_options` | map str→scalar | no | `{}` | Same mechanism as `source_options` (global config options). | `pygeoapi/provider/ogr.py:129` |
  | `data.open_options` | map str→scalar | no | `{}` | When non-empty, opening uses `gdal.OpenEx(..., open_options=['K=V',…])`. | `pygeoapi/provider/ogr.py:138`, `pygeoapi/provider/ogr.py:183` |
  | `data.source_srs` / `data.target_srs` | string | — | — | **Removed.** Only a warning is logged ("no longer supported"). Use `storage_crs`/`crs` instead. | `pygeoapi/provider/ogr.py:143`, `docs/source/publishing/ogcapi-features.rst:387` |
  | `layer` | string | **yes** | — | Top-level. Passed to `GetLayerByName`. A missing value raises bare `Exception("Need explicit 'layer' attr…")`. | `pygeoapi/provider/ogr.py:159`, `pygeoapi/provider/ogr.py:620` |
  | `id_field` | string | **yes** | — | A missing value raises bare `Exception`. It is popped out of properties into the feature `id` and used for `get()` via an attribute filter. | `pygeoapi/provider/ogr.py:152`, `pygeoapi/provider/ogr.py:515`, `pygeoapi/provider/ogr.py:415` |
  | `geom_field` | string | no | — | Selects a named geometry field (`GetGeomFieldRef`) in multi-geometry layers. | `pygeoapi/provider/ogr.py:149`, `pygeoapi/provider/ogr.py:489` |
  | `properties` | list | no | — | Honoured: other properties are dropped. | `pygeoapi/provider/ogr.py:507` |
  | `crs`, `storage_crs` | common | no | CRS84 | Native OSR `CoordinateTransformation`, not `@crs_transform`. EPSG codes are parsed from the URI. `always_xy` is not used. | `pygeoapi/provider/ogr.py:386`, `pygeoapi/provider/ogr.py:381` |
  | `count`, `time_field`, `editable` | common | — | — | Ignored by the provider. There are no transactions and no `numberMatched` in results mode. | `pygeoapi/provider/ogr.py:533` |

- **Drivers documented or tested:**
  - Docs: ESRI Shapefile (`docs/source/publishing/ogcapi-features.rst:317`), WFS (:333), ESRIJSON (:359), PostgreSQL (:380). The docs also recommend VRT for flat files such as CSV (:306).
  - Class docstring: GPKG, SQLite, GeoJSON, ESRI Shapefile, WFS v2 (`pygeoapi/provider/ogr.py:64`).
  - Tests add CSV over `/vsicurl/` (`tests/provider/test_ogr_csv_provider.py:53`).
- **Capabilities** (read, verified in §3):
  - **limit:** stops after `limit` features (`pygeoapi/provider/ogr.py:557`).
  - **offset:** only when `source_capabilities.paging` is true. With `CommonSourceHelper` and `offset > 0`, the provider runs `SELECT * FROM "<layer>" LIMIT n OFFSET m` (`pygeoapi/provider/ogr.py:714`), and bbox/attribute filters are then applied to that **result set** (`pygeoapi/provider/ogr.py:316`). With paging off (the default), offset is ignored (read).
  - **bbox:** `SetSpatialFilter` (`pygeoapi/provider/ogr.py:316`).
  - **properties filter:** `SetAttributeFilter("k = 'v' and …")`, with values sanitized (`pygeoapi/provider/ogr.py:325`, `pygeoapi/provider/base.py:297`).
  - **skip_geometry:** yes.
  - **hits:** `GetFeatureCount()` (`pygeoapi/provider/ogr.py:579`).
  - **datetime_, sortby, select_properties, q, filterq:** ignored.
  - Results responses carry **no `numberMatched`/`numberReturned`** (`pygeoapi/provider/ogr.py:533`).
  - **get():** yes (`pygeoapi/provider/ogr.py:397`).
  - **No transactions, schema or domains.**
  - **Memory:** streaming via OGR.
- **Docs examples** (`docs/source/publishing/ogcapi-features.rst`):
  - Shapefile at :315: `source_type`, `source`, `source_options.ADJUST_GEOM_TYPE`, `gdal_ogr_options.SHPT`, `id_field: fid`, `layer`.
  - WFS at :331: `source_options.{VERSION, OGR_WFS_PAGING_ALLOWED, OGR_WFS_LOAD_MULTIPLE_LAYER_DEFN}`, `gdal_ogr_options.{GDAL_CACHEMAX, GDAL_HTTP_PROXY, GDAL_PROXY_AUTH, CPL_DEBUG}`, `crs`, `storage_crs`, `id_field: gml_id`, `layer: rdinfo:stations`. The `gdal_ogr_options` line at :339 is **mis-indented**.
  - ESRIJSON at :357: `source_capabilities.paging`, `open_options.FEATURE_SERVER_PAGING`, `gdal_ogr_options.{EMPTY_AS_NULL, GDAL_CACHEMAX, CPL_DEBUG}`, `id_field: BGS_ID`, `layer: ESRIJSON`.
  - PostgreSQL at :378: `source: "PG: …password=…"`, `id_field`, `layer: osm.hotosm_bdi_waterways`, `geom_field: foo_geom`.
  - Docstring example: `pygeoapi/provider/ogr.py:83`.
- **Tests/fixtures:**
  - `tests/provider/test_ogr_shapefile_provider.py:51` (4326 `.shp`) and :70 (`/vsizip/./tests/data/dutch_addresses_shape_28992.zip` with `storage_crs` 28992).
  - `tests/provider/test_ogr_gpkg_provider.py:45` (`poi_portugal.gpkg`), :105 (`dutch_addresses_4326.gpkg`, layer `OGRGeoJSON`), :124 (28992).
  - `tests/provider/test_ogr_sqlite_provider.py:47` (`dutch_addresses_4326.sqlite`, layer `ogrgeojson`).
  - Online: `tests/provider/test_ogr_csv_provider.py:47`, `tests/provider/test_ogr_esrijson_provider.py:48`, `tests/provider/test_ogr_wfs_provider_live.py:52`. `tests/provider/test_ogr_wfs_provider.py:48` is mocked.
  - Config: `tests/pygeoapi-test-config-ogr.yml:112` (WFS), :161 and :205 (GPKG 4326/28992), :251 (poi), :292 (ESRIJSON, **no `layer`**, so it cannot load), :333 (CSV over `/vsicurl/`).
- **Errors raised:**
  - Bare `Exception` for a missing `id_field`/`layer`, no driver, "Cannot open OGR Source", and "Cannot get Layer".
  - `ProviderConnectionError` when GDAL raises `RuntimeError` on open, and on HTTP 5xx via the error handler (`pygeoapi/provider/ogr.py:874`).
  - `ProviderQueryError` on `RuntimeError` in query/get.
  - `ProviderGenericError` for anything else (`pygeoapi/provider/ogr.py:355`).
  - `ProviderItemNotFoundError` for an unknown id (`pygeoapi/provider/ogr.py:468`).
- **Gotchas:**
  - `get_fields()` **swallows** generic exceptions and returns `{}` (`pygeoapi/provider/ogr.py:272`). A bad layer name may still load (verified in §3).
  - OGR type names pass through lowercased. `integer64` → `integer`, `real` → `number`, `datetime` → `string`/`date-time`. Other names such as `date` and `stringlist` are not valid JSON Schema types (`pygeoapi/provider/ogr.py:251`).
  - Config options are process-global, so two OGR collections can clash.

### `Parquet` — `pygeoapi.provider.parquet.ParquetProvider`

- **Provider type(s) and base class:** `feature`. `class ParquetProvider(BaseProvider)` (`pygeoapi/provider/parquet.py:99`). Registry `'Parquet'` (`pygeoapi/plugin.py:61`).
- **Dependencies:** `geopandas`, `pyarrow`, `s3fs` and `python-dateutil` are **all imported at module import** (`pygeoapi/provider/parquet.py:36`, `pygeoapi/provider/parquet.py:37`, `pygeoapi/provider/parquet.py:38`, `pygeoapi/provider/parquet.py:42`). The docs say only pyarrow is required, with geopandas needed for GeoParquet (`docs/source/publishing/ogcapi-features.rst:583`, `docs/source/publishing/ogcapi-features.rst:585`). **Conflict:** the code needs all four.
- **`data` forms:** **an object** with `source` (`self.data.get('source')`, `pygeoapi/provider/parquet.py:124`).
  - A `source` beginning with `s3` has its scheme stripped and is read through `s3fs.S3FileSystem(default_cache_type='none')` (`pygeoapi/provider/parquet.py:149`).
  - Anything else goes to `pyarrow.dataset.dataset(source)`, which accepts a file, a **directory of files**, or URIs that pyarrow understands (`pygeoapi/provider/parquet.py:157`). Relative paths resolve against the CWD.
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data.source` | string | **yes** | — | Local file/dir or `s3://bucket/key`. A missing value raises `ProviderGenericError('Need explicit "source" attr …')`. | `pygeoapi/provider/parquet.py:124`, `pygeoapi/provider/parquet.py:146` |
  | `data.batch_size` | int | no | `20000` | Rows per scanner batch. | `pygeoapi/provider/parquet.py:134` |
  | `data.batch_readahead` | int | no | `2` | Number of batches prefetched. | `pygeoapi/provider/parquet.py:141` |
  | `id_field` | string | no (yes for `get()`) | — | Must exist in the schema, else a load error (`schema.field`). Without it the pandas index becomes the id. | `pygeoapi/provider/parquet.py:160`, `pygeoapi/provider/parquet.py:166` |
  | `x_field` | string \| [min,max] | conditional | — | **Top-level** key. Used for bbox only when there is no GeoParquet bbox covering. A string for points; a 2-list of min/max columns otherwise. | `pygeoapi/provider/parquet.py:232`, `pygeoapi/provider/parquet.py:236` |
  | `y_field` | string \| [min,max] | conditional | — | Same as `x_field`. | `pygeoapi/provider/parquet.py:238`, `pygeoapi/provider/parquet.py:242` |
  | `time_field` | string | for datetime | — | Required for `datetime`, else `ProviderQueryError`. | `pygeoapi/provider/parquet.py:386` |
  | `count`, `properties`, `editable` | common | — | — | **Ignored.** | `pygeoapi/provider/parquet.py:583` |
  | `crs`, `storage_crs` | common | no | CRS84 | `@crs_transform`. The file's own GeoParquet `crs` is used only to build the GeoSeries (`pygeoapi/provider/parquet.py:191`). | `pygeoapi/provider/parquet.py:318` |

- **Capabilities** (read, verified in §3):
  - **offset/limit:** streamed batches (`pygeoapi/provider/parquet.py:523`).
  - **bbox:** needs geometry plus a covering bbox or x/y fields, else `ProviderQueryError`. The semantic is **containment**, not intersection: `xmin >= minx AND xmax <= maxx …` (`pygeoapi/provider/parquet.py:371`).
  - **datetime:** instant or interval with `..` (`pygeoapi/provider/parquet.py:393`).
  - **properties filter:** typed equality (`pygeoapi/provider/parquet.py:410`).
  - **select_properties:** projection, which always adds the id and geometry (`pygeoapi/provider/parquet.py:414`).
  - **skip_geometry:** drops the geometry column (`pygeoapi/provider/parquet.py:423`).
  - **hits:** `count_rows()` (`pygeoapi/provider/parquet.py:606`).
  - **sortby:** not a parameter, so ignored. **q/filterq:** ignored.
  - In results mode `numberMatched = offset + rows read (limit+1 look-ahead)`, which is **not a true total** (`pygeoapi/provider/parquet.py:561`).
  - **get():** typed id equality; asserts the id is unique (`pygeoapi/provider/parquet.py:469`).
- **Docs examples:**
  - `docs/source/publishing/ogcapi-features.rst:594`: `data.source: ./tests/data/parquet/naive/random.parquet` (:596), `id_field: id`, `time_field: time`, `x_field: [minlon, maxlon]` (:599), `y_field: [minlat, maxlat]` (:602).
  - `docs/source/publishing/ogcapi-features.rst:615`: `id_field: id`, `data.source: …/geoparquet1.1/nyc_subset_overture.parquet` (:618), `batch_size: 10000` (:619), `batch_readahead: 2` (:620).
- **Tests/fixtures:**
  - `tests/provider/test_parquet_provider.py`: configs at :43 (random: `x_field: lon`, `y_field: lat`, `time_field: time`), :58 (nogeom), :71 (nocrs), :87 (GeoParquet without bbox, no `id_field`), :109 (Overture with bbox).
  - No config yml blocks use Parquet.
  - Fixtures: `tests/data/parquet/naive/{random,random_nocrs,random_nogeom}.parquet`, `tests/data/parquet/geoparquet1.1/{data-polygon-encoding_wkb_no_bbox,nyc_subset_overture}.parquet`.
- **Errors raised:**
  - `ProviderGenericError` for a missing source, and wrapping any non-Runtime error in query/get (`pygeoapi/provider/parquet.py:445`).
  - `ProviderQueryError` for bbox without geometry or bbox support, datetime without `time_field`, and `RuntimeError`.
  - `ProviderItemNotFoundError` (`pygeoapi/provider/parquet.py:475`).
- **Gotchas:**
  - The warning at `pygeoapi/provider/parquet.py:172` fires when the id **is** numeric, yet says "not numeric".
  - A local path beginning with `s3…` (for example `s3data/x.parquet`) is misrouted to S3 (`pygeoapi/provider/parquet.py:149`).

### `TinyDB` — `pygeoapi.provider.tinydb_.TinyDBProvider`

- **Provider type(s) and base class:** `feature`. `class TinyDBProvider(BaseProvider)` (`pygeoapi/provider/tinydb_.py:51`). Registry `'TinyDB'` (`pygeoapi/plugin.py:68`).
  - `TinyDBCatalogueProvider(TinyDBProvider)` (`pygeoapi/provider/tinydb_.py:392`, registry `pygeoapi/plugin.py:69`) inherits `__init__`, `query`, `get`, `create`, `update`, `delete` and `get_domains`. It overrides `_excludes`, `get_fields` (adds `q`), `_add_extra_fields` and `_add_search_query`.
  - `name: TinyDB` under `server.manager` in the test configs is the **process manager**, not this provider (`tests/pygeoapi-test-config.yml:52`).
- **Dependencies:** `tinydb` (pip `tinydb`, `pygeoapi/provider/tinydb_.py:40`), `python-dateutil` (:38) and `shapely` (:39). All are core dependencies. No system libraries.
- **`data` forms:** a string path to an existing TinyDB JSON file. A missing file raises `ProviderConnectionError` (`pygeoapi/provider/tinydb_.py:69`). The file is opened read-only when not writable (`pygeoapi/provider/tinydb_.py:77`). A relative path resolves against the CWD. Documents live in the `_default` table and are GeoJSON Features with a top-level `id`.
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `data` | string (path) | yes | — | Must exist. | `pygeoapi/provider/tinydb_.py:69` |
  | `id_field` | string | no (API only) | — | **Not used for lookups**: `get()`, `update()` and `delete()` use the top-level `id` (`pygeoapi/provider/tinydb_.py:284`, `pygeoapi/provider/tinydb_.py:335`). Docs show `id_field: identifier`. | `docs/source/publishing/ogcapi-features.rst:867` |
  | `time_field` | string | no | — | Datetime filters `properties.<time_field>`. When unset, the top-level `time` is used. | `pygeoapi/provider/tinydb_.py:181`, `pygeoapi/provider/tinydb_.py:187` |
  | `count` | bool | no | `true` | Adds `numberMatched`. | `pygeoapi/provider/tinydb_.py:232` |
  | `editable` | bool | no | false | Enables create/update/delete at API level. | `docs/source/publishing/ogcapi-features.rst:864` |
  | `properties` | list | no | — | **Ignored** by the provider; affects API queryables only. | `pygeoapi/provider/tinydb_.py:240` |
  | `include_extra_query_parameters` | bool | no | false | Docs example. | `docs/source/publishing/ogcapi-features.rst:918` |
  | `crs`, `storage_crs` | common | no | CRS84 | `@crs_transform`. | `pygeoapi/provider/tinydb_.py:141` |

- **Capabilities** (read, verified in §3):
  - **offset/limit:** in-memory slice (`pygeoapi/provider/tinydb_.py:268`).
  - **bbox:** bounds overlap (`pygeoapi/provider/tinydb_.py:482`).
  - **datetime:** range filters use string `>=`/`<=` (:194). An **instant** calls `getattr(Q, <Query>)`, which raises `TypeError` (read, `pygeoapi/provider/tinydb_.py:200`).
  - **properties filter:** exact typed equality. Names must be in fields, else `ProviderInvalidQueryError` (`pygeoapi/provider/tinydb_.py:205`).
  - **sortby:** first key only, Python sort (`pygeoapi/provider/tinydb_.py:265`).
  - **select_properties / skip_geometry:** ignored.
  - **q:** the feature provider's `_add_search_query` returns the bare `Query` (`pygeoapi/provider/tinydb_.py:386`), which the search then evaluates (verified in §3).
  - **hits:** yes.
  - **get_domains:** yes, but it runs `self.query()` with its default `limit=10` (`pygeoapi/provider/tinydb_.py:124`).
  - **Transactions:** yes, through `_load_and_prepare_item`, which accepts JSON str/bytes (`pygeoapi/provider/base.py:250`). The id comes from `id`, then `properties.identifier`, else a uuid4 (`pygeoapi/provider/tinydb_.py:310`).
- **Docs examples:** `docs/source/publishing/ogcapi-features.rst:865` (`editable`, `data: /path/to/file.db`, `id_field: identifier`, `time_field: datetimefield`) and `docs/source/publishing/ogcapi-features.rst:914` (the same plus `include_extra_query_parameters: true`).
- **Tests/fixtures:**
  - `tests/provider/test_tinydb_provider.py`: config at :81 copies the fixture to `tmp_path`, with `id_field: IDENTIFIER` and `time_field: DATE`. 11 fields, 50 records.
  - No feature-TinyDB blocks in `tests/*.yml`.
  - Fixture: `tests/data/canada-hydat-daily-mean-02HC003.tinydb`.
- **Errors raised:**
  - `ProviderConnectionError` for a missing file.
  - `ProviderInvalidQueryError` for a bad property name or a `SyntaxError` in the query (`pygeoapi/provider/tinydb_.py:230`).
  - `ProviderItemNotFoundError('record does not exist')` (`pygeoapi/provider/tinydb_.py:287`).
  - `ProviderInvalidDataError` for invalid JSON, a missing identifier, missing geometry/properties, or "record already exists" (`pygeoapi/provider/base.py:259`, `pygeoapi/provider/base.py:291`).

---

## 3. Probe log (condensed)

CSV: see the CSV section; every probe there was executed. The remaining providers are
filled in below as their probes complete.

## 4. Cross-cutting notes for descriptors

- **Path resolution:** every file provider opens `data`/`data.source` relative to the **pygeoapi process CWD**. The only exceptions are OGR (GDAL dataset names) and Parquet (pyarrow/s3fs URIs). The app should store absolute paths, or paths relative to a declared data root that the backend resolves.
- **Secrets:** OGR `data.source` can embed a password (`PG: … password=…`, `docs/source/publishing/ogcapi-features.rst:381`). Treat it as `secret` when `source_type` is PostgreSQL. `gdal_ogr_options.GDAL_PROXY_AUTH` (`docs/source/publishing/ogcapi-features.rst:342`) is also a secret.
- **Load-time vs query-time failures:** CSV (bad `id_field`) and OGR (bad layer) can load successfully and fail on the first query. The probe step must run `query(limit=…)` as well as `load_plugin`.
