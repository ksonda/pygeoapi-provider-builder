# Tile, map, record and STAC providers — pygeoapi 0.24.0

Phase 0 research for the pygeoapi Provider Builder. It covers 13 core providers and the API layers that call them:

- **Tiles:** MVT-tippecanoe, MVT-proxy, MVT-postgresql, MVT-elastic, WMTSFacade
- **Maps:** WMSFacade, MapScript
- **Records:** TinyDBCatalogue, ElasticsearchCatalogue, CSWFacade
- **STAC:** FileSystem, AzureBlobStorage, Hateoas

**Ground truth.** All facts come from pygeoapi **0.24.0** (tag 0.24.0, commit f765a64), vendored at `vendor/pygeoapi`. Every citation is `path:line`, relative to the pygeoapi repo root. Where the docs and the code disagree, the code wins, and the disagreement is written down.

**Labels.**
- **(read)** means I got the fact by reading the source.
- **(executed)** means I confirmed it by running code in the project venv (`.venv/bin/python -I`, Python 3.12.14) or in a scratch venv. Section 6 records the runs.

**The `Req?` column in the key tables:**
- **yes** — the provider or the API fails without the key. The failure is usually a `KeyError`, an `AttributeError`, or a `RuntimeError` from `BaseProvider`.
- **cond.** — the key is required only on some code paths. The notes say which ones.
- **no** — the key is optional.
- **ignored** — the key is read or documented, but has no effect.

---

## 0. Common plumbing

### 0.1 Registry and loading
- The registry names are the keys of `PLUGINS['provider']`:

  | Registry name | Class | Source |
  |---|---|---|
  | `AzureBlobStorage` | `pygeoapi.provider.azure_.AzureBlobStorageProvider` | `pygeoapi/plugin.py:41` |
  | `CSWFacade` | `pygeoapi.provider.csw_facade.CSWFacadeProvider` | `pygeoapi/plugin.py:43` |
  | `ElasticsearchCatalogue` | `pygeoapi.provider.elasticsearch_.ElasticsearchCatalogueProvider` | `pygeoapi/plugin.py:45` |
  | `FileSystem` | `pygeoapi.provider.filesystem.FileSystemProvider` | `pygeoapi/plugin.py:48` |
  | `Hateoas` | `pygeoapi.provider.hateoas.HateoasProvider` | `pygeoapi/plugin.py:50` |
  | `MapScript` | `pygeoapi.provider.mapscript_.MapScriptProvider` | `pygeoapi/plugin.py:51` |
  | `MVT-tippecanoe` | `pygeoapi.provider.mvt_tippecanoe.MVTTippecanoeProvider` | `pygeoapi/plugin.py:53` |
  | `MVT-elastic` | `pygeoapi.provider.mvt_elastic.MVTElasticProvider` | `pygeoapi/plugin.py:54` |
  | `MVT-proxy` | `pygeoapi.provider.mvt_proxy.MVTProxyProvider` | `pygeoapi/plugin.py:55` |
  | `MVT-postgresql` | `pygeoapi.provider.mvt_postgresql.MVTPostgreSQLProvider` | `pygeoapi/plugin.py:57` |
  | `TinyDBCatalogue` | `pygeoapi.provider.tinydb_.TinyDBCatalogueProvider` | `pygeoapi/plugin.py:69` |
  | `WMSFacade` | `pygeoapi.provider.wms_facade.WMSFacadeProvider` | `pygeoapi/plugin.py:70` |
  | `WMTSFacade` | `pygeoapi.provider.wmts_facade.WMTSFacadeProvider` | `pygeoapi/plugin.py:71` |

- `load_plugin('provider', provider_def)` reads `provider_def['name']` (`pygeoapi/plugin.py:107`). A name that contains `.` is imported as a dotted class path (`pygeoapi/plugin.py:123-124`). The class is then called with the **whole provider dict** (`pygeoapi/plugin.py:133`). (read)
- The API calls `load_plugin` again **on every request**, so providers have no state between requests. Some examples:
  - `pygeoapi/api/tiles.py:97`
  - `pygeoapi/api/tiles.py:238`
  - `pygeoapi/api/maps.py:92`
  - `pygeoapi/api/stac.py:163`
  - `pygeoapi/api/collection.py:333` — the tile provider is loaded on every `/collections/{id}` request.

  So any work a constructor does happens once per HTTP request. For example, MVT-tippecanoe in URL mode sends an HTTP HEAD (`pygeoapi/provider/mvt_tippecanoe.py:94`), and the ES providers ping the cluster (`pygeoapi/provider/elasticsearch_.py:77`). (read)
- The API picks a provider by type with `get_provider_by_type(providers, type)`. It returns the **first** provider of that type and raises `ProviderTypeError` if there is none (`pygeoapi/provider/__init__.py:63-66`). `filter_providers_by_type` returns the **last** one instead, because it builds a dict (`pygeoapi/provider/__init__.py:47`). `get_provider_default` returns the provider with `default: true`, or the first provider (`pygeoapi/provider/__init__.py:81-86`). (read)

### 0.2 Two base classes that read keys differently
- **`BaseProvider.__init__`** (`pygeoapi/provider/base.py:51-92`). The map, record, STAC and MVT-postgresql providers use it.
  - `name`, `type` and `data` are required. If one is missing it raises `RuntimeError('name/type/data are required')` (`pygeoapi/provider/base.py:63-67`).
  - It then reads these optional keys:
    - `editable` (`:69`)
    - `count` (`:70`)
    - `options` (`:71`)
    - `id_field` (`:72`)
    - `uri_field` (`:73`)
    - `x_field`, `y_field`, `z_field` (`:74-76`)
    - `time_field` (`:77`)
    - `title_field` (`:78`)
    - `properties` (`:79`)
    - `file_types` (`:80`)
    - `include_extra_query_parameters` (`:81`)
    - `storage_crs` (`:86`)

  (read)
- **`BaseTileProvider.__init__`** (`pygeoapi/provider/tile.py:47-62`). It does **not** inherit from `BaseProvider`. MVT-tippecanoe, MVT-proxy, MVT-elastic and WMTSFacade use only this class, so none of the generic keys above are read for them.
  - It reads `name` (`pygeoapi/provider/tile.py:56`) and `data` (`:57`).
  - `format.name` (`:58`) and `format.mimetype` (`:59`) are required and raise `KeyError` if missing.
  - `options` (`:60`) is also **effectively required**, because the next line calls `self.options.get('schemes', [])` (`:61`). (read)
- MVT-postgresql inherits from both (`pygeoapi/provider/mvt_postgresql.py:58`). It calls `PostgreSQLProvider.__init__` first and then `BaseMVTProvider.__init__` (`:73-74`). (read)

### 0.3 Config JSON Schema constraints (the `pygeoapi config validate` schema)
- Resource `type` must be `collection` or `stac-collection` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:381-383`).
- Provider `type` must be one of `feature`, `coverage`, `record`, `map`, `tile`, `edr`, `stac` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:522-529`).
- Every provider requires `type`, `name` and `data` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:640-643`). `data` may be a string or an object (`:540-544`).
- If `format` is present, it requires both `name` and `mimetype` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:578-590`).
- `options` is validated against the **PostgreSQL options** schema (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:591-595`). That schema:
  - types `connect_timeout`, keepalives and pool keys (`:719-751`);
  - has no `additionalProperties: false`, so keys such as `zoom`, `schemes`, `layer` and `wmts_layer` pass unchecked (`:723-751`).
- A resource requires `type`, `title`, `description`, `keywords`, `extents` and `providers` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:663-669`). This includes `stac-collection` resources.

### 0.4 Error classes (HTTP mapping)
- `ProviderGenericError` (`pygeoapi/provider/base.py:322`)
- `ProviderConnectionError` (`:327`)
- `ProviderTypeError` — 400 (`:332-335`)
- `ProviderInvalidQueryError` — 400 `InvalidQuery` (`:338-342`)
- `ProviderQueryError` (`:345`)
- `ProviderItemNotFoundError` — 404 (`:350-354`)
- `ProviderNoDataError` — 204 (`:357-361`)
- `ProviderNotFoundError` (`:364`)
- `ProviderInvalidDataError` (`:374`)
- Tile-specific:
  - `ProviderTileQueryError` (`pygeoapi/provider/tile.py:178`)
  - `ProviderTileNotFoundError` — a subclass of `ProviderItemNotFoundError` (`pygeoapi/provider/tile.py:183`)
  - `ProviderTilesetIdNotFoundError` (`pygeoapi/provider/tile.py:188`)

---

## 1. Tile providers

### `MVT-tippecanoe` — `mvt_tippecanoe.MVTTippecanoeProvider`
- **Provider type(s):** `tile`. The class is `MVTTippecanoeProvider(BaseMVTProvider)` (`pygeoapi/provider/mvt_tippecanoe.py:55`). `BaseMVTProvider(BaseTileProvider)` (`pygeoapi/provider/base_mvt.py:43`) sets `tile_type = 'vector'` (`pygeoapi/provider/base_mvt.py:57`). (read)
- **Dependencies:** only `requests` (`pygeoapi/provider/mvt_tippecanoe.py:36`), which is a core requirement (`requirements.txt:15`). No extra pip package is needed. (read)
- **`data` forms:**
  1. **Local directory** of pre-rendered tiles laid out as `{z}/{x}/{y}.{format.name}`, with an optional `metadata.json` at its root. The directory must exist (`pygeoapi/provider/mvt_tippecanoe.py:103-107`). The layer name is the directory basename (`:141`). `metadata.json` is optional: if it is missing, the provider only logs (`:109-113`).
  2. **URL template** that contains `/{z}/{x}/{y}`, for example `http://host/ne_110m_lakes/{z}/{x}/{y}.pbf`.
     - The literal `/{z}/{x}/{y}` must be present, or the provider raises `ProviderConnectionError` (`:129-132`).
     - The layer is the path before `/{z}/{x}/{y}`, without the leading `/` (`:134-138`).
     - Tiles are fetched from `{scheme}://{netloc}/{layer}/{z}/{x}/{y}{ext}`. `ext` is the suffix of the URL path, such as `.pbf`, not `format.name` (`:185-190`).
     - Any query string on the template is **dropped** (`:182-190`).

  (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `MVT-tippecanoe` | `pygeoapi/provider/tile.py:56`, `pygeoapi/plugin.py:53` |
  | `type` | string | yes | — | Must be `tile`. The constructor does not read it; the API selects the provider with it. | `pygeoapi/api/tiles.py:95-96` |
  | `data` | string (dir path \| URL template) | yes | — | See `data` forms above. | `pygeoapi/provider/tile.py:57`, `pygeoapi/provider/mvt_tippecanoe.py:76` |
  | `format.name` | string | yes | — | Disk-tile file extension (`{z}/{x}/{y}.{format.name}`). Also the `f=` value in tile links. The API always passes it as `format_`. `mvt` is mapped to `format.name`. | `pygeoapi/provider/tile.py:58`, `pygeoapi/provider/mvt_tippecanoe.py:221`, `pygeoapi/provider/mvt_tippecanoe.py:244-245`, `pygeoapi/api/tiles.py:240` |
  | `format.mimetype` | string | yes | — | Tile response `Content-Type`. A value starting with `image` gives dataType `map`; anything else gives `vector`. | `pygeoapi/provider/tile.py:59`, `pygeoapi/api/tiles.py:144`, `pygeoapi/api/tiles.py:241` |
  | `options` | object | yes | — | Omitting it raises `AttributeError` on `None.get`. | `pygeoapi/provider/tile.py:60-61` |
  | `options.zoom.min` | integer | cond. | — | Checked by `is_in_limits`, which runs only on a missing tile. Also required by the HTML `/tiles` page. | `pygeoapi/provider/tile.py:151`, `pygeoapi/api/tiles.py:185` |
  | `options.zoom.max` | integer | cond. | — | Same as `options.zoom.min`. | `pygeoapi/provider/tile.py:151`, `pygeoapi/api/tiles.py:186` |
  | `options.schemes` | list[string] | ignored | `[]` | Read by the base class, but `get_tiling_schemes` is overridden to always return WebMercatorQuad. | `pygeoapi/provider/tile.py:61`, `pygeoapi/provider/mvt_tippecanoe.py:143-146` |
  | `options.bounds` | list | ignored | — | Appears only in the test configs. No code reads it. | `tests/pygeoapi-test-config.yml:257` |
  | `default` | boolean | no | first provider | API-level: makes this the default provider. | `pygeoapi/provider/__init__.py:81` |

- **Capabilities:**
  - Tiling schemes: `WebMercatorQuad` only (`pygeoapi/provider/mvt_tippecanoe.py:143-146`).
  - Metadata formats `json`, `jsonld`, `tilejson` and `html`, dispatched in `pygeoapi/provider/base_mvt.py:205-222`.
  - The `tilejson` and `html` formats embed `metadata.json`. Its `json` member must be a JSON string that contains `vector_layers` (`pygeoapi/provider/mvt_tippecanoe.py:304-307`, `:395-398`).
  - Pre-rendered tiles only. Features are not rendered on the fly (`docs/source/publishing/ogcapi-tiles.rst:21`).

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-tiles.rst:43-55` uses these keys:
  - `type: tile` (`:44`)
  - `name` (`:45`)
  - `data: tests/data/tiles/ne_110m_lakes` (`:46`); a MinIO URL alternative is commented out (`:47`)
  - `options.zoom.min: 0` and `options.zoom.max: 5` (`:48-51`)
  - `format.name: pbf` and `format.mimetype: application/vnd.mapbox-vector-tile` (`:53-55`)
- **Tests and fixtures:**
  - Fixture directory `tests/data/tiles/ne_110m_lakes/`: 78 files, z0–z5, plus `metadata.json` generated by tippecanoe v1.36.0.
  - Config block `tests/pygeoapi-test-config.yml:252-265`, collection `naturalearth/lakes`, which also has a GeoJSON feature provider. The same block appears in `tests/pygeoapi-test-config-apirules.yml:216-229` and `tests/pygeoapi-test-config-pubsub.yml:257`.
  - API tests in `tests/api/test_tiles.py`:
    - `:70-85` covers tile data:
      - `z0/x0/y0` → 200 (`:74`)
      - `z5/x15/y16` → 204 (in limits but no file) (`:79`)
      - `z0/x1/y1` → 404 (outside the matrix) (`:84`)
    - `:116-122` covers metadata as `json` and `tilejson`.
  - There is no provider-level unit test.
- **Errors raised:**
  - `ProviderConnectionError` when:
    - the directory is missing (`pygeoapi/provider/mvt_tippecanoe.py:104-107`);
    - the URL template is malformed (`:129-132`);
    - `data` is neither a directory nor a URL (`:251-254`);
    - the tileset is not WebMercatorQuad (`:352-355`);
    - `tilejson` is requested without metadata (`:400-403`).
  - `ProviderTileNotFoundError` when the tile is out of limits (`:192-195`, `:224-227`).
  - `ProviderInvalidQueryError` or `ProviderGenericError` on HTTP errors in URL mode (`:199-203`).
  - Returns `None` when a tile is in limits but missing, which the API maps to **204** (`pygeoapi/api/tiles.py:246-249`).

  (read)
- **Gotchas and conflicts:**
  - **`get_tiles_service` changes the instance.** It overwrites `self._service_url` with the servicepath string (`pygeoapi/provider/mvt_tippecanoe.py:164`). `get_tms_links` also overwrites `self._service_metadata_url` with a URL (`pygeoapi/provider/base_mvt.py:233`). After that, `get_tiles` on the *same instance* in disk mode fails, because `isinstance(self.service_url, Path)` is no longer true (`pygeoapi/provider/mvt_tippecanoe.py:247-254`). `tilejson` metadata then tries an HTTP GET. The server never notices, because it creates a fresh instance per request. A probe must use a fresh instance for each call.
  - **URL mode issues:**
    - The metadata URL is built as `f'{baseurl}/{layer}/metadata.json'`, where `layer` already starts with `/`, so the URL has a double slash (`pygeoapi/provider/mvt_tippecanoe.py:79`, `:93`).
    - The constructor sends a HEAD request, and a non-200 answer is only logged (`:94-98`).
  - **Wrong tileset in docs.** The docs example `.../lakes/tiles/WorldCRS84Quad/metadata` (`docs/source/publishing/ogcapi-tiles.rst:232`) fails for this provider, which is WebMercatorQuad-only. `get_default_metadata` raises `ProviderConnectionError`, and `get_collection_tiles_metadata` does not wrap `get_metadata` in a try block (`pygeoapi/api/tiles.py:318-327`).
  - **Test config lists a scheme that is ignored.** The test config lists `schemes: [WorldCRS84Quad]` (`tests/pygeoapi-test-config.yml:261-262`), but the provider advertises only WebMercatorQuad.
  - **Wrong path in docs.** The `mbtiles`-style `{z}/{x}/{y}` wording in the docs matches the code, but the docs comment "MVT-tippecanoe always uses WebMercatorQuad" sits misindented under `options` (`docs/source/publishing/ogcapi-tiles.rst:52`).

### `MVT-proxy` — `mvt_proxy.MVTProxyProvider`
- **Provider type(s):** `tile`. The class is `MVTProxyProvider(BaseMVTProvider)` (`pygeoapi/provider/mvt_proxy.py:48`) and has `tile_type` `vector`. (read)
- **Dependencies:** `requests` only (`pygeoapi/provider/mvt_proxy.py:33`). (read)
- **`data` forms:** An HTTP(S) URL template with `/{z}/{x}/{y}`. A non-URL raises `ProviderConnectionError` (`pygeoapi/provider/mvt_proxy.py:66-69`), and so does a URL without the template (`:113-116`).
  - If the path contains a `.` (for example `{y}.mvt`), the upstream URL is `{base}/{layer}/{z}/{x}/{y}.{format_}`. Here `format_` is `format.name`, not the template's own extension.
  - Otherwise the upstream URL is `{base}/{layer}/{z}/{x}/{y}` (`:174-177`).
  - The template's query string is kept (`:164-167`).

  (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `MVT-proxy` | `pygeoapi/provider/tile.py:56` |
  | `type` | string | yes | — | `tile` | `pygeoapi/api/tiles.py:95-96` |
  | `data` | URL template | yes | — | See `data` forms above. | `pygeoapi/provider/mvt_proxy.py:66`, `pygeoapi/provider/mvt_proxy.py:113` |
  | `format.name` | string | yes | — | Appended as the extension when the template path contains `.`. Watch out: `pbf` + `{y}.mvt` requests `.pbf`. | `pygeoapi/provider/tile.py:58`, `pygeoapi/provider/mvt_proxy.py:175` |
  | `format.mimetype` | string | yes | — | Response `Content-Type`. | `pygeoapi/provider/tile.py:59` |
  | `options` | object | yes | — | Omitting it raises `AttributeError`. | `pygeoapi/provider/tile.py:60-61` |
  | `options.zoom.min` | integer | cond. | — | Required when upstream returns 404 or 500 (`is_in_limits`), and for the HTML `/tiles` page. | `pygeoapi/provider/tile.py:151`, `pygeoapi/provider/mvt_proxy.py:179-180` |
  | `options.zoom.max` | integer | cond. | — | Same as `options.zoom.min`. | `pygeoapi/provider/tile.py:151` |
  | `options.schemes` | list[string] (TMS ids) | no | `[WorldCRS84Quad, WebMercatorQuad]` | **The only provider that honours it.** It filters the TMS enum and can therefore use custom TMS JSON files. | `pygeoapi/provider/base_mvt.py:73-85` |

- **Capabilities:**
  - Tiling schemes come from `options.schemes` or default to both schemes. `get_tiling_schemes` is not overridden (`pygeoapi/provider/base_mvt.py:73-87`).
  - Metadata formats are JSON, JSON-LD and HTML. `tilejson` raises `NotImplementedError`, because `get_vendor_metadata` is inherited unimplemented (`pygeoapi/provider/base_mvt.py:166-184`).

  (read)
- **Docs examples:**
  - pg_tileserv (`docs/source/publishing/ogcapi-tiles.rst:102-114`): `data` = `http://localhost:7800/public.ne_50m_admin_0_countries/{z}/{x}/{y}.mvt` (`:105`), `options.zoom` 0–15 (`:106-109`), `options.schemes: [WebMercatorQuad]` (`:110-111`), `format` pbf.
  - martin (`docs/source/publishing/ogcapi-tiles.rst:120-132`): `data: http://localhost:3000/ne_50m_admin_0_countries/{z}/{x}/{y}` (`:123`).
- **Tests and fixtures:** none (no test or config references `MVT-proxy`).
- **Errors raised:**
  - `ProviderConnectionError` for a bad `data` value (`pygeoapi/provider/mvt_proxy.py:66-69`).
  - `ProviderTileNotFoundError` for an upstream 404/500 outside limits (`:179-182`).
  - `ProviderInvalidQueryError` or `ProviderGenericError` (`:186-190`).
  - `UnboundLocalError` when the tileset matches no scheme: `tiling_scheme` is never initialised before the check at `:247` (`pygeoapi/provider/mvt_proxy.py:228-247`).

  (read)
- **Gotchas and conflicts:**
  - The docs say `schemes` "is needed" (`docs/source/publishing/ogcapi-tiles.rst:111`). In the code it is optional and defaults to both schemes.
  - `get_tilematrixset()` returns `None` for an unknown id (`pygeoapi/provider/tile.py:170-175`). `is_in_limits(None, …)` then raises `AttributeError`.
  - The docs matrix marks everything ❓ for MVT-proxy (`docs/source/publishing/ogcapi-tiles.rst:23`).

### `MVT-postgresql` — `mvt_postgresql.MVTPostgreSQLProvider`
- **Provider type(s):** `tile`. The class is `MVTPostgreSQLProvider(BaseMVTProvider, PostgreSQLProvider)` (`pygeoapi/provider/mvt_postgresql.py:58`). (read)
- **Dependencies:**
  - `geoalchemy2` (`pygeoapi/provider/mvt_postgresql.py:40-41`) and `sqlalchemy` (`:43-44`), which come in through `sql.py` (`pygeoapi/provider/sql.py:20-23`).
  - psycopg2, via the driver `postgresql+psycopg2` (`pygeoapi/provider/sql.py:810`).
  - Pip names: `geoalchemy2` (`requirements-provider.txt:8`), `psycopg2` (`requirements-provider.txt:16`), `SQLAlchemy` (`requirements.txt:17`), `pygeofilter[backend-sqlalchemy]` (`requirements-provider.txt:18`).
  - The docs say sqlalchemy, geoalchemy2 and psycopg2-binary (`docs/source/publishing/ogcapi-tiles.rst:138`), and PostGIS with protobuf-c (`:141`). (read)
- **`data` forms:** a dict of connection parameters, or a connection-URL string (`pygeoapi/provider/sql.py:597-601`). The table is reflected from the database **in the constructor** (`pygeoapi/provider/sql.py:139-141`), so the database must be reachable at load time. (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `MVT-postgresql` | `pygeoapi/provider/base.py:63` |
  | `type` | string | yes | — | `tile` | `pygeoapi/provider/base.py:64` |
  | `data` | object \| string (SQLAlchemy URL) | yes | — | A string is used as the connection URL as-is. | `pygeoapi/provider/sql.py:597-598` |
  | `data.host` | string | no | `None` | | `pygeoapi/provider/sql.py:604` |
  | `data.port` | int/string | no | `5432` | | `pygeoapi/provider/sql.py:605`, `pygeoapi/provider/sql.py:797` |
  | `data.dbname` / `data.database` | string | no | `None` | Either spelling works. | `pygeoapi/provider/sql.py:606-608` |
  | `data.user` | string | no | `None` | | `pygeoapi/provider/sql.py:603` |
  | `data.password` | string | no | `None` | | `pygeoapi/provider/sql.py:610` |
  | `data.search_path` | list[string] | no | `['public']` | Also accepted as `options.search_path`. | `pygeoapi/provider/sql.py:614-617` |
  | `data.query` | string | ignored | — | Stored as `db_query`; nothing reads it. | `pygeoapi/provider/sql.py:609` |
  | `table` | string | yes | — | Also used as the MVT **layer name**. The API passes `get_layer()` = `table`. | `pygeoapi/provider/sql.py:115`, `pygeoapi/provider/mvt_postgresql.py:96-102` |
  | `id_field` | string | yes | — | Emitted as the column `id` in the tile. | `pygeoapi/provider/sql.py:116`, `pygeoapi/provider/mvt_postgresql.py:88-91` |
  | `geom_field` | string | no | `geom` | Geometry column. | `pygeoapi/provider/sql.py:117` |
  | `storage_crs` | URI | no | CRS84 | Used to transform the tile envelope into the storage CRS. The docs stress that it must be correct. | `pygeoapi/provider/base.py:86`, `pygeoapi/provider/mvt_postgresql.py:162`, `docs/source/publishing/ogcapi-tiles.rst:173` |
  | `options` | object | yes | — | **Shared:** DB connect args + pool keys + `zoom` (+ `schemes`). `BaseTileProvider` requires it. | `pygeoapi/provider/sql.py:127`, `pygeoapi/provider/tile.py:60-61` |
  | `options.zoom.min` / `.max` | integer | yes | — | Checked on **every** tile request, because `is_in_limits` is always called. | `pygeoapi/provider/mvt_postgresql.py:155`, `pygeoapi/provider/tile.py:151` |
  | `options.<pool key>` | int/bool | no | `pool_size` 5, `max_overflow` 10, `pool_recycle` -1, `pool_timeout` 30, `pool_pre_ping` True | SQLAlchemy pool settings. | `pygeoapi/provider/sql.py:635-641` |
  | `options.<other scalar>` | scalar | no | — | Passed to psycopg2 as `connect_args`, for example `connect_timeout`. Values that are dicts, such as `zoom`, are filtered out. | `pygeoapi/provider/sql.py:623-627`, `pygeoapi/provider/sql.py:689-698` |
  | `options.schemes` | list | ignored / harmful | — | Ignored, because `get_tiling_schemes` is overridden. Also not a dict, so it is forwarded as a connect arg (see Gotchas). | `pygeoapi/provider/mvt_postgresql.py:104-113`, `pygeoapi/provider/sql.py:626` |
  | `format.name` / `format.mimetype` | string | yes | — | | `pygeoapi/provider/tile.py:58-59` |
  | `properties` | list | ignored | — | The MVT `get_fields` includes every non-geometry column. | `pygeoapi/provider/mvt_postgresql.py:82-92` |

- **Capabilities:**
  - `WebMercatorQuad` and `WorldCRS84Quad` (`pygeoapi/provider/mvt_postgresql.py:104-113`).
  - Rendered on the fly with `ST_AsMVT` / `ST_AsMVTGeom` (`:166-181`).
  - The WorldCRS84Quad envelope is computed by hand (`:289-300`). WebMercatorQuad uses `ST_TileEnvelope` (`:303`).
  - Metadata formats are JSON and HTML. TILEJSON is not implemented (base class).

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-tiles.rst:150-170` uses these keys:
  - `data.host`, `data.port`, `data.dbname`, `data.user`, `data.password`, `data.search_path` (`:153-159`)
  - `id_field` (`:160`), `table` (`:161`), `geom_field` (`:162`), `storage_crs` (`:163`)
  - `options.zoom` (`:164-167`), `format` (`:168-170`)
  - Connection options may also go into `options` (`:175`).
- **Tests and fixtures:** `tests/provider/test_postgresql_mvt_provider.py:45-71` is the config fixture. It needs a live PostGIS database loaded from `tests/data/hotosm_bdi_waterways.sql.gz`, which CI does in `.github/workflows/main.yml:141`. The tests cover:
  - `is_in_limits` (`:105-117`)
  - both schemes (`:120-125`)
  - tiles (`:240-278`): an empty tile returns `None` (`:245-250`); an out-of-matrix tile raises `ProviderTileNotFoundError` (`:273-278`)

  There is no test-config YAML block.
- **Errors raised:**
  - `ProviderTileNotFoundError` for a tile outside the zoom or matrix limits (`pygeoapi/provider/mvt_postgresql.py:155-157`).
  - `ValueError` from the list unpacking when the tileset id is unknown (`:151-154`).
  - `ProviderConnectionError` from `get_default_metadata` (`:243-246`). `tiling_scheme` is not initialised first, so an unknown tileset raises `UnboundLocalError` instead.
  - SQLAlchemy errors, which are not wrapped. (read)
- **Gotchas and conflicts:**
  - **`options.schemes` breaks construction.** Every non-dict `options` value is forwarded to `get_engine(**connect_args)`, and `get_engine` is `functools.cache`d (`pygeoapi/provider/sql.py:644`). A list value such as `schemes` is unhashable, so the call fails with `TypeError`. Verification: see §6.
  - The `get_tiles` default is `layer='default'` (`pygeoapi/provider/mvt_postgresql.py:135`), but the API always passes the table name (`pygeoapi/api/tiles.py:244`).
  - An empty tile returns `None`, which the API maps to 204 (`pygeoapi/provider/mvt_postgresql.py:184-186`).

### `MVT-elastic` — `mvt_elastic.MVTElasticProvider`
- **Provider type(s):** `tile`. The class is `MVTElasticProvider(BaseMVTProvider)` (`pygeoapi/provider/mvt_elastic.py:48`). (read)
- **Dependencies:** `requests` only (`pygeoapi/provider/mvt_elastic.py:33`). It does **not** use the elasticsearch client. (read)
- **`data` forms:** An ES vector-tile API URL template, for example `http://localhost:9200/<index>/_mvt/<geom-field>/{z}/{x}/{y}?grid_precision=0` (`docs/source/publishing/ogcapi-tiles.rst:75`).
  - It must be a URL (`pygeoapi/provider/mvt_elastic.py:67-70`) and contain `/{z}/{x}/{y}` (`:114-117`).
  - The layer is the path before the template, i.e. `<index>/_mvt/<field>` (`:119-123`).
  - Tiles are requested with a GET that carries the JSON body `{'fields': ['*']}` (`:179-181`).
  - The query string is kept (`:172-175`).

  (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `MVT-elastic` | `pygeoapi/provider/tile.py:56` |
  | `type` | string | yes | — | `tile` | `pygeoapi/api/tiles.py:95-96` |
  | `data` | URL template | yes | — | See `data` forms above. | `pygeoapi/provider/mvt_elastic.py:67`, `pygeoapi/provider/mvt_elastic.py:114` |
  | `format.name` | string | yes | — | Used only in links. The upstream URL ignores it. | `pygeoapi/provider/tile.py:58`, `pygeoapi/provider/mvt_elastic.py:181` |
  | `format.mimetype` | string | yes | — | | `pygeoapi/provider/tile.py:59` |
  | `options` | object | yes | — | | `pygeoapi/provider/tile.py:60-61` |
  | `options.zoom.min` / `.max` | integer | cond. | — | Required on an upstream 404 and for the HTML page. | `pygeoapi/provider/mvt_elastic.py:183-186`, `pygeoapi/provider/tile.py:151` |
  | `options.schemes` | list | ignored | — | WebMercatorQuad only. | `pygeoapi/provider/mvt_elastic.py:125-130` |

- **Capabilities:** WebMercatorQuad only (`pygeoapi/provider/mvt_elastic.py:125-130`). Metadata formats are JSON and HTML. TileJSON is not implemented, and the class docstring says ES gives no tileset metadata (`:53`). (read)
- **Docs examples:** `docs/source/publishing/ogcapi-tiles.rst:72-85` uses `data` (`:75`), `options.zoom` 0–5 (`:78-81`) and `format` pbf (`:83-85`).
- **Tests and fixtures:** none.
- **Errors raised:**
  - `ProviderConnectionError` for a bad `data` value (`pygeoapi/provider/mvt_elastic.py:67-70`).
  - `ProviderTileNotFoundError` (`:183-186`).
  - `ProviderInvalidQueryError` or `ProviderGenericError` (`:190-194`). The `except` branch reads `resp.status_code`, and `resp` is unbound if the connection itself fails. That gives an `UnboundLocalError`.
  - `UnboundLocalError` when metadata is requested for an unknown tileset (`:232-251`).

  (read)

### `WMTSFacade` — `wmts_facade.WMTSFacadeProvider`
- **Provider type(s):** `tile`. The class is `WMTSFacadeProvider(BaseTileProvider)`, a raster provider with `tile_type = 'raster'` (`pygeoapi/provider/wmts_facade.py:50`, `:67`). (read)
- **Dependencies:** `requests` only (`pygeoapi/provider/wmts_facade.py:35`). It does not use owslib. (read)
- **`data` forms:** A WMTS KVP endpoint URL (`docs/source/publishing/ogcapi-tiles.rst:192`). A `?` already in the URL is handled (`pygeoapi/provider/wmts_facade.py:167-170`). A non-URL raises `ProviderConnectionError` at tile time (`:195-198`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `WMTSFacade` | `pygeoapi/provider/tile.py:56` |
  | `type` | string | yes | — | `tile` | `pygeoapi/api/tiles.py:95-96` |
  | `data` | URL | yes | — | WMTS endpoint. | `pygeoapi/provider/wmts_facade.py:153` |
  | `format.name` | `png` \| `jpeg` | yes | — | Anything else raises `RuntimeError` in the constructor. | `pygeoapi/provider/wmts_facade.py:62-63` |
  | `format.mimetype` | string | yes | — | Sent as the WMTS `format` parameter. | `pygeoapi/provider/wmts_facade.py:158` |
  | `options` | object | yes | — | | `pygeoapi/provider/tile.py:60-61` |
  | `options.wmts_layer` | string | yes | — | WMTS `layer`. | `pygeoapi/provider/wmts_facade.py:159` |
  | `options.wmts_tile_matrix_set` | string | yes | — | WMTS `tileMatrixSet`, the upstream name. | `pygeoapi/provider/wmts_facade.py:160` |
  | `options.wmts_style` | string | no | `''` | WMTS `style`. | `pygeoapi/provider/wmts_facade.py:164` |
  | `options.scheme` (singular) | `WebMercatorQuad` \| `WorldCRS84Quad` | yes | — | The pygeoapi TMS this layer aligns to. Any other value gives an empty scheme list. | `pygeoapi/provider/wmts_facade.py:88` |
  | `options.zoom.min` / `.max` | integer | cond. | — | Required on an upstream 400 and for the HTML page. | `pygeoapi/provider/wmts_facade.py:178-184`, `pygeoapi/provider/tile.py:151` |
  | `options.schemes` | list | ignored | — | Read by the base class, but unused. | `pygeoapi/provider/tile.py:61` |

- **Capabilities:**
  - The one scheme named by `options.scheme` (`pygeoapi/provider/wmts_facade.py:80-91`).
  - GetTile requests with `tileMatrix=z`, `tileRow=y`, `tileCol=x` (`:161-163`).
  - Metadata formats are JSON and HTML only. TILEJSON raises `NotImplementedError` (`:219-228`), even though the API allows `f=tilejson` (`pygeoapi/api/tiles.py:283`).

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-tiles.rst:189-203` uses `data` (`:192`), `format` png (`:193-195`), `options.wmts_layer` (`:197`), `options.wmts_tile_matrix_set` (`:198`), `options.wmts_style` (`:199`), `options.scheme` (`:200`) and `options.zoom` 0–20 (`:201-203`).
- **Tests and fixtures:** none.
- **Errors raised:**
  - `RuntimeError` for a bad format (`pygeoapi/provider/wmts_facade.py:62-63`).
  - `ProviderTileNotFoundError` (`:184`).
  - `ProviderInvalidQueryError` or `ProviderGenericError` (`:189-193`), with the same unbound-`resp` risk as MVT-elastic.
  - `ProviderConnectionError` (`:195-198`).
  - `UnboundLocalError` or `IndexError` in `get_default_metadata` when `options.scheme` does not match (`:257-281`).

  (read)
- **Gotchas and conflicts:**
  - The requested `tileMatrixSetId` is ignored when building the upstream URL; `options.wmts_tile_matrix_set` is always sent (`pygeoapi/provider/wmts_facade.py:160`).
  - `get_layer()` is the base no-op that returns `None` (`pygeoapi/provider/tile.py:64-69`).
  - `options.scheme` versus `schemes`: WMTSFacade uses the singular, the MVT base uses the plural. A GUI should not merge the two.

---

## 2. Map providers

### `WMSFacade` — `wms_facade.WMSFacadeProvider`
- **Provider type(s):** `map`. The class is `WMSFacadeProvider(BaseProvider)` (`pygeoapi/provider/wms_facade.py:53`, `:65`). (read)
- **Dependencies:** `pyproj` (`pygeoapi/provider/wms_facade.py:34`, `:38`) and `requests` (`:39`), both core. No owslib. (read)
- **`data` forms:** A WMS base URL, which may already contain `?` (`pygeoapi/provider/wms_facade.py:124-127`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | string | yes | — | `WMSFacade` | `pygeoapi/provider/base.py:63` |
  | `type` | string | yes | — | `map` | `pygeoapi/provider/base.py:64`, `pygeoapi/api/maps.py:89-90` |
  | `data` | URL | yes | — | WMS endpoint. | `pygeoapi/provider/wms_facade.py:124` |
  | `options` | object | yes | — | Omitting it raises `AttributeError` at query time. | `pygeoapi/provider/wms_facade.py:92` |
  | `options.layer` | string | yes | — | WMS `layers`. Missing raises `KeyError` at query time. | `pygeoapi/provider/wms_facade.py:113` |
  | `options.style` | string | no | `default` | WMS `styles`. The `/styles/{styleId}/map` path value is **ignored**. | `pygeoapi/provider/wms_facade.py:114` |
  | `options.version` | string | no | `1.3.0` | Any other version sends `srs=` instead of `crs=`. 1.3.0 with EPSG:4326 swaps the bbox axes. | `pygeoapi/provider/wms_facade.py:92`, `pygeoapi/provider/wms_facade.py:99-105` |
  | `format.name` | string | yes | — | API: `f` must equal it, or be absent or `html`. Provider: only `png` is in `OUTPUT_FORMATS`. | `pygeoapi/api/maps.py:252-262`, `pygeoapi/provider/wms_facade.py:46-48` |
  | `format.mimetype` | string | yes | — | Response `Content-Type`. | `pygeoapi/api/maps.py:258` |
  | `storage_crs` | URI | no | CRS84 | API: the default output `crs` when the request has no `crs`. | `pygeoapi/api/maps.py:113-115` |
  | `time_field` | string | no | — | API: adds the `datetime` OpenAPI parameter only. The provider always forwards `datetime_` as WMS `TIME`. | `pygeoapi/api/maps.py:428-430`, `pygeoapi/provider/wms_facade.py:121-122` |

- **Capabilities:** GetMap with `bbox`, `width`, `height`, `crs`, `transparent` and `time` (`pygeoapi/provider/wms_facade.py:107-122`). PNG only (`:46-48`). (read)
- **Docs examples:** `docs/source/publishing/ogcapi-maps.rst:106-117` uses `data` (`:109`), `options.layer` (`:111`), `options.style` (`:112`), `options.version` (`:113`), `format` (`:114-116`) and `storage_crs` (`:117`).
- **Tests and fixtures:**
  - `tests/provider/test_wms_facade_provider.py:37-51` (config) and `:59-70` (CRS query against the live `demo.mapserver.org`). An invalid CRS URI is expected to raise `ProviderQueryError` (`:69-70`).
  - Config blocks: `tests/pygeoapi-test-config.yml:398-406`, `tests/pygeoapi-test-config-apirules.yml:328-336`, `pygeoapi-config.yml:200-208`.
  - API tests: `tests/api/test_maps.py:42-106`.
- **Errors raised:**
  - `ProviderQueryError` when the response body contains `ServiceException` (`pygeoapi/provider/wms_facade.py:133-136`).
  - `KeyError` for an unsupported `format_` (`:117`).

  (read)
- **Gotchas and conflicts:**
  - **`get_curie()` returns, not raises, `CRSError`** (`pygeoapi/crs.py:211-212`). The fallback `except CRSError: wms_crs = DEFAULT_WMS_CRS` (`pygeoapi/provider/wms_facade.py:94-97`) is therefore dead code. For an unsupported CRS, the error object's text is sent upstream, the WMS answers `ServiceException`, and the provider raises `ProviderQueryError`. That is why the test expects it (`tests/provider/test_wms_facade_provider.py:69-70`).
  - **The HTTP status is never checked.** Any body without `ServiceException`, such as an HTML 404 page, is returned as the "image" (`pygeoapi/provider/wms_facade.py:131-138`). A probe must check the PNG magic bytes itself.
  - **`transparent` arrives as a raw query string** (`pygeoapi/api/maps.py:135`). `transparent=false` is a truthy string, so the result is still `TRUE` (`pygeoapi/provider/wms_facade.py:103-104`).
  - The docs' vertical `subset` example (`docs/source/publishing/ogcapi-maps.rst:160-162`) is validated by the API, but the provider drops `subsets` through `**kwargs`.

### `MapScript` — `mapscript_.MapScriptProvider`
- **Provider type(s):** `map`. The class is `MapScriptProvider(BaseProvider)` (`pygeoapi/provider/mapscript_.py:54`, `:66`). (read)
- **Dependencies:**
  - `mapscript` (`pygeoapi/provider/mapscript_.py:34-35`). It is not in any requirements file. The Dockerfile installs the apt package `python3-mapscript` (`Dockerfile:82`).
  - `osgeo.osr`, i.e. the GDAL Python bindings (`pygeoapi/provider/mapscript_.py:33`). The pip name is `GDAL` (`requirements-provider.txt:7`).
  - Neither is installed in the project venv. (executed)
- **`data` forms:**
  - A `.shp` or `.tif` path, read with the built-in driver (`pygeoapi/provider/mapscript_.py:86-88`).
  - Any other value is an OGR connection string (`:90-92`).
  - With `options.tileindex`, a tile-index path (`:82-84`).

  (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `MapScript`, `map`, path | `pygeoapi/provider/base.py:63-65` |
  | `options` | object | yes | — | | `pygeoapi/provider/mapscript_.py:82` |
  | `options.type` | enum `MS_LAYER_POINT` \| `MS_LAYER_LINE` \| `MS_LAYER_POLYGON` \| `MS_LAYER_RASTER` | yes | — | Resolved with `getattr(mapscript, …)`. | `pygeoapi/provider/mapscript_.py:94`, `docs/source/publishing/ogcapi-maps.rst:39-44` |
  | `options.projection` | int (EPSG) | no | `4326` | Layer projection. | `pygeoapi/provider/mapscript_.py:96-99` |
  | `options.style` | path (`.sld`/`.xml` or `.inc`) | no | default black class (vector only) | SLD is applied with `applySLD`. `.inc` is a MapServer CLASS snippet. | `pygeoapi/provider/mapscript_.py:105-121` |
  | `options.layer` | string | cond. | — | Read **only** with an SLD style, as the NamedLayer to apply. | `pygeoapi/provider/mapscript_.py:109` |
  | `options.tileindex` | bool | no | `False` | Runs through `str2bool`. | `pygeoapi/provider/mapscript_.py:82` |
  | `time_field` | string | no | — | Adds the filter `time_field = "datetime_"`. | `pygeoapi/provider/mapscript_.py:175-181` |
  | `format.name` / `format.mimetype` | string | yes | — | The API compares `f` with `format.name`. The provider supports `png`, `png8` and `jpeg`. | `pygeoapi/provider/mapscript_.py:45-49`, `pygeoapi/api/maps.py:252` |
  | `storage_crs` | URI | no | CRS84 | API default output `crs`. | `pygeoapi/api/maps.py:113-115` |

- **Capabilities:** Renders `bbox`, `width`/`height`, `crs` (EPSG or CRS84), `transparent` and `datetime` (`pygeoapi/provider/mapscript_.py:130-204`). Raster layers get `SCALE=AUTO` (`:123-124`). (read)
- **Docs examples:**
  - `docs/source/publishing/ogcapi-maps.rst:53-64` uses `data` (`:56`), `options.type` (`:58`), `options.layer` (`:59`), `options.style` (`:60`), `format` (`:61-63`) and `storage_crs` (`:64`).
  - `options.projection: 32631` (`:73`).
  - A tile index with `options.tileindex: True` (`:83-94`).
- **Tests and fixtures:** none.
- **Errors raised:**
  - `ProviderConnectionError` on a `MapServerError` during setup (`pygeoapi/provider/mapscript_.py:126-128`).
  - `ProviderQueryError` for a bad projection (`:171-173`) or a draw failure (`:199-202`).
  - For a bad format, the code tries to raise `ProviderQueryError`, but the log line references the unbound `image_obj_format` first, so an `UnboundLocalError` is raised instead (`:148-152`).

  (read)
- **Gotchas and conflicts:**
  - The docs say the default projection is "WGS84 (4236)" (`docs/source/publishing/ogcapi-maps.rst:75`). The code uses 4326 (`pygeoapi/provider/mapscript_.py:99`).
  - The docs imply `options.layer` is always needed (`docs/source/publishing/ogcapi-maps.rst:59`). The code uses it only with SLD.
  - `get_curie` returns a `CRSError` object for non-URI CRS values. The later `curie.split` raises `AttributeError`, which is not caught (`pygeoapi/provider/mapscript_.py:158-165`).
  - `transparent=false` is ignored, as with WMSFacade.

---

## 3. Record providers

### `TinyDBCatalogue` — `tinydb_.TinyDBCatalogueProvider`
- **Provider type(s):** `record`. The class is `TinyDBCatalogueProvider(TinyDBProvider)` (`pygeoapi/provider/tinydb_.py:392`), and `TinyDBProvider(BaseProvider)` (`:51`). The provider test fixture uses `type: 'feature'` (`tests/provider/test_tinydb_catalogue_provider.py:87`), but the config and docs use `record`. (read)
- **Dependencies:** `tinydb` (`pygeoapi/provider/tinydb_.py:40`), `shapely` (`:39`) and `python-dateutil` (`:38`), all core (`requirements.txt:18`). (read)
- **`data` forms:**
  - A path to a TinyDB JSON file that already exists (`pygeoapi/provider/tinydb_.py:69-74`).
  - If the file is not writable, it is opened read-only (`:77-80`).
  - Records live in the `_default` table as GeoJSON-like dicts with a top-level `id`.
  - `tests/load_tinydb_records.py` builds such a file from ISO XML.

  (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `TinyDBCatalogue`, `record`, file path | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/tinydb_.py:69` |
  | `editable` | bool | no | `false` | Enables POST, PUT and DELETE through the API. | `pygeoapi/provider/base.py:69`, `pygeoapi/api/itemtypes.py:788-792` |
  | `time_field` | string | no | `None` | A datetime filter on `properties.<time_field>`. When unset, it falls back to the top-level `time` member. | `pygeoapi/provider/tinydb_.py:181-187` |
  | `id_field` | string | ignored by provider | — | `get()` always matches top-level `id`. Used only by the HTML templates. | `pygeoapi/provider/tinydb_.py:284`, `pygeoapi/api/itemtypes.py:676` |
  | `title_field` | string | no | — | HTML: shown as the record label. | `pygeoapi/api/itemtypes.py:679-683` |
  | `uri_field` | string | no | — | API: item self URI and JSON-LD id. | `pygeoapi/api/itemtypes.py:949` |
  | `count` | bool | no | `true` | Adds `numberMatched`. | `pygeoapi/provider/base.py:70`, `pygeoapi/provider/tinydb_.py:232-235` |
  | `properties` | list | no | — | API: limits advertised queryables. The provider ignores `select_properties`. | `pygeoapi/api/itemtypes.py:191-195` |
  | `include_extra_query_parameters` | bool | no | `false` | API: passes unknown query parameters to the provider as property filters. TinyDB then rejects them with `ProviderInvalidQueryError`. | `pygeoapi/api/itemtypes.py:431`, `pygeoapi/provider/tinydb_.py:205-208` |

- **Capabilities:**
  - `q` full-text search over `properties._metadata-anytext`. Comma means OR, space means AND, case-insensitive (`pygeoapi/provider/tinydb_.py:443-444`, `:468-476`).
  - `bbox` (`:174-177`)
  - `datetime` ranges (`:189-196`)
  - Property equality filters (`:202-210`)
  - Single-key sort (`:258-266`)
  - `resulttype=hits` (`:170-172`)
  - Domains (`:123-139`)
  - Create, update and delete (`:297-351`)
  - `q` appears in the fields, and therefore in the queryables (`:403-408`).

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-records.rst:71-77` uses `editable` (`:73`), `data` (`:75`), `id_field: identifier` (`:76`) and `time_field` (`:77`). The STAC API example is at `docs/source/publishing/stac.rst:429-434`.
- **Tests and fixtures:**
  - Fixtures: `tests/data/open.canada.ca/sample-records.tinydb` (10 records) and `tests/data/dutch-nationaalgeoregister/sample-records.tinydb`, used at `tests/provider/test_tinydb_catalogue_provider.py:40-41`.
  - Provider tests: `:107-238`.
  - Config blocks:
    - `tests/pygeoapi-test-config.yml:439-444`, which uses `id_field: externalId`, `time_field: created` and `title_field: title`.
    - `tests/pygeoapi-test-stac-api-config.yml:183-188`, under the `stac-collection` resource.
    - `pygeoapi-config.yml:297-302`.
  - API queryables tests: `tests/api/test_itemtypes.py:86-115`.
- **Errors raised:**
  - `ProviderConnectionError` when the file is missing (`pygeoapi/provider/tinydb_.py:69-74`).
  - `ProviderInvalidQueryError` for an unknown property filter (`:205-208`) or a query `SyntaxError` (`:227-230`).
  - `ProviderItemNotFoundError` (`:286-287`).

  (read)
- **Gotchas and conflicts:**
  - **Datetime instants crash.** The code is `getattr(Q, time_field2)`, where `time_field2` is already a `Query` object, so the call raises `TypeError` (`pygeoapi/provider/tinydb_.py:198-200`). Verification: see §6.
  - **The docs overstate TinyDB.** The docs matrix marks TinyDB CQL as ✅ (`docs/source/publishing/ogcapi-records.rst:22`), but `query()` has no `filterq` handling, so the value is silently dropped in `**kwargs` (`pygeoapi/provider/tinydb_.py:142-144`). `select_properties` and `skip_geometry` are accepted and ignored in the same way.
  - **Typo in the anytext key.** When `title` or `description` is missing, `_add_extra_fields` writes `_metadata_anytext` (underscore) instead of `_metadata-anytext` (hyphen) (`pygeoapi/provider/tinydb_.py:419`, `:413`).
  - **Records created without a top-level `id` cannot be found again.** An item whose identifier comes only from `properties.identifier` (`pygeoapi/provider/base.py:270`) is stored without a top-level `id`, so `get()` can never find it (`pygeoapi/provider/tinydb_.py:308-311`, `:284`).

### `ElasticsearchCatalogue` — `elasticsearch_.ElasticsearchCatalogueProvider`
- **Provider type(s):** `record`. The class is `ElasticsearchCatalogueProvider(ElasticsearchProvider)` (`pygeoapi/provider/elasticsearch_.py:603`), and `ElasticsearchProvider(BaseProvider)` (`:52`). (read)
- **Dependencies:** `elasticsearch` (`pygeoapi/provider/elasticsearch_.py:38`), `elasticsearch_dsl` (`:41`) and `pygeofilter.backends.elasticsearch` (`:39`). The pip names are `elasticsearch` and `elasticsearch-dsl` (`requirements-provider.txt:4-5`). ES 8 or later is required (`pygeoapi/provider/elasticsearch_.py:83-87`). (read)
- **`data` forms:** `http(s)://host:port/<index>`. The value is split at the last `/` into host and index (`pygeoapi/provider/elasticsearch_.py:68`). The index may be a wildcard (`:108-114`) or an alias (`:115-118`). The index mapping must map `geometry` as `geo_shape` (`docs/source/publishing/ogcapi-records.rst:43`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `ElasticsearchCatalogue`, `record`, URL | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/elasticsearch_.py:68` |
  | `id_field` | string | no | falls back to `_source.id`, then `_id` | Takes the feature id from `properties.<id_field>`. | `pygeoapi/provider/elasticsearch_.py:542-545` |
  | `time_field` | string | cond. | — | Required whenever `datetime` is used; otherwise `ProviderQueryError`. Queried as `properties.<time_field>`. | `pygeoapi/provider/elasticsearch_.py:258-264` |
  | `properties` | list | no | — | Restricts `_source` includes and output properties. | `pygeoapi/provider/elasticsearch_.py:350-361` |
  | `editable` | bool | no | `false` | Create, update and delete through `es.index` / `es.delete`. | `pygeoapi/provider/elasticsearch_.py:472-524` |
  | `title_field` / `uri_field` | string | no | — | API and HTML only. | `pygeoapi/api/itemtypes.py:677-683` |

- **Capabilities:**
  - `q` as a `query_string` with comma-separated OR phrases (`pygeoapi/provider/elasticsearch_.py:334-348`)
  - `bbox` as a `geo_shape` envelope (`:241-256`)
  - `datetime` (`:258-290`)
  - Property filters (`:292-306`)
  - Sort (`:308-332`)
  - Scroll past 10000 hits (`:377-392`)
  - Domains (`:153-202`)
  - Transactions
  - `q` in the fields (`:620`)

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-records.rst:47-52` uses `data` (`:50`), `id_field` (`:51`) and `time_field` (`:52`). The docs claim CQL support (`:54`).
- **Tests and fixtures:** none for the Catalogue subclass. `tests/provider/test_elasticsearch__provider.py` tests only `Elasticsearch`, with a live ES loaded by `tests/load_es_data.py`.
- **Errors raised:**
  - `ProviderConnectionError` when the ping fails or ES is older than 8 (`pygeoapi/provider/elasticsearch_.py:77-87`).
  - `ProviderQueryError` for a missing index (`:90-94`), a missing `time_field` (`:260-262`), or a request error (`:403-408`).
  - `ProviderItemNotFoundError` (`:458-465`). `get()` returns `None` on other errors (`:466-468`), which the API turns into **400** (`pygeoapi/api/itemtypes.py:944-947`).

  (read)
- **Gotchas and conflicts:**
  - **CQL is silently ignored for the Catalogue.** Its `query()` does not pass `filterq` to `super()` (`pygeoapi/provider/elasticsearch_.py:624-636`), yet the docs table marks CQL ✅ (`docs/source/publishing/ogcapi-records.rst:21`).
  - **`_excludes()` has no effect.** It lists `properties._metadata-anytext` (`pygeoapi/provider/elasticsearch_.py:609-612`), but field names are relative to `properties`, as in `_metadata-anytext` (`:110`, `:138`). The exclusion therefore never matches.
  - Constructing the provider needs a live ES (ping plus index mapping).

### `CSWFacade` — `csw_facade.CSWFacadeProvider`
- **Provider type(s):** `record`. The class is `CSWFacadeProvider(BaseProvider)` (`pygeoapi/provider/csw_facade.py:47`). (read)
- **Dependencies:** `owslib` (`pygeoapi/provider/csw_facade.py:33-35`). The pip name is `OWSLib` (`docs/source/publishing/ogcapi-records.rst:83`). It is **not listed** in `requirements-provider.txt`, and it is not installed in the project venv. (read; executed)
- **`data` forms:** A CSW 2.0.2 endpoint URL. The connection is lazy: the constructor does not contact the server; the first query does (`pygeoapi/provider/csw_facade.py:252-264`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `CSWFacade`, `record`, CSW URL | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/csw_facade.py:260` |
  | `time_field` | string, one of `record_mappings` keys (`date`, `created`, `updated`, …) | cond. | — | Required for `datetime` queries. It is mapped through `record_mappings` to the CSW queryable, and a missing or unknown value raises `KeyError`. | `pygeoapi/provider/csw_facade.py:161`, `pygeoapi/provider/csw_facade.py:61-71` |
  | `id_field` | string | ignored by provider | — | Records use `record.identifier`. Used by HTML only. | `pygeoapi/provider/csw_facade.py:294` |
  | `title_field` | string | no | — | HTML label. | `pygeoapi/api/itemtypes.py:679-683` |

- **Capabilities:**
  - Fixed fields: `type`, `title`, `description`, `keywords`, `date`, `created`, `updated`, `rights`, `language` (`pygeoapi/provider/csw_facade.py:61-71`, `:76-93`).
  - `q` searches `csw:AnyText` with `PropertyIsLike` (`:183-188`).
  - `bbox`, with the axes swapped to yx (`:154-158`).
  - `datetime` (`:160-171`)
  - Property equality filters (`:173-181`)
  - Sort (`:190-203`)
  - Domains through GetDomain (`:95-122`)
  - No CQL and no transactions.
  - Each record carries a `time` value from `record.date` and a GetRecordById link (`:297-301`).

  (read)
- **Docs examples:** `docs/source/publishing/ogcapi-records.rst:90-96` uses `data` (`:93`), `id_field` (`:94`), `time_field: datetime` (`:95`) and `title_field` (`:96`).
- **Tests and fixtures:** `tests/provider/test_csw_provider.py:41-49` uses `time_field: 'date'`, with a mocked `CatalogueServiceWeb` (`:38`). A live variant is in `tests/provider/test_csw_provider_live.py`. No YAML config block.
- **Errors raised:**
  - `ProviderInvalidQueryError` for an unknown property (`pygeoapi/provider/csw_facade.py:175-178`).
  - `ProviderQueryError` on an `ExceptionReport` (`:214-217`).
  - `ProviderItemNotFoundError` (`:243-246`).
  - `ProviderConnectionError` (`:259-264`).

  (read)
- **Gotchas and conflicts:**
  - **The docs example breaks datetime queries.** The docs use `time_field: datetime` (`docs/source/publishing/ogcapi-records.rst:95`), but `datetime` is not a `record_mappings` key. Any `?datetime=` request with that config raises `KeyError` (`pygeoapi/provider/csw_facade.py:161`). The tests use `date` (`tests/provider/test_csw_provider.py:48`).
  - Unlike the other two catalogues, CSWFacade does not add `q` to its fields.

---

## 4. STAC providers

### `FileSystem` — `filesystem.FileSystemProvider`
- **Provider type(s):** `stac`, inside a `type: stac-collection` resource. The class is `FileSystemProvider(BaseProvider)` (`pygeoapi/provider/filesystem.py:44`). (read)
- **Dependencies:** none are required.
  - rasterio and fiona are used optionally to describe files, and each is skipped if missing (`pygeoapi/provider/filesystem.py:262-275`).
  - pygeometa is used optionally for MCF sidecars (`:245-254`).
  - Pip names: `rasterio` (core, `requirements.txt:14`), `fiona` (`requirements-provider.txt:6`), `pygeometa` (`requirements-provider.txt:20`).
  - The docs say rasterio and fiona are required (`docs/source/publishing/stac.rst:47-48`). (read)
- **`data` forms:** A directory path, which must exist (`pygeoapi/provider/filesystem.py:58-61`). The provider **concatenates** it with the request sub-path, `self.data + dirpath`, where `dirpath` starts with `/` (`:85`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `FileSystem`, `stac`, directory | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/filesystem.py:58` |
  | `file_types` | list[string] (with leading dot, e.g. `.gpkg`) | no (effectively yes) | `[]` | Decides three things: which files are listed as STAC Items, which request paths return the raw bytes (the path ends with a type), and which bare ids resolve to an Item. With an empty list, only directories are browsable. | `pygeoapi/provider/base.py:80`, `pygeoapi/provider/filesystem.py:130-141`, `pygeoapi/provider/filesystem.py:178` |

- **Capabilities:**
  - `get_data_path(baseurl, urlpath, dirpath)` returns one of:
    - a STAC `Catalog` dict for a directory, with child and item links (`pygeoapi/provider/filesystem.py:152-187`);
    - a STAC Item (`Feature` with `assets.default`) for a file (`:189-214`);
    - raw `bytes` for a raw-file path (`:148-150`).
  - A directory named `tiles` is always skipped (`:158-159`).
  - A `<basename>.yml` MCF sidecar is turned into STAC with pygeometa (`:241-256`).

  (read)
- **Docs examples:**
  - `docs/source/publishing/stac.rst:36-44`: resource `type: stac-collection` (`:37`), provider `type: stac` (`:40`), `name` (`:41`), `data` (`:42`), `file_types: [.grib2]` (`:43-44`).
  - Shapefiles need `.shp`, `.shx` and `.dbf` all listed in `file_types` (`:64-65`).
- **Tests and fixtures:**
  - `tests/provider/test_filesystem_provider.py:39-79`, using `file_types: ['.gpkg']` on `tests/data`:
    - the root has 14 links (`:58`);
    - `/poi_portugal` resolves to an Item (`:60-76`);
    - `..` raises an error (`:78-79`).
  - Config blocks: `tests/pygeoapi-test-stac-api-config.yml:123-150` and `pygeoapi-config.yml:237-264`.
  - The API functions `get_stac_root` and `get_stac_path` have no tests (`pygeoapi/api/stac.py:71`, `:135`).
- **Errors raised:**
  - `ProviderConnectionError` when the directory is missing (`pygeoapi/provider/filesystem.py:58-61`).
  - `ProviderInvalidQueryError` for `..` (`:80-83`).
  - `ProviderNotFoundError` (`:143-146`), which the API turns into 404 (`pygeoapi/api/stac.py:188-191`).
  - Any other exception becomes 500 "data query error" (`pygeoapi/api/stac.py:192-196`).

  (read)
- **Gotchas and conflicts:** `get_stac_root` loads every STAC provider and catches only `ProviderTypeError` (`pygeoapi/api/stac.py:103-108`). A single `FileSystem` resource with a missing directory therefore breaks the whole `/stac` root with a 500. (read)

### `AzureBlobStorage` — `azure_.AzureBlobStorageProvider`
- **Provider type(s):** `stac`. The class is `AzureBlobStorageProvider(BaseProvider)` (`pygeoapi/provider/azure_.py:43`). (read)
- **Dependencies:** `azure.storage.blob` (`pygeoapi/provider/azure_.py:34`). The pip name is `azure-storage-blob` (`requirements-provider.txt:2`); `azure-identity` is also listed (`requirements-provider.txt:1`) but not imported here. rasterio and fiona are optional (`pygeoapi/provider/azure_.py:256-272`). (read)
- **`data` forms:** An Azure **container name** (`pygeoapi/provider/azure_.py:64-65`). Credentials come only from the environment variable `AZURE_STORAGE_CONNECTION_STRING` (`:57-63`). (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `AzureBlobStorage`, `stac`, container name | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/azure_.py:64-65` |
  | `file_types` | list[string] | no (effectively yes) | `[]` | Same role as in FileSystem. | `pygeoapi/provider/azure_.py:143-157` |
  | env `AZURE_STORAGE_CONNECTION_STRING` | string (env var, not a config key) | yes | — | Checked in the constructor; missing raises `ProviderConnectionError`. | `pygeoapi/provider/azure_.py:57-60` |

- **Capabilities:** Same `get_data_path` contract as FileSystem: a Catalog listing through `walk_blobs`, an Item, or raw bytes (`pygeoapi/provider/azure_.py:67-234`). Items are described by reading the downloaded blob with rasterio's `MemoryFile` (`:274-278`). (read)
- **Docs examples:** `docs/source/publishing/stac.rst:90-98` uses `data: my-container-name` (`:96`) and `file_types` (`:97-98`). The environment variable is documented at `:101-102`.
- **Tests and fixtures:** none.
- **Errors raised:** `ProviderConnectionError` when the environment variable is missing (`pygeoapi/provider/azure_.py:57-60`) and `ProviderNotFoundError` (`:160-163`). (read)
- **Gotchas and conflicts:**
  - `__repr__` says `<FileSystemProvider>`, a copy-paste leftover (`pygeoapi/provider/azure_.py:237`).
  - `urlpath` is cut down to its first segment (`:78`).
  - The directory detection `walk_blobs(...) or data_path == ''` always treats a path as a directory, because a generator is truthy (`:145`).
  - The file branch uses `tmp_path` from the loop (`:203-204`).

### `Hateoas` — `hateoas.HateoasProvider`
- **Provider type(s):** `stac`. The class is `HateoasProvider(BaseProvider)` (`pygeoapi/provider/hateoas.py:42`). (read)
- **Dependencies:** `requests` only (`pygeoapi/provider/hateoas.py:31`). (read)
- **`data` forms:** The root of a static STAC catalog, either a local directory or an `http(s)` URL. The case-insensitive `HTTP` prefix check is at `pygeoapi/provider/hateoas.py:231`. The provider tries these files in order:
  1. `{data}{entrypath}/catalog.json`
  2. `{data}{entrypath}/collection.json`
  3. `{data}{entrypath}/{basename}.json`

  (`:116-134`) (read)
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` / `type` / `data` | string | yes | — | `Hateoas`, `stac`, dir or URL | `pygeoapi/provider/base.py:63-65`, `pygeoapi/provider/hateoas.py:73` |
  | `file_types` | string | ignored (doc-only) | — | The docs say to set it to the root file name, as a **string** (`catalog.json`). No code reads it. | `docs/source/publishing/stac.rst:358-359`, `docs/source/publishing/stac.rst:373`, `pygeoapi/provider/hateoas.py:116-134` |

- **Capabilities:** Walks `child` and `item` links (`pygeoapi/provider/hateoas.py:139-173`) and returns a Catalog, a Collection or an Item (asset) dict (`:136-189`). (read)
- **Docs examples:**
  - `docs/source/publishing/stac.rst:366-382`: a remote URL (`:372`) and a local `tests/stac` (`:381`). The `tests/stac` directory does **not** exist in the 0.24.0 tree.
  - The structural rules (`:133-149`) are enforced by the code.
- **Tests and fixtures:** config block `tests/pygeoapi-test-stac-api-config.yml:103-121`, which points at a remote USGS catalog. The `extents:` and `spatial:` keys there are mis-indented as siblings (`:113-116`). There are no unit tests.
- **Errors raised:** `ProviderNotFoundError` when no JSON document resolves (`pygeoapi/provider/hateoas.py:131-134`). (read)
- **Gotchas and conflicts:**
  - **Link hrefs must be exactly `./<dir>/<file>.json`.** The code does `unused, path_ending, entry_type = link.split('/')` (`pygeoapi/provider/hateoas.py:147`). Any other depth, or an absolute href, raises `ValueError`, which the API turns into 500 (`pygeoapi/api/stac.py:192-196`). This matches docs rule 3 (`docs/source/publishing/stac.rst:139`).

---

## 5. Cross-cutting

### 5.1 Tiles (`pygeoapi/api/tiles.py`)

**Routes** (flask):

| Route | Source |
|---|---|
| `/TileMatrixSets` | `pygeoapi/flask_app.py:222` |
| `/TileMatrixSets/<tileMatrixSetId>` | `pygeoapi/flask_app.py:208` |
| `/collections/<id>/tiles` | `pygeoapi/flask_app.py:344` |
| `/collections/<id>/tiles/<tileMatrixSetId>` and `/collections/<id>/tiles/<tileMatrixSetId>/metadata` | `pygeoapi/flask_app.py:358-359` |
| `/collections/<id>/tiles/<tileMatrixSetId>/<tileMatrix>/<tileRow>/<tileCol>` (`tileRow` = y, `tileCol` = x, format check skipped) | `pygeoapi/flask_app.py:376-398` |

**Calls the API makes on a tile provider** (the provider is always chosen with `get_provider_by_type(..., 'tile')`):

1. `/tiles` list
   - `p.get_tiles_service(baseurl=api.base_url, servicepath=f'{collections_url}/{id}/tiles/{{tileMatrixSetId}}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}?f={p.format_type}')` (`pygeoapi/api/tiles.py:132-135`). The result must be a dict with `links` (`:137-138`).
   - Then `p.get_tiling_schemes()`, a list of `TileMatrixSetEnumType`. The API uses `.tileMatrixSetURI`, `.crs` and `.tileMatrixSet` from it (`:140-174`).
   - dataType is `map` when `format.mimetype` starts with `image`, and `vector` otherwise (`:142-145`).
   - For HTML it also reads `p.options['zoom']['min'/'max']`, `p.tile_type` and the resource's `extents.spatial.bbox` (`:183-188`).
   - Note: the base `BaseMVTProvider.get_tiles_service` returns `None` (`pygeoapi/provider/base_mvt.py:89-108`). Every subclass must override it and return `get_tms_links()`, as all core MVT subclasses do.
2. Tile data
   - `format_ = p.format_type`, i.e. `format.name`. The request's `f` is ignored after the "format present" check (`pygeoapi/api/tiles.py:219-221`, `:240`).
   - `Content-Type` is set to `format.mimetype` (`:241`).
   - The provider is called as `p.get_tiles(layer=p.get_layer(), tileset=matrix_id, z=z_idx, y=y_idx, x=x_idx, format_=format_)` (`:244-245`). The indices are **strings** from the path.
   - Outcomes:

     | Provider result | HTTP response | Source |
     |---|---|---|
     | `None` | 204 | `pygeoapi/api/tiles.py:246-249` |
     | `KeyError` | 400 | `pygeoapi/api/tiles.py:254-258` |
     | `ProviderTileNotFoundError` | 404 | `pygeoapi/api/tiles.py:259-262` |
     | `ProviderGenericError` | its own mapped status | `pygeoapi/api/tiles.py:263-266` |

   - Only resources with `type: collection` are served (`:226-232`).
3. Tile metadata
   - Valid formats also include `tilejson` (`pygeoapi/api/tiles.py:283`).
   - The call is `p.get_metadata(dataset=, server_url=api.base_url, layer=p.get_layer(), tileset=matrix_id, metadata_format=request._format, title=, description=, language=)` (`:318-327`). The resource's `keywords` are **not** passed.
   - The MVT dispatch on `metadata_format.upper()` covers `JSON`, `TILEJSON`, `HTML` and `JSONLD` (`pygeoapi/provider/base_mvt.py:205-222`). WMTS covers `JSON` and `HTML` (`pygeoapi/provider/wmts_facade.py:219-228`).
   - This call is **not wrapped** in a try block, so provider errors propagate.

**Tile matrix sets:**
- Loaded dynamically from `pygeoapi/resources/definitions/tiles/*.json` by `TileMatrixSetLoader` (`pygeoapi/models/provider/base.py:45`, `:126-135`).
- 0.24.0 ships two:

  | TMS id | Enum member | URI | CRS | Matrices | z0 size | Source |
  |---|---|---|---|---|---|---|
  | `WebMercatorQuad` | `WEBMERCATORQUAD` | `http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad` | EPSG:3857 | 25 (z0–24) | 1×1 | `pygeoapi/resources/definitions/tiles/WebMercatorQuad.json:2-5` |
  | `WorldCRS84Quad` | `WORLDCRS84QUAD` | `http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad` | OGC CRS84 | 24 | 2×1 | `pygeoapi/resources/definitions/tiles/WorldCRS84Quad.json:2-5` |

- Enum member names are the id upper-cased, with `-` and spaces removed (`pygeoapi/models/provider/base.py:128`).
- Custom TMS files dropped into that directory (`docs/source/publishing/ogcapi-tiles.rst:209-212`) become usable only by **MVT-proxy**, via `options.schemes`. Every other provider hard-codes the two enum members. (read; TMS summary executed)

**Metadata models:**
- `TileSetMetadata` (`pygeoapi/models/provider/base.py:201`) and `MVTTilesJson` (`pygeoapi/models/provider/mvt.py:43`).

**"One small tile" probe without a server.** Instantiate the provider the same way the API does, then call its methods directly:

```python
from pygeoapi.plugin import load_plugin
p = load_plugin('provider', provider_def)            # fresh instance
tms = p.get_tiling_schemes()[0].tileMatrixSet        # WMTS: options.scheme
z = provider_def['options']['zoom']['min']
tile = p.get_tiles(layer=p.get_layer(), tileset=tms, z=z, y=0, x=0,
                   format_=p.format_type)
# bytes → OK (MVT: optionally decode; PNG: b'\x89PNG'; JPEG: b'\xff\xd8');
# None → "in limits but empty" (API 204); ProviderTileNotFoundError → 404
p2 = load_plugin('provider', provider_def)           # NEW instance (see tippecanoe gotcha)
md = p2.get_metadata(dataset='probe', server_url='http://localhost:5000',
                     layer=p2.get_layer(), tileset=tms, metadata_format='json',
                     title='t', description='d')
```

The probe's inputs and external requirements differ per provider:

| Provider | Probe inputs | Needs |
|---|---|---|
| MVT-tippecanoe (disk) | z = `zoom.min`, x = y = 0 on WebMercatorQuad | Nothing external. |
| MVT-tippecanoe (URL), MVT-proxy, MVT-elastic | Same | The upstream HTTP server. To test URL construction offline, monkeypatch `requests.Session.get` (§6). |
| MVT-postgresql | `tileset` must be `WebMercatorQuad` or `WorldCRS84Quad` | A live PostGIS, already at construction time. |
| WMTSFacade | `tileset` = `options.scheme` | The upstream WMTS. It sends `tileMatrix=z&tileRow=y&tileCol=x` for the *upstream* `wmts_tile_matrix_set`, so pick the upstream `zoom.min` tile. |

Suggested checks:
- the result is `bytes` with length > 0;
- the magic bytes match `format.mimetype`;
- for MVT, optionally decode with `mapbox-vector-tile`, which is not a pygeoapi dependency.

### 5.2 Maps (`pygeoapi/api/maps.py`)

**Routes:** `/collections/<id>/map` and `/collections/<id>/styles/<style_id>/map` (`pygeoapi/flask_app.py:402-416`). A legend function exists (`pygeoapi/api/maps.py:273-329`), but no route uses it, and no core provider implements `get_legend`. (read)

**Contract.** The API builds these `query_args` and calls `p.query(**query_args)` (`pygeoapi/api/maps.py:245`):

| Argument | Value | Source |
|---|---|---|
| `format_` | Request `f`, default `png` | `pygeoapi/api/maps.py:107` |
| `style` | The `/styles/{style}` path value, else `None` | `pygeoapi/api/maps.py:108` |
| `crs` | Request `crs` through `get_uri`; otherwise the provider's `storage_crs`; otherwise CRS84 | `pygeoapi/api/maps.py:112-120` |
| `'bbox-crs'` | A hyphenated key, passed through `**kwargs`. Request `bbox-crs`, default CRS84 | `pygeoapi/api/maps.py:125-131` |
| `transparent` | Raw string or `True` | `pygeoapi/api/maps.py:135` |
| `width` / `height` | int, default 500 × 300 | `pygeoapi/api/maps.py:137-139` |
| `bbox` | 4 floats, default `[-180,-90,180,90]`. **Already transformed from `bbox-crs` into `crs`** when they differ | `pygeoapi/api/maps.py:65`, `pygeoapi/api/maps.py:150-183` |
| `datetime_` | Validated against the resource `extents` | `pygeoapi/api/maps.py:185-194` |
| `subsets` | Only when `subset` is given | `pygeoapi/api/maps.py:196-216` |
| `select_properties` | Only when `properties` is given; needs `get_fields` | `pygeoapi/api/maps.py:218-241` |

**Response:**
- The provider must return `bytes`.
- The API returns them with `Content-Type: format.mimetype` when `f == format.name`, or when `f` is absent or `html` (`pygeoapi/api/maps.py:252-262`).
- It sets the `Content-Crs` and `Content-Bbox` headers (`:254-255`).
- Any other `f` returns 400. The body in that case is `to_json(data)` of the image, which looks like a bug (`:263-270`).

**Options:** WMSFacade reads `options.layer`, `options.style` and `options.version`. MapScript reads `options.type`, `options.style`, `options.layer`, `options.projection` and `options.tileindex`. See the per-provider tables.

**"One small image" probe:**

```python
p = load_plugin('provider', provider_def)
img = p.query(bbox=[-180, -90, 180, 90], width=64, height=32,
              crs='http://www.opengis.net/def/crs/OGC/1.3/CRS84',
              format_=provider_def['format']['name'], transparent=True,
              style=None, datetime_=None)
ok = isinstance(img, bytes) and (img[:8] == b'\x89PNG\r\n\x1a\n' or img[:2] == b'\xff\xd8')
```

For WMSFacade, validate the magic bytes, because the provider does not check the HTTP status. To see the exact GetMap URL offline, monkeypatch `requests.get` (§6). MapScript needs `mapscript` and GDAL installed.

### 5.3 Records vs features (`pygeoapi/api/itemtypes.py`, `pygeoapi/api/collection.py`)

**How the API treats records differently from features:**
- **Provider selection.** `/items` and `/items/{id}` look for a `feature` provider **first** and fall back to `record` (`pygeoapi/api/itemtypes.py:335-345`, `:891-901`). OpenAPI generation does the opposite and prefers `record` when both exist (`:1086-1092`). Queryables try `feature`, then `coverage`, then `record` (`:127`).
- **CRS.** The `crs` parameter and the `Content-Crs` header apply to features only. Records are never reprojected (`pygeoapi/api/itemtypes.py:371-385`, `:912-927`). The collection document lists `crs` and `storageCrs` only for `edr` and `feature` (`pygeoapi/api/collection.py:273-275`).
- **Full-text search.** `q` is a reserved query parameter (`pygeoapi/api/itemtypes.py:254-257`) and is passed to **every** provider as `q=` (`:557-563`). OpenAPI adds the Records `q` parameter only for records (`:1206-1208`) and skips a `q` field so it is not declared twice (`:1264-1266`). The catalogue providers add `q` to `get_fields()` so it shows up in `/queryables` (`pygeoapi/provider/tinydb_.py:406`, `pygeoapi/provider/elasticsearch_.py:620`).
- **Collection document.** It has `itemType: record` (`pygeoapi/api/collection.py:243-246`) and extra `ogc-catalog` links (`:207-218`).
- **Conformance.** Records conformance classes are advertised at `pygeoapi/api/itemtypes.py:94-98`.
- **HTML.** The templates branch on `itemtype == 'record'` (`pygeoapi/templates/collections/items/index.html:20`), where `itemtype` = `p.type` (`pygeoapi/api/itemtypes.py:669`). `title_field` replaces `id_field` as the HTML label (`:679-683`, `:1016-1018`). `uri_field` sets the item URI and the JSON-LD id (`:949`, `:1028`).
- **Record title and type fields.** The API does not pull `title` or `type` out of a record. They are plain `properties` members. CSWFacade maps them from DC/DCT fields (`pygeoapi/provider/csw_facade.py:61-71`). TinyDBCatalogue builds `_metadata-anytext` from `title` + `description` on create (`pygeoapi/provider/tinydb_.py:410-421`).

**What a Records provider must return:**
- **`query(**kw)`** returns a dict with:
  - `type: 'FeatureCollection'`;
  - `features: [...]`, each record a GeoJSON-like dict with `id`, `type: 'Feature'`, `geometry` (may be `null`), `properties` holding `title`, `description`, `type`, `keywords` and so on, and optionally `time` and `links`;
  - `numberMatched`, when `count` is set;
  - `numberReturned`.

  For `resulttype=hits`, return only `numberMatched`. The API adds links, paging and `timeStamp` (`pygeoapi/api/itemtypes.py:577-658`), and uses the provider's own `next` link if it supplies one (`:610-611`).
- **`get(identifier, **kw)`** returns a single record, or raises `ProviderItemNotFoundError`. Returning `None` produces a **400** "identifier not found" (`pygeoapi/api/itemtypes.py:944-947`).
- **`get_fields()`** returns `{name: {'type': ..., 'format'?: ...}}`, including `q`.
- **`get_domains()`** is optional. It is used by `profile=actual-domain`, and `NotImplementedError` is tolerated (`pygeoapi/api/itemtypes.py:180-185`).

  (read)

### 5.4 STAC (`pygeoapi/api/stac.py`)

**Two separate things share the name STAC.**

1. **Static STAC browsing: `/stac` and `/stac/<path>`.** This is for resources with `type: stac-collection` whose provider has `type: stac`, i.e. FileSystem, AzureBlobStorage or Hateoas (`pygeoapi/flask_app.py:556-577`).
   - `get_stac_root` lists every `stac-collection` resource that has a `stac` provider (`pygeoapi/api/stac.py:99-123`).
   - `get_stac_path` takes the first path segment as the resource id (`:149-151`) and calls `p.get_data_path(f'{base_url}/stac', path, path.replace(dataset, '', 1))` (`:183-187`).
   - A dict result gets the resource `links` appended (`:198-201`). `bytes` are sent back as a raw file (`:232-234`).
   - The resource `description` is used (`:115`, `:179`).
2. **STAC API: `/stac-api` and `/stac-api/search`.** This is for resources with `type: stac-collection` whose provider has type **`feature` or `record`** (`pygeoapi/api/stac.py:319-330`), for example TinyDBCatalogue (`tests/pygeoapi-test-stac-api-config.yml:152-188`).
   - Search temporarily flips the resource `type` to `collection` and calls `get_collection_items` (`pygeoapi/api/stac.py:359-363`).
   - It then adds `stac_version`, `bbox`, `links` and `assets` to each feature, and derives a temporal value from `properties.datetime`, the `time` object or `properties.created` (`:371-386`, `:474-512`).
   - POST bodies map only `bbox`, `datetime`, `limit` and `offset` (`:342-347`).

**How a `stac-collection` resource differs from a `collection`:**
- **Not served by the OGC endpoints.** `/collections/{id}/items`, `/tiles` and the rest filter on `type: collection` (`pygeoapi/api/itemtypes.py:259-260`, `pygeoapi/api/tiles.py:226-227`), so a `stac-collection` is not served there.
- **Keys the schema still requires:** `title`, `description`, `keywords`, `extents` and `providers` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:663-669`).
- **Keys the STAC code actually uses:** only `description`, `links` and `providers`.
- **Provider keys:**
  - `type: stac`, `name`, `data`;
  - `file_types` for FileSystem and Azure;
  - no `format`, no `options`, no `id_field`.
- **OpenAPI:** it documents only `/stac/catalog` (`pygeoapi/api/stac.py:458`). That path is not a real route (the routes are `/stac` and `/stac/<path>`). (read)

---

## 6. Execution log

The executed checks are pending.

---

## 7. Open questions
- `get_curie` and `get_uri` *return* `CRSError` instead of raising it (`pygeoapi/crs.py:178-179`, `:211-212`). Is this intentional? It affects WMSFacade, MapScript and the maps API `crs` handling (`pygeoapi/api/maps.py:117`).
- OWSLib, the CSWFacade dependency, is missing from `requirements-provider.txt`. Presumably it arrives transitively through pygeometa; not verified.
- Should the Builder offer `options.schemes` for MVT-postgresql? Per §1 it is harmful, so it should probably not.
