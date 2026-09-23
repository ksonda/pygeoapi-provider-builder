# pygeoapi 0.24.0 provider contract (plugin loader, base classes, API-layer expectations)

Status: complete for Phase 0 (all sections written; citations self-checked against the 0.24.0 source)

Ground truth: pygeoapi **0.24.0** (`pygeoapi/__init__.py:33`), source at `vendor/pygeoapi` (tag 0.24.0, commit f765a64), identical to the package in `.venv` (Python 3.12.14). All citations are `path:line` relative to the pygeoapi repo root. "(executed)" = confirmed by running code in the venv; "(read)" = from reading source. Experiment scripts live in the session scratchpad (`provider-contract/exp*.py`), not in the repo.

---

## 1. Plugin loader (`pygeoapi/plugin.py`)

### 1.1 `PLUGINS['provider']` registry

`PLUGINS` is a plain module-level dict (`pygeoapi/plugin.py:39`); the `'provider'` sub-dict starts at `pygeoapi/plugin.py:40`. 33 short names (executed: `len(PLUGINS['provider']) == 33`):

| Short name | Class path | Line |
|---|---|---|
| `AzureBlobStorage` | `pygeoapi.provider.azure_.AzureBlobStorageProvider` | `pygeoapi/plugin.py:41` |
| `CSV` | `pygeoapi.provider.csv_.CSVProvider` | `pygeoapi/plugin.py:42` |
| `CSWFacade` | `pygeoapi.provider.csw_facade.CSWFacadeProvider` | `pygeoapi/plugin.py:43` |
| `Elasticsearch` | `pygeoapi.provider.elasticsearch_.ElasticsearchProvider` | `pygeoapi/plugin.py:44` |
| `ElasticsearchCatalogue` | `pygeoapi.provider.elasticsearch_.ElasticsearchCatalogueProvider` | `pygeoapi/plugin.py:45` |
| `ERDDAPTabledap` | `pygeoapi.provider.erddap.TabledapProvider` | `pygeoapi/plugin.py:46` |
| `ESRI` | `pygeoapi.provider.esri.ESRIServiceProvider` | `pygeoapi/plugin.py:47` |
| `FileSystem` | `pygeoapi.provider.filesystem.FileSystemProvider` | `pygeoapi/plugin.py:48` |
| `GeoJSON` | `pygeoapi.provider.geojson.GeoJSONProvider` | `pygeoapi/plugin.py:49` |
| `Hateoas` | `pygeoapi.provider.hateoas.HateoasProvider` | `pygeoapi/plugin.py:50` |
| `MapScript` | `pygeoapi.provider.mapscript_.MapScriptProvider` | `pygeoapi/plugin.py:51` |
| `MongoDB` | `pygeoapi.provider.mongo.MongoProvider` | `pygeoapi/plugin.py:52` |
| `MVT-tippecanoe` | `pygeoapi.provider.mvt_tippecanoe.MVTTippecanoeProvider` | `pygeoapi/plugin.py:53` |
| `MVT-elastic` | `pygeoapi.provider.mvt_elastic.MVTElasticProvider` | `pygeoapi/plugin.py:54` |
| `MVT-proxy` | `pygeoapi.provider.mvt_proxy.MVTProxyProvider` | `pygeoapi/plugin.py:55` |
| `MySQL` | `pygeoapi.provider.sql.MySQLProvider` | `pygeoapi/plugin.py:56` |
| `MVT-postgresql` | `pygeoapi.provider.mvt_postgresql.MVTPostgreSQLProvider` | `pygeoapi/plugin.py:57` |
| `OracleDB` | `pygeoapi.provider.oracle.OracleProvider` | `pygeoapi/plugin.py:58` |
| `OGR` | `pygeoapi.provider.ogr.OGRProvider` | `pygeoapi/plugin.py:59` |
| `OpenSearch` | `pygeoapi.provider.opensearch_.OpenSearchProvider` | `pygeoapi/plugin.py:60` |
| `Parquet` | `pygeoapi.provider.parquet.ParquetProvider` | `pygeoapi/plugin.py:61` |
| `PostgreSQL` | `pygeoapi.provider.sql.PostgreSQLProvider` | `pygeoapi/plugin.py:62` |
| `rasterio` | `pygeoapi.provider.rasterio_.RasterioProvider` | `pygeoapi/plugin.py:63` |
| `SensorThings` | `pygeoapi.provider.sensorthings.SensorThingsProvider` | `pygeoapi/plugin.py:64` |
| `SensorThingsEDR` | `pygeoapi.provider.sensorthings_edr.SensorThingsEDRProvider` | `pygeoapi/plugin.py:65` |
| `SQLiteGPKG` | `pygeoapi.provider.sqlite.SQLiteGPKGProvider` | `pygeoapi/plugin.py:66` |
| `Socrata` | `pygeoapi.provider.socrata.SODAServiceProvider` | `pygeoapi/plugin.py:67` |
| `TinyDB` | `pygeoapi.provider.tinydb_.TinyDBProvider` | `pygeoapi/plugin.py:68` |
| `TinyDBCatalogue` | `pygeoapi.provider.tinydb_.TinyDBCatalogueProvider` | `pygeoapi/plugin.py:69` |
| `WMSFacade` | `pygeoapi.provider.wms_facade.WMSFacadeProvider` | `pygeoapi/plugin.py:70` |
| `WMTSFacade` | `pygeoapi.provider.wmts_facade.WMTSFacadeProvider` | `pygeoapi/plugin.py:71` |
| `xarray` | `pygeoapi.provider.xarray_.XarrayProvider` | `pygeoapi/plugin.py:72` |
| `xarray-edr` | `pygeoapi.provider.xarray_edr.XarrayEDRProvider` | `pygeoapi/plugin.py:73` |

The same dict also has `formatter` (`pygeoapi/plugin.py:75`), `process` (`pygeoapi/plugin.py:78`), `process_manager` (`pygeoapi/plugin.py:83`) and `pubsub` (`pygeoapi/plugin.py:89`) sections. Short names are case-sensitive (executed: `name: geojson` raises `InvalidPluginError: Plugin geojson not found`). The base classes themselves are not registered.

### 1.2 `load_plugin(plugin_type, plugin_def)` (`pygeoapi/plugin.py:97`)

Order of operations (read, each confirmed executed):

1. `name = plugin_def['name']` (`pygeoapi/plugin.py:107`) - runs first, so a def without `name` raises a bare `KeyError: 'name'` (executed).
2. Unknown `plugin_type` -> `InvalidPluginError('Plugin type ... not found')` (`pygeoapi/plugin.py:109`-`pygeoapi/plugin.py:112`) (executed).
3. Short-name lookup: if the name contains no `.` and is not a registry key -> `InvalidPluginError('Plugin <name> not found')` (`pygeoapi/plugin.py:118`-`pygeoapi/plugin.py:121`) (executed). Registry lookup is per plugin type: a process short name (`HelloWorld`) used as a provider name is "not found" (executed).
4. Any name containing a `.` is a dotted path, split on the **last** dot: `packagename, classname = name.rsplit('.', 1)` (`pygeoapi/plugin.py:123`-`pygeoapi/plugin.py:124`). Registry names are resolved the same way from the registry value (`pygeoapi/plugin.py:126`). Consequence: a registry short name can never contain a dot, and a third-party class must be at least `module.Class`.
5. `module = importlib.import_module(packagename)` (`pygeoapi/plugin.py:131`) then `class_ = getattr(module, classname)` (`pygeoapi/plugin.py:132`). Nothing is caught:
   - unimportable module -> `ModuleNotFoundError: No module named 'nosuchpkg'` propagates (executed);
   - importable module, missing class -> `AttributeError: module 'pygeoapi.provider.geojson' has no attribute 'NoSuchClass'` propagates (executed);
   - any exception raised while importing the module (e.g. the EDR class-definition check in section 7) propagates unchanged.
6. `plugin = class_(plugin_def)` (`pygeoapi/plugin.py:133`): the class is called with the **whole provider definition dict, the same object** (executed: `spy.received is pdef` -> `True`). There is no `issubclass` check: any callable taking one dict works (executed with a class that does not subclass `BaseProvider`).
7. `InvalidPluginError` is a plain `Exception` subclass (`pygeoapi/plugin.py:138`), **not** a `ProviderGenericError`, so none of the API catch sites (section 3.3) map it to an OGC exception: an unknown short name surfaces as an unhandled exception (HTTP 500 from the framework) at request time (read).
8. Cosmetic: `LOGGER.exception(msg)` is called outside an `except` block (`pygeoapi/plugin.py:111`, `pygeoapi/plugin.py:120`), which prints `NoneType: None` to stderr (executed).

No caching: every API handler calls `load_plugin('provider', ...)` per request (e.g. `pygeoapi/api/itemtypes.py:339`, `pygeoapi/api/environmental_data_retrieval.py:280`, `pygeoapi/api/coverages.py:98`, `pygeoapi/api/tiles.py:97`), so the provider constructor runs on every request, plus during OpenAPI generation (`pygeoapi/api/itemtypes.py:1094`) and collection-metadata generation for coverage/tile/map/EDR providers (`pygeoapi/api/collection.py:307`, `pygeoapi/api/collection.py:333`, `pygeoapi/api/collection.py:355`, `pygeoapi/api/collection.py:398`) (read). Python's `sys.modules` caches the module import, so module-level code runs once per process.

### 1.3 No entry-point discovery for providers

The only `entry_points` use in the package is the CLI: `entry_points(group="pygeoapi")` (`pygeoapi/__init__.py:57`) whose members are added as click sub-commands of `pygeoapi plugins` (`pygeoapi/__init__.py:71`, group defined at `pygeoapi/__init__.py:85`-`pygeoapi/__init__.py:87`). `load_plugin` only consults `PLUGINS` and `importlib.import_module` (read; `grep` for `entry_points|importlib.metadata|pkg_resources` finds nothing else under `pygeoapi/`). A third-party provider is therefore wired **only** by putting its dotted path in the config `name:` and making its module importable (installed package or on `sys.path`).

---

## 2. `BaseProvider` (`pygeoapi/provider/base.py:48`)

### 2.1 Constructor: the definitive list of common provider keys

`BaseProvider.__init__(self, provider_def)` (`pygeoapi/provider/base.py:51`):

| provider_def key | Attribute | Default / processing | Citation |
|---|---|---|---|
| `name` (required) | `self.name` | - | `pygeoapi/provider/base.py:63` |
| `type` (required) | `self.type` | - | `pygeoapi/provider/base.py:64` |
| `data` (required) | `self.data` | any type (str, dict, ...) | `pygeoapi/provider/base.py:65` |
| (missing any of the 3) | - | `RuntimeError('name/type/data are required')` (executed) | `pygeoapi/provider/base.py:67` |
| `editable` | `self.editable` | `False`; not passed through `str2bool` | `pygeoapi/provider/base.py:69` |
| `count` | `self.count` | `str2bool(provider_def.get('count', True))` | `pygeoapi/provider/base.py:70` |
| `options` | `self.options` | `None` (not `{}`) | `pygeoapi/provider/base.py:71` |
| `id_field` | `self.id_field` | `None` | `pygeoapi/provider/base.py:72` |
| `uri_field` | `self.uri_field` | `None` | `pygeoapi/provider/base.py:73` |
| `x_field` | `self.x_field` | `None` | `pygeoapi/provider/base.py:74` |
| `y_field` | `self.y_field` | `None` | `pygeoapi/provider/base.py:75` |
| `z_field` | `self.z_field` | `None` | `pygeoapi/provider/base.py:76` |
| `time_field` | `self.time_field` | `None` | `pygeoapi/provider/base.py:77` |
| `title_field` | `self.title_field` | `None` | `pygeoapi/provider/base.py:78` |
| `properties` | `self.properties` | `[]` | `pygeoapi/provider/base.py:79` |
| `file_types` | `self.file_types` | `[]` | `pygeoapi/provider/base.py:80` |
| `include_extra_query_parameters` | `self.include_extra_query_parameters` | `False` | `pygeoapi/provider/base.py:81` |
| - | `self._fields` | `{}` | `pygeoapi/provider/base.py:82` |
| - | `self.filename` | `None` | `pygeoapi/provider/base.py:83` |
| `storage_crs` | `self.storage_crs` | `get_crs(provider_def.get('storage_crs', DEFAULT_STORAGE_CRS))` -> a `pyproj.CRS` (CRS84 by default) | `pygeoapi/provider/base.py:86`-`pygeoapi/provider/base.py:87` |
| - | `self.axes` | `[]` (coverage) | `pygeoapi/provider/base.py:90` |
| - | `self.crs` | `None` (coverage; **not** the `crs:` config list) | `pygeoapi/provider/base.py:91` |
| - | `self.num_bands` | `None` (coverage) | `pygeoapi/provider/base.py:92` |

Notes:
- `DEFAULT_STORAGE_CRS` = `DEFAULT_CRS` = `http://www.opengis.net/def/crs/OGC/1.3/CRS84` (`pygeoapi/crs.py:60`-`pygeoapi/crs.py:61`). An unparseable `storage_crs` makes the constructor raise `pyproj.exceptions.CRSError` via `get_crs` (`pygeoapi/crs.py:251`) (read).
- Keys the **API layer reads directly from the provider_def** (never via an attribute): `crs` (list of supported output CRSs, `pygeoapi/crs.py:111`), `storage_crs`, `storage_crs_coordinate_epoch`, `always_xy` (section 4), `geom_field` (`pygeoapi/api/itemtypes.py:499`), `languages`/`language` (`pygeoapi/l10n.py:473`), `default` (`pygeoapi/provider/__init__.py:81`), `format.name`/`format.mimetype` for coverage/map/tile (`pygeoapi/api/coverages.py:193`, `pygeoapi/api/maps.py:252`, `pygeoapi/api/tiles.py:241`) (read).
- Everything else in provider_def is provider-specific (e.g. CSV's `geometry.x_field`, `pygeoapi/provider/csv_.py:61`, which is *not* the base `x_field`) (read).

### 2.2 Methods and default behaviour

| Method (exact signature) | Default | Citation |
|---|---|---|
| `get_fields(self)` | `raise NotImplementedError()`; docstring example `{'field1': {'type': 'string'}, 'field2': {'type': 'number'}}` | `pygeoapi/provider/base.py:94`, `pygeoapi/provider/base.py:99`, `pygeoapi/provider/base.py:104` |
| `fields` (property) | `return self._fields` if the attribute exists, else `self.get_fields()`; because `__init__` always sets `_fields = {}`, **it returns `self._fields` (initially `{}`) and never calls `get_fields()`** (executed: `GeoJSONProvider.fields is ._fields` -> True; uncached provider -> `{}`) | `pygeoapi/provider/base.py:106`-`pygeoapi/provider/base.py:121` |
| `get_schema(self, schema_type: SchemaType = SchemaType.item)` | `raise NotImplementedError()`; returns `(media_type, schema_dict)` | `pygeoapi/provider/base.py:123`, `pygeoapi/provider/base.py:133` |
| `get_data_path(self, baseurl, urlpath, dirpath)` | `raise NotImplementedError()` (used by STAC/FileSystem) | `pygeoapi/provider/base.py:135`, `pygeoapi/provider/base.py:146` |
| `get_metadata(self)` | `raise NotImplementedError()` | `pygeoapi/provider/base.py:148`, `pygeoapi/provider/base.py:156` |
| `get_domains(self, properties=[], current=False)` | `raise NotImplementedError()`; returns `(domains, is_current)` | `pygeoapi/provider/base.py:158`, `pygeoapi/provider/base.py:170` |
| `query(self)` | `raise NotImplementedError()`; **declared with no parameters** although the API always calls it with keyword arguments | `pygeoapi/provider/base.py:172`, `pygeoapi/provider/base.py:179` |
| `get(self, identifier, **kwargs)` | `raise NotImplementedError()` | `pygeoapi/provider/base.py:181`, `pygeoapi/provider/base.py:190` |
| `create(self, item)` | `raise NotImplementedError()`; returns the new identifier | `pygeoapi/provider/base.py:192`, `pygeoapi/provider/base.py:201` |
| `update(self, identifier, item)` | `raise NotImplementedError()`; returns `bool` | `pygeoapi/provider/base.py:203`, `pygeoapi/provider/base.py:213` |
| `delete(self, identifier)` | `raise NotImplementedError()`; returns `bool` | `pygeoapi/provider/base.py:215`, `pygeoapi/provider/base.py:224` |
| `_load_and_prepare_item(self, item, identifier=None, accept_missing_identifier=False, raise_if_exists=True)` | helper for create/update: `json.loads(item)`; `ProviderInvalidDataError` on bad JSON, missing id (`id` or `properties.identifier`), missing `geometry`/`properties`, or (if `raise_if_exists`) when `self.get(id)` succeeds (`user_msg='record already exists'`) | `pygeoapi/provider/base.py:226`, `pygeoapi/provider/base.py:250`, `pygeoapi/provider/base.py:259`, `pygeoapi/provider/base.py:275`, `pygeoapi/provider/base.py:279`, `pygeoapi/provider/base.py:287`-`pygeoapi/provider/base.py:291` |
| `sanitize_attribute_value(self, value) -> str` | SQL-literal quoting helper (OGR) | `pygeoapi/provider/base.py:297` |
| `__repr__(self)` | `'<BaseProvider> {self.type}'` | `pygeoapi/provider/base.py:318`-`pygeoapi/provider/base.py:319` |

`SchemaType` enum values: `item`, `create`, `update`, `replace` (`pygeoapi/provider/base.py:41`-`pygeoapi/provider/base.py:45`). The API only ever calls `get_schema()` (item), `get_schema(SchemaType.create)` and `get_schema(SchemaType.replace)`, and only while building OpenAPI, inside `except Exception` (`pygeoapi/api/itemtypes.py:1199`, `pygeoapi/api/itemtypes.py:1203`, `pygeoapi/api/itemtypes.py:1333`, `pygeoapi/api/itemtypes.py:1341`, `pygeoapi/api/itemtypes.py:1372`) - `SchemaType.update` is unused (read). Not declared on `BaseProvider` but called by the API: `get_legend(style, format)` for map providers (`pygeoapi/api/maps.py:311`), `get_parameters()`/`get_query_types()`/`instance()`/`instances()` (EDR, section 7), and the attribute `_coverage_properties` (coverage, section 9) (read).

**`create`/`update` input** - the API passes the raw request body (`bytes`, from `request.data`, `pygeoapi/api/__init__.py:335`-`pygeoapi/api/__init__.py:337`) to `create(payload)`/`update(identifier, payload)` (`pygeoapi/api/itemtypes.py:803`-`pygeoapi/api/itemtypes.py:804`, `pygeoapi/api/itemtypes.py:822`-`pygeoapi/api/itemtypes.py:823`) (executed: `create received type bytes`). Providers must parse it (the `_load_and_prepare_item` helper does `json.loads`, which accepts bytes). The core GeoJSON provider's `create(new_feature)` indexes `new_feature['properties']` as if it were a dict (`pygeoapi/provider/geojson.py:229`-`pygeoapi/provider/geojson.py:231`) - with bytes this raises `TypeError: a bytes-like object is required, not 'str'` (executed), which the API turns into HTTP 400 (`pygeoapi/api/itemtypes.py:805`) (read).

---

## 3. Error hierarchy and HTTP mapping

### 3.1 Classes

Root: `pygeoapi.error.GenericError(Exception)` (`pygeoapi/error.py:34`) with class attributes `ogc_exception_code = 'NoApplicableCode'` (`pygeoapi/error.py:40`), `http_status_code = HTTPStatus.INTERNAL_SERVER_ERROR` (`pygeoapi/error.py:41`), `default_msg = 'Unknown error'` (`pygeoapi/error.py:42`); `__init__(self, msg=None, *args, user_msg=None)` (`pygeoapi/error.py:44`); property `message` returns `user_msg` if set, else `default_msg` (`pygeoapi/error.py:51`-`pygeoapi/error.py:53`).

| Class | Parent | `http_status_code` | `ogc_exception_code` | `default_msg` | Citation |
|---|---|---|---|---|---|
| `ProviderGenericError` | `GenericError` | 500 (inherited) | `NoApplicableCode` (inherited) | `generic error (check logs)` | `pygeoapi/provider/base.py:322`-`pygeoapi/provider/base.py:324` |
| `ProviderConnectionError` | `ProviderGenericError` | 500 | `NoApplicableCode` | `connection error (check logs)` | `pygeoapi/provider/base.py:327`-`pygeoapi/provider/base.py:329` |
| `ProviderTypeError` | `ProviderGenericError` | **400** | `NoApplicableCode` | `invalid provider type` | `pygeoapi/provider/base.py:332`-`pygeoapi/provider/base.py:335` |
| `ProviderInvalidQueryError` | `ProviderGenericError` | **400** | `InvalidQuery` | `query error` | `pygeoapi/provider/base.py:338`-`pygeoapi/provider/base.py:342` |
| `ProviderQueryError` | `ProviderGenericError` | 500 | `NoApplicableCode` | `query error (check logs)` | `pygeoapi/provider/base.py:345`-`pygeoapi/provider/base.py:347` |
| `ProviderItemNotFoundError` | `ProviderGenericError` | **404** | `NotFound` | `identifier not found` | `pygeoapi/provider/base.py:350`-`pygeoapi/provider/base.py:354` |
| `ProviderNoDataError` | `ProviderGenericError` | **204** | `InvalidParameterValue` | `No data found` | `pygeoapi/provider/base.py:357`-`pygeoapi/provider/base.py:361` |
| `ProviderNotFoundError` | `ProviderGenericError` | 500 | `NoApplicableCode` | inherited `generic error (check logs)` | `pygeoapi/provider/base.py:364`-`pygeoapi/provider/base.py:366` |
| `ProviderVersionError` | `ProviderGenericError` | 500 | `NoApplicableCode` | inherited | `pygeoapi/provider/base.py:369`-`pygeoapi/provider/base.py:371` |
| `ProviderInvalidDataError` | `ProviderGenericError` | 500 | `NoApplicableCode` | inherited | `pygeoapi/provider/base.py:374`-`pygeoapi/provider/base.py:376` |
| `ProviderRequestEntityTooLargeError` | `ProviderGenericError` | **413** | `NoApplicableCode` | inherited; its `__init__` copies `msg` into `user_msg` so the message is shown | `pygeoapi/provider/base.py:379`-`pygeoapi/provider/base.py:387` |
| `ProviderTileQueryError` | `ProviderGenericError` | 500 | `NoApplicableCode` | `Tile not found` | `pygeoapi/provider/tile.py:178`-`pygeoapi/provider/tile.py:180` |
| `ProviderTileNotFoundError` | `ProviderItemNotFoundError` | 404 | `NotFound` | `Tile not found (check logs)` | `pygeoapi/provider/tile.py:183`-`pygeoapi/provider/tile.py:185` |
| `ProviderTilesetIdNotFoundError` | `ProviderTileQueryError` | 404 | `NotFound` | `Tileset id not found` | `pygeoapi/provider/tile.py:188`-`pygeoapi/provider/tile.py:192` |

### 3.2 Message visibility (important for generated code)

`ProviderX('text')` sets only `msg`; the client sees `err.message`, i.e. `default_msg`, **not** `'text'` (executed for every class: `ProviderInvalidQueryError('custom msg')` -> body `"description": "query error"`). To expose a message to the client a provider must pass `user_msg=` (`pygeoapi/error.py:44`-`pygeoapi/error.py:53`); the only exception is `ProviderRequestEntityTooLargeError`, which promotes `msg` to `user_msg` (`pygeoapi/provider/base.py:384`-`pygeoapi/provider/base.py:386`) (executed: 413 with `"custom msg"`).

### 3.3 Where the API maps errors (`get_exception`)

`API.get_exception(status, headers, format_, code, description)` (`pygeoapi/api/__init__.py:562`) renders `{'code': code, 'type': code, 'description': description}` (`pygeoapi/api/__init__.py:582`-`pygeoapi/api/__init__.py:586`) and, for 204, suppresses the body (`pygeoapi/api/__init__.py:596`-`pygeoapi/api/__init__.py:598`). Mapped call sites use `err.http_status_code, ..., err.ogc_exception_code, err.message`.

Features/records (`pygeoapi/api/itemtypes.py`):
- `/items` provider load: `except ProviderTypeError` -> try `record` type, then 400 `Invalid provider type` (`pygeoapi/api/itemtypes.py:340`-`pygeoapi/api/itemtypes.py:350`); `except ProviderGenericError` -> mapped (`pygeoapi/api/itemtypes.py:351`-`pygeoapi/api/itemtypes.py:354`). Note: a `ProviderTypeError` raised by a feature provider's **constructor** is indistinguishable from "no feature provider configured" and triggers the record fallback (read).
- `query()`: `except ProviderGenericError` -> mapped (`pygeoapi/api/itemtypes.py:564`-`pygeoapi/api/itemtypes.py:567`). Executed mapping: Generic/Connection/Query/NotFound(provider)/Version/InvalidData -> 500; Type -> 400 `NoApplicableCode`; InvalidQuery -> 400 `InvalidQuery`; ItemNotFound -> 404 `NotFound`; NoData -> 204 with empty body; RequestEntityTooLarge -> 413. A non-Provider exception (e.g. `ValueError`, `KeyError`) is **not caught** and propagates to the framework (HTTP 500 without OGC body) (executed).
- `/items/{id}` load and `get()`: same pattern (`pygeoapi/api/itemtypes.py:896`-`pygeoapi/api/itemtypes.py:910`, `pygeoapi/api/itemtypes.py:939`-`pygeoapi/api/itemtypes.py:942`); `get()` returning `None` -> **400** (not 404) with code `NotFound` (`pygeoapi/api/itemtypes.py:944`-`pygeoapi/api/itemtypes.py:947`) (executed).
- transactions: load catches only `ProviderTypeError` (`pygeoapi/api/itemtypes.py:768`-`pygeoapi/api/itemtypes.py:777`) so a constructor `ProviderConnectionError` propagates uncaught (executed); `create`/`update`: `TypeError` -> 400 `InvalidParameterValue`, `ProviderGenericError` -> mapped (`pygeoapi/api/itemtypes.py:805`-`pygeoapi/api/itemtypes.py:813`, `pygeoapi/api/itemtypes.py:824`-`pygeoapi/api/itemtypes.py:832`); `delete`: first `p.get(identifier)` with `except ProviderItemNotFoundError` (`pygeoapi/api/itemtypes.py:839`-`pygeoapi/api/itemtypes.py:843`), then `p.delete` with `except ProviderGenericError` (`pygeoapi/api/itemtypes.py:846`-`pygeoapi/api/itemtypes.py:850`).
- queryables: load catches only `ProviderTypeError` while probing `feature`, `coverage`, `record` (`pygeoapi/api/itemtypes.py:127`-`pygeoapi/api/itemtypes.py:134`) -> constructor `ProviderConnectionError` propagates (executed); `get_domains` `NotImplementedError` swallowed (`pygeoapi/api/itemtypes.py:181`-`pygeoapi/api/itemtypes.py:185`).
- schema (`pygeoapi/api/__init__.py:1018`): nested `try/except ProviderTypeError` chain feature -> coverage -> record -> edr with a sibling `except ProviderGenericError` (`pygeoapi/api/__init__.py:1039`-`pygeoapi/api/__init__.py:1061`); only errors from the **first** (feature) load are mapped - exceptions raised inside the nested handlers (coverage/record/edr constructors, or a final `ProviderTypeError`) propagate (read; executed for the feature case: 500 `connection error (check logs)`).

Collection metadata (`gen_collection`, `pygeoapi/api/collection.py:55`): coverage/tile/EDR loads re-raise `ProviderConnectionError` and treat `ProviderTypeError` as "absent" (`pygeoapi/api/collection.py:308`-`pygeoapi/api/collection.py:311`, `pygeoapi/api/collection.py:334`-`pygeoapi/api/collection.py:337`, `pygeoapi/api/collection.py:399`-`pygeoapi/api/collection.py:402`); the map load catches only `ProviderTypeError` (`pygeoapi/api/collection.py:356`). `describe_collections` wraps each collection in `except Exception`: in `/collections` the failing collection is silently skipped, for `/collections/{id}` it returns 500 `Error generating collection` (`pygeoapi/api/__init__.py:952`-`pygeoapi/api/__init__.py:962`). **Feature/record providers are not instantiated for collection metadata at all** (executed: a feature provider whose constructor raises still appears in `/collections` and `/collections/{id}` returns 200).

EDR, coverage, map, tile catch sites: see sections 7-9.

---

## 4. CRS handling: `@crs_transform`, `crs`, `storage_crs`, `storage_crs_coordinate_epoch`, `always_xy`

- `crs_transform` is defined in `pygeoapi/crs.py:301`. It wraps a function returning a GeoJSON Feature or FeatureCollection dict. It reads **only the keyword argument** `crs_transform_spec` (`pygeoapi/crs.py:329`); if it is `None` the result is returned unchanged (`pygeoapi/crs.py:331`-`pygeoapi/crs.py:335`). Otherwise it builds a transformer from the spec (`get_transform_from_spec`, `pygeoapi/crs.py:260`) and rewrites each feature's `geometry` in place via shapely (`pygeoapi/crs.py:343`-`pygeoapi/crs.py:352`, `crs_transform_feature` at `pygeoapi/crs.py:357`; `None` geometries are skipped at `pygeoapi/crs.py:366`-`pygeoapi/crs.py:367`). If `result.get('features')` is `None` the result is treated as a single Feature (`pygeoapi/crs.py:343`-`pygeoapi/crs.py:347`). It transforms geometries only: `bbox` members or coordinates in properties are untouched (read). If a decorated `get()` returns `None` while a spec is set, `result.get` raises `AttributeError` (read) - another reason `get()` must raise `ProviderItemNotFoundError` instead of returning `None`.
- Usage by providers: decorate `query` and `get` (e.g. `pygeoapi/provider/geojson.py:165`, `pygeoapi/provider/geojson.py:202`) and accept `**kwargs` so `crs_transform_spec` is accepted; the decorator still passes it through to the wrapped function (`pygeoapi/crs.py:330`) (read). Providers that transform natively (SQL, OGR, ...) instead consume `crs_transform_spec` themselves (read; out of scope).
- `CrsTransformSpec` dataclass: `source_crs_uri`, `source_crs_wkt`, `target_crs_uri`, `target_crs_wkt`, `always_xy=False` (`pygeoapi/crs.py:64`-`pygeoapi/crs.py:70`).
- `create_crs_transform_spec(provider_def, query_crs_uri)` (`pygeoapi/crs.py:535`), called by the API for features only (`pygeoapi/api/itemtypes.py:371`-`pygeoapi/api/itemtypes.py:379`, `pygeoapi/api/itemtypes.py:913`-`pygeoapi/api/itemtypes.py:921`) and for EDR only when `crs` is in the request (`pygeoapi/api/environmental_data_retrieval.py:313`-`pygeoapi/api/environmental_data_retrieval.py:319`):
  - reads `always_xy` (default False) and `storage_crs` (default CRS84) from provider_def (`pygeoapi/crs.py:556`-`pygeoapi/crs.py:558`);
  - no `crs` param -> target = `storage_crs` if it is CRS84/CRS84h, else CRS84 (`pygeoapi/crs.py:560`-`pygeoapi/crs.py:566`);
  - target must be in `get_supported_crs_list(provider_def)` = the config `crs:` list plus CRS84 appended if no default is present (`pygeoapi/crs.py:111`-`pygeoapi/crs.py:120`) - note this `append` mutates the provider_def's own `crs` list in place (read); otherwise `ValueError` (`pygeoapi/crs.py:571`-`pygeoapi/crs.py:576`) which the API maps to 400 `InvalidParameterValue` (`pygeoapi/api/itemtypes.py:380`-`pygeoapi/api/itemtypes.py:384`) (executed);
  - returns `None` when storage CRS equals target (`pygeoapi/crs.py:581`-`pygeoapi/crs.py:583`), else a spec carrying `always_xy` (`pygeoapi/crs.py:588`-`pygeoapi/crs.py:594`).
- `always_xy` is passed to `pyproj.Transformer.from_crs(..., always_xy=always_xy)` (`pygeoapi/crs.py:295`-`pygeoapi/crs.py:296`). With the default `False`, CRS84 -> EPSG:4326 swaps to lat/lon order (executed: `[3.0, 1.5]` -> `[1.5, 3.0]`).
- `Content-Crs` header is set by the API, not the provider (`pygeoapi/crs.py:597`, `pygeoapi/crs.py:621`; called at `pygeoapi/api/itemtypes.py:385`) (executed: `<http://www.opengis.net/def/crs/EPSG/0/4326>`).
- `bbox` is transformed by the API to `storage_crs` before `query()` when `bbox-crs` (default CRS84) differs (`pygeoapi/api/itemtypes.py:415`-`pygeoapi/api/itemtypes.py:421`), so providers receive bbox in storage CRS (read). CQL2 filter geometries are transformed to `storage_crs` and the generic `geometry` attribute is renamed to `geom_field` by `modify_pygeofilter` (`pygeoapi/crs.py:409`, `pygeoapi/api/itemtypes.py:495`-`pygeoapi/api/itemtypes.py:500`) (read).
- `storage_crs_coordinate_epoch` is **only** echoed into collection metadata as `storageCrsCoordinateEpoch` for `edr`/`feature` collections (`pygeoapi/api/collection.py:273`-`pygeoapi/api/collection.py:277`); it is not used in any transformation (read; `grep` finds no other use in `pygeoapi/`). It is documented at `docs/source/crs.rst:52` and `docs/source/configuration.rst:287`.
- `BaseProvider` stores only `storage_crs` (as `pyproj.CRS`, `pygeoapi/provider/base.py:86`-`pygeoapi/provider/base.py:87`); `crs`, `always_xy`, `storage_crs_coordinate_epoch` are never provider attributes, and `self.crs` is an unrelated coverage attribute (`pygeoapi/provider/base.py:91`) (read).

---

## 5. Features/records contract as the API uses it

### 5.1 Exact kwargs passed to `query()` (`pygeoapi/api/itemtypes.py:557`-`pygeoapi/api/itemtypes.py:563`)

Always all 13, always as keywords (executed with a spy provider):

| kwarg | Value / parsing | Citation |
|---|---|---|
| `offset` | `int(offset)`; negative -> 400; absent -> `0` | `pygeoapi/api/itemtypes.py:270`-`pygeoapi/api/itemtypes.py:284` |
| `limit` | `evaluate_limit(...)`: absent -> `default_items` (default 10); `<=0` / non-int -> 400; above `max_items` (default 10) -> throttled to max unless `on_exceed: error` (then 400); collection `limits` override server `limits` | `pygeoapi/api/itemtypes.py:293`-`pygeoapi/api/itemtypes.py:299`, `pygeoapi/api/__init__.py:1305`-`pygeoapi/api/__init__.py:1334` |
| `resulttype` | raw string, `'results'` if absent/empty; not validated | `pygeoapi/api/itemtypes.py:301` |
| `bbox` | `[]` or list of 4/6 floats (`validate_bbox`), transformed to storage CRS | `pygeoapi/api/itemtypes.py:305`-`pygeoapi/api/itemtypes.py:316`, `pygeoapi/api/__init__.py:1113` |
| `datetime_` | `None` or string; validated/normalised (`/x` -> `../x`) only if the collection has `extents.temporal`; out-of-range -> 400 | `pygeoapi/api/itemtypes.py:319`-`pygeoapi/api/itemtypes.py:327`, `pygeoapi/api/__init__.py:1181`, `pygeoapi/api/__init__.py:1205`-`pygeoapi/api/__init__.py:1206` |
| `properties` | list of `(name, value)` tuples of non-reserved query params whose name is in `p.fields` (or any, if `include_extra_query_parameters`); unknown names silently dropped | `pygeoapi/api/itemtypes.py:254`-`pygeoapi/api/itemtypes.py:257`, `pygeoapi/api/itemtypes.py:428`-`pygeoapi/api/itemtypes.py:438` |
| `sortby` | list of `{'property': p, 'order': '+'|'-'}`; property not in `p.fields` -> 400 `bad sortby property` | `pygeoapi/api/itemtypes.py:441`-`pygeoapi/api/itemtypes.py:461` |
| `select_properties` | list from `properties=a,b`; names not in `p.properties | p.fields` -> 400 | `pygeoapi/api/itemtypes.py:464`-`pygeoapi/api/itemtypes.py:477` |
| `skip_geometry` | `str2bool(skipGeometry)`, default False | `pygeoapi/api/itemtypes.py:480`-`pygeoapi/api/itemtypes.py:484` |
| `crs_transform_spec` | `CrsTransformSpec` or `None` (features only; always `None` for records) | `pygeoapi/api/itemtypes.py:370`-`pygeoapi/api/itemtypes.py:379` |
| `q` | raw string or `None` | `pygeoapi/api/itemtypes.py:330` |
| `language` | `babel.Locale` or `None` from provider_def `languages`/`language` (`l10n.get_plugin_locale`) | `pygeoapi/api/itemtypes.py:535`, `pygeoapi/l10n.py:453` |
| `filterq` | `None` or a **pygeofilter AST node** parsed from `filter=` (CQL2 text) or from the request body (CQL2 JSON) after `modify_pygeofilter`; bad CQL -> 400 (executed: `Equal(lhs=ATTRIBUTE name, rhs='n1')`) | `pygeoapi/api/itemtypes.py:490`-`pygeoapi/api/itemtypes.py:524` |

Not passed: `geometry_column_name`, `filter-lang` (validated against `[None, 'cql-json', 'cql-text', 'cql2-text', 'cql2-json']` then discarded, `pygeoapi/api/itemtypes.py:527`-`pygeoapi/api/itemtypes.py:533`), `filter-crs` (used only in `modify_pygeofilter`), `bbox-crs`, `f`, `lang` (read; executed spy shows exactly the 13 keys). Reserved names never forwarded as `properties`: `bbox, bbox-crs, crs, f, lang, limit, offset, resulttype, datetime, sortby, properties, skipGeometry, q, filter, filter-lang, filter-crs` (`pygeoapi/api/itemtypes.py:254`-`pygeoapi/api/itemtypes.py:257`).

A provider signature compatible with the API is therefore `def query(self, offset=0, limit=10, resulttype='results', bbox=[], datetime_=None, properties=[], sortby=[], select_properties=[], skip_geometry=False, q=None, **kwargs)` - exactly what the core GeoJSON/CSV providers use (`pygeoapi/provider/geojson.py:166`-`pygeoapi/provider/geojson.py:168`, `pygeoapi/provider/csv_.py:233`-`pygeoapi/provider/csv_.py:235`), with `crs_transform_spec`, `language`, `filterq` arriving in `**kwargs`.

### 5.2 What `query()` must return and what the API adds

- A dict; the API does `content['links']` (creates it if absent) (`pygeoapi/api/itemtypes.py:577`-`pygeoapi/api/itemtypes.py:578`) and reads `content['features']` (`pygeoapi/api/itemtypes.py:615`).
- The **provider** must supply `type: FeatureCollection`, `features`, and (optionally) `numberMatched` and `numberReturned`; the API never computes either count (read: no `numberReturned` assignment in `pygeoapi/api/`). Core providers set `numberMatched` only when `self.count` or hits (`pygeoapi/provider/geojson.py:191`-`pygeoapi/provider/geojson.py:192`) and `numberReturned = len(features)` (`pygeoapi/provider/geojson.py:198`) (executed: GeoJSON with `count: false` returns keys `type, features, numberReturned`).
- The **API** appends links `self`/`alternate` (GeoJSON, JSON-LD, HTML, plus each dataset formatter) (`pygeoapi/api/itemtypes.py:582`-`pygeoapi/api/itemtypes.py:605`), `prev`/`next` (below), `collection` (`pygeoapi/api/itemtypes.py:648`-`pygeoapi/api/itemtypes.py:655`) and overwrites `timeStamp` with the current UTC time (`pygeoapi/api/itemtypes.py:657`-`pygeoapi/api/itemtypes.py:658`) (executed: output keys `type, numberMatched, features, numberReturned, links, timeStamp`).
- Pagination: if any provider link already has `rel: next`, the API adds **neither next nor prev** (`pygeoapi/api/itemtypes.py:610`-`pygeoapi/api/itemtypes.py:619`) (executed). Otherwise `next` if `numberMatched > limit + offset`, else if `len(features) == limit`; `prev` if `offset > 0`, with `prev = max(0, offset - limit)` (`pygeoapi/api/itemtypes.py:613`-`pygeoapi/api/itemtypes.py:622`, `pygeoapi/api/itemtypes.py:636`) (executed: offset=3, limit=2 -> prev offset=1, next offset=5).
- `resulttype=hits` is **not handled by the API** beyond passing the string: the provider decides. Core providers return `features: []` plus `numberMatched` (executed: GeoJSON/CSV `['type', 'features', 'numberMatched']`). Consequences (executed): (a) a hits response still gets a `next` link when `numberMatched > limit + offset`; (b) a hits response **without** a `features` key crashes with `KeyError: 'features'` at `pygeoapi/api/itemtypes.py:615` whenever `numberMatched <= limit + offset` (e.g. `limit=50`).
- HTML rendering reads `p.type`, `p.id_field`, `p.uri_field`, `p.title_field` (`pygeoapi/api/itemtypes.py:669`-`pygeoapi/api/itemtypes.py:683`); dataset formatters read `p.filename` for `Content-Disposition` (`pygeoapi/api/itemtypes.py:710`-`pygeoapi/api/itemtypes.py:714`); JSON-LD uses `p.uri_field or 'id'` (`pygeoapi/api/itemtypes.py:722`-`pygeoapi/api/itemtypes.py:724`) (read).

### 5.3 `get()` contract (`pygeoapi/api/itemtypes.py:934`-`pygeoapi/api/itemtypes.py:938`)

- Called as `p.get(identifier, language=prv_locale, crs_transform_spec=crs_transform_spec)` (executed: kwargs `identifier`(str), `language`, `crs_transform_spec`). `identifier` is the raw path segment (always `str`).
- Must return a GeoJSON Feature dict with `properties` (the API reads `content['properties'].get(p.uri_field)` when `uri_field` is set, `pygeoapi/api/itemtypes.py:949`-`pygeoapi/api/itemtypes.py:950`). Unknown id: raise `ProviderItemNotFoundError` (-> 404); returning `None` yields 400 (section 3.3).
- Optional top-level `prev`/`next` members (identifiers) become item navigation links (and remain in the body) (`pygeoapi/api/itemtypes.py:991`-`pygeoapi/api/itemtypes.py:1002`) (executed).
- API adds links `root`x2, `self`, `alternate`x2, `collection` (`pygeoapi/api/itemtypes.py:955`-`pygeoapi/api/itemtypes.py:986`) (executed).

### 5.4 Transactions (`manage_collection_item`, `pygeoapi/api/itemtypes.py:731`)

- Gate: `p.editable` truthy, else 400 `Collection is not editable` (`pygeoapi/api/itemtypes.py:788`-`pygeoapi/api/itemtypes.py:792`); empty body -> 400 (`pygeoapi/api/itemtypes.py:794`-`pygeoapi/api/itemtypes.py:798`). `OPTIONS` returns `Allow: HEAD, GET` + `POST` (collection) or `PUT, DELETE` (item) when editable (`pygeoapi/api/itemtypes.py:779`-`pygeoapi/api/itemtypes.py:786`) (executed).
- `create(bytes)` -> must return the new identifier, used for `Location: .../items/{identifier}`; 201 (`pygeoapi/api/itemtypes.py:804`-`pygeoapi/api/itemtypes.py:817`) (executed). `update(identifier, bytes)` -> 204 (`pygeoapi/api/itemtypes.py:823`-`pygeoapi/api/itemtypes.py:834`). `delete(identifier)` is preceded by `get(identifier)` (unknown -> 404) and returns 200 (`pygeoapi/api/itemtypes.py:839`-`pygeoapi/api/itemtypes.py:852`) (executed). Return values of `update`/`delete` are ignored.
- Flask routes `POST /items` with `Content-Type: application/geo+json` to create, any other POST to a CQL2-JSON search (`pygeoapi/flask_app.py:293`-`pygeoapi/flask_app.py:303`) (read).

### 5.5 Provider attributes the features/records API reads

`fields` (via the property -> `_fields`), `properties`, `id_field`, `time_field`, `uri_field`, `title_field`, `type`, `editable`, `filename`, `include_extra_query_parameters` (citations above and `pygeoapi/api/itemtypes.py:150`, `pygeoapi/api/itemtypes.py:209`-`pygeoapi/api/itemtypes.py:212`, `pygeoapi/api/itemtypes.py:431`, `pygeoapi/api/itemtypes.py:1179`, `pygeoapi/api/itemtypes.py:1254`). The API never calls `get_fields()` for features/records - only the `fields` property (read; `grep` in section 6.1).

---

## 6. `get_fields()` / `fields`: where consumed and the required shape

### 6.1 Consumers (all read, several executed)

| Consumer | Uses | Citation |
|---|---|---|
| queryables | `p.fields` truthy -> adds `geometry`; per field `{'title': k, 'type': v['type']}` (`float`->`number`), `format` if not None, `values` -> `enum`, `x-ogc-role` `id`/`primary-instant` from `id_field`/`time_field`; `profile=actual-domain` -> `get_domains()` enums; `p.properties` filters | `pygeoapi/api/itemtypes.py:174`-`pygeoapi/api/itemtypes.py:214` |
| `/schema` | copies each field dict **as-is** (`schema['properties'][k] = v`, same object), `float`->`number`, drops `format: None`, adds `x-ogc-role`; geometry added unless `p.type == 'coverage'` | `pygeoapi/api/__init__.py:1072`-`pygeoapi/api/__init__.py:1092` |
| items query params | property filters, `sortby` validation, `properties` validation | `pygeoapi/api/itemtypes.py:431`, `pygeoapi/api/itemtypes.py:453`, `pygeoapi/api/itemtypes.py:468` |
| OpenAPI (features/records) | `properties` enum = `list(p.fields.keys())`; `/schema` and `/queryables` paths only if `p.fields`; one query parameter per field with `schema = type_` (the field dict itself), `schema.get('format')` | `pygeoapi/api/itemtypes.py:1105`, `pygeoapi/api/itemtypes.py:1209`, `pygeoapi/api/itemtypes.py:1258`-`pygeoapi/api/itemtypes.py:1297` |
| coverage | `properties` validation (`a not in p.fields`), OpenAPI enum | `pygeoapi/api/coverages.py:160`, `pygeoapi/api/coverages.py:273` |
| EDR | `get_parameters()` via `self.fields` (collection metadata); `parameter-name` validation via **`p.get_fields()`** | `pygeoapi/provider/base_edr.py:123`, `pygeoapi/api/environmental_data_retrieval.py:395`-`pygeoapi/api/environmental_data_retrieval.py:396` |
| maps | `properties` validation via `p.get_fields()` (NotImplementedError -> 501); OpenAPI enum via `mp.fields` | `pygeoapi/api/maps.py:220`-`pygeoapi/api/maps.py:226`, `pygeoapi/api/maps.py:360` |
| collection metadata (map) | `if p._fields:` adds schema links | `pygeoapi/api/collection.py:375` |

**Consequence 1 - `_fields` must be populated in the constructor.** Because `fields` returns `self._fields` (section 2.2), a provider whose `get_fields()` returns a dict but never stores it gets (executed, `UncachedFieldsProvider`): empty queryables (`properties: {}`), property filters silently dropped (`properties=[]` passed to `query`), `sortby=<field>` -> 400, `properties=<field>` -> 400, and no `/schema`/`/queryables` paths in OpenAPI. Every core feature provider calls `self.get_fields()` in `__init__` and caches into `self._fields` (e.g. `pygeoapi/provider/geojson.py:73`, `pygeoapi/provider/geojson.py:82`, `pygeoapi/provider/csv_.py:63`, `pygeoapi/provider/csv_.py:71`, `pygeoapi/provider/sql.py:143`, `pygeoapi/provider/sql.py:287`).

**Consequence 2 - values must be dicts with a `type` key.** With the docs-style `{'name': 'string'}` (executed, `StringFieldsProvider`): `/queryables` -> uncaught `TypeError: string indices must be integers` at `pygeoapi/api/itemtypes.py:200`; `/schema` -> same at `pygeoapi/api/__init__.py:1084`; OpenAPI -> `AttributeError: 'str' object has no attribute 'get'` at `pygeoapi/api/itemtypes.py:1286`; `/items` still works. The `type_ == 'date'/'float'/'long'` string branches in OpenAPI (`pygeoapi/api/itemtypes.py:1268`-`pygeoapi/api/itemtypes.py:1282`) are dead legacy code for the old string shape: they compare a dict to a string, so a legacy string value falls through to `schema = type_` and crashes on `.get` (read + executed).

**Consequence 3 - OpenAPI blast radius.** `openapi.get_oas_30` wraps each API module in `except Exception` and re-raises when `fail_on_invalid_collection` (default True; CLI `--fail-on-invalid-collection/--no-fail-on-invalid-collection`) (`pygeoapi/openapi.py:550`-`pygeoapi/openapi.py:566`, `pygeoapi/openapi.py:1096`-`pygeoapi/openapi.py:1097`). With False, one bad provider drops **all** paths contributed by that module (e.g. every collection's `/items` paths), not just its own (executed: only `/collections/strfields` survived).

### 6.2 Shapes produced by core providers (all `{name: {...}}` dicts)

`{'type': ...}` only: CSV (`pygeoapi/provider/csv_.py:93`), GeoJSON (`pygeoapi/provider/geojson.py:95`), ESRI with raw ESRI type names like `esriFieldTypeString` (`pygeoapi/provider/esri.py:117`), MongoDB always `string` (`pygeoapi/provider/mongo.py:94`), SensorThings (`pygeoapi/provider/sensorthings.py:121`, `pygeoapi/provider/sensorthings.py:123`), Socrata with native type names (`pygeoapi/provider/socrata.py:91`), SQLite (`pygeoapi/provider/sqlite.py:104`), Oracle with DB type names (`pygeoapi/provider/oracle.py:281`), ERDDAP with Python type names (`pygeoapi/provider/erddap.py:81` - note this assignment is outside its `for` loop, so only the last field is kept). `{'type', 'format'}`: Elasticsearch (`pygeoapi/provider/elasticsearch_.py:144`-`pygeoapi/provider/elasticsearch_.py:150`), OpenSearch (`pygeoapi/provider/opensearch_.py:122`-`pygeoapi/provider/opensearch_.py:129`), OGR (`pygeoapi/provider/ogr.py:258`-`pygeoapi/provider/ogr.py:264`), Parquet (`pygeoapi/provider/parquet.py:309`-`pygeoapi/provider/parquet.py:314`), SQL with `format: None` possible (`pygeoapi/provider/sql.py:293`-`pygeoapi/provider/sql.py:298`), TinyDB (`pygeoapi/provider/tinydb_.py:108`-`pygeoapi/provider/tinydb_.py:116`), CSW facade (`pygeoapi/provider/csw_facade.py:88`-`pygeoapi/provider/csw_facade.py:91`). `{'type', 'title', 'x-ogc-unit'}`: xarray (skips variables without `units`, `pygeoapi/provider/xarray_.py:130`-`pygeoapi/provider/xarray_.py:139`), SensorThingsEDR (`pygeoapi/provider/sensorthings_edr.py:95`-`pygeoapi/provider/sensorthings_edr.py:99`); rasterio adds `_meta` and `x-ogc-unit` only when known (`pygeoapi/provider/rasterio_.py:89`-`pygeoapi/provider/rasterio_.py:95`). Outlier: MVT-postgresql stores SQLAlchemy column objects in `_fields` (`pygeoapi/provider/mvt_postgresql.py:88`-`pygeoapi/provider/mvt_postgresql.py:92`) - tile providers' fields never reach the API (read). Non-JSON-Schema `type` values (`float`, `datetime`, ESRI/Oracle/Socrata names) are passed through; only `float` is normalised (to `number`) by queryables/schema (read).

Executed on fixtures: GeoJSON `tests/data/ne_110m_lakes.geojson` -> `{"id": {"type": "integer"}, "scalerank": {"type": "integer"}, "name": {"type": "string"}, "name_alt": {"type": "string"}, "admin": {"type": "string"}, "featureclass": {"type": "string"}}`; CSV `tests/data/obs.csv` -> `{"id": {"type": "string"}, "stn_id": {"type": "integer"}, "datetime": {"type": "string"}, "value": {"type": "number"}}`.

### 6.3 Per-field keys used anywhere in the API layer

`type` (required, all consumers), `format` (queryables, schema, OpenAPI), `values` (queryables -> `enum`, `pygeoapi/api/itemtypes.py:206`-`pygeoapi/api/itemtypes.py:207`), `title` (EDR `get_parameters` only, `pygeoapi/provider/base_edr.py:128`; queryables ignores it and uses the key as title, `pygeoapi/api/itemtypes.py:199`), `x-ogc-unit` (EDR, required there, `pygeoapi/provider/base_edr.py:139`), `description` (EDR, `pygeoapi/provider/base_edr.py:146`). `/schema` and the OpenAPI per-field query parameter echo the whole dict, so any extra key (e.g. `_meta`, `values`, `title`) appears there verbatim (executed: OpenAPI param schema `{'type': 'number', 'values': [1.5, 3.0]}`). `x-ogc-role` is added by the API, never read from providers. No other key (e.g. `x-ogc-definition`, `minimum`) is read (read; `grep x-ogc` in `pygeoapi/api`).

---

## 7. EDR (`pygeoapi/provider/base_edr.py`)

### 7.1 Query-type registration

`EDR_QUERY_TYPES = ['position', 'radius', 'area', 'cube', 'trajectory', 'corridor', 'items', 'locations', 'instances']` (`pygeoapi/provider/base_edr.py:37`-`pygeoapi/provider/base_edr.py:39`); class attribute `query_types = []` (`pygeoapi/provider/base_edr.py:45`). `__init_subclass__` (`pygeoapi/provider/base_edr.py:58`) sets `cls.query_types` to the names in `cls.__dict__` that are in `EDR_QUERY_TYPES` and `callable` (`pygeoapi/provider/base_edr.py:61`-`pygeoapi/provider/base_edr.py:64`) and raises `ProviderInvalidDataError('<cls> does not implement any query types')` if empty (`pygeoapi/provider/base_edr.py:66`-`pygeoapi/provider/base_edr.py:69`); registering `items` only logs a warning (`pygeoapi/provider/base_edr.py:75`-`pygeoapi/provider/base_edr.py:79`). Confirmed (executed, `exp2_edr.py`):

| Class shape | Result |
|---|---|
| subclass with no query methods | raises at **class definition** (`ProviderInvalidDataError`) |
| subclass defining `position` | `query_types == ['position']` |
| grandchild (subclass of the above) defining nothing | raises at class definition |
| grandchild defining only `area` | `query_types == ['area']` - inherited `position` is no longer advertised, although `query(query_type='position')` still dispatches |
| grandchild re-declaring `def position(self, **kw): return super().position(**kw)` | `['position']` |
| mixin engine (`class Engine: def position...`) + `class Generated(Engine, BaseEDRProvider): pass` | raises |
| same, with `position = Engine.position` in the class body | `['position']` |
| only `instances()`/`instance()` defined | `['instances']` (valid provider!) |
| only `items()` | `['items']` |
| method attached after class creation | not registered |
| `@staticmethod position` | registered (staticmethod objects are callable in 3.10+) |
| `@classmethod position` | **not** registered (classmethod objects are not callable in 3.12) |
| attribute `position = 'not callable'` | not registered |

Consequences for a shared engine class + generated subclass design:
- Every generated EDR class body must itself (re)bind each query method it wants advertised (`position = SpecEngine.position` works), otherwise the module fails at import time with `ProviderInvalidDataError` raised from inside `importlib.import_module` in `load_plugin` (`pygeoapi/plugin.py:131`).
- Alternative (executed in exp4, see 7.4): the engine class defines its own `__init_subclass__` that computes `query_types` over the MRO (optionally filtered by a class attribute), so generated subclasses can have an empty body. The engine class itself still passes through `BaseEDRProvider.__init_subclass__` once, so it must define at least one query method in its own body.
- `query_types` is also what the API uses to route/validate requests (section 7.3), so shadowing a parent's list silently disables inherited query types.

### 7.2 Other `BaseEDRProvider` methods

- `__init__(self, provider_def)` just calls `BaseProvider.__init__` (`pygeoapi/provider/base_edr.py:47`-`pygeoapi/provider/base_edr.py:56`).
- `instances(self)` and `instance(self, instance)` **return** (not raise) `NotImplementedError()` (`pygeoapi/provider/base_edr.py:81`-`pygeoapi/provider/base_edr.py:88`, `pygeoapi/provider/base_edr.py:90`-`pygeoapi/provider/base_edr.py:97`); the returned exception object is truthy (executed), so the API treats every instance id as valid unless `instance()` is overridden.
- `get_query_types(self)` returns `self.query_types` (`pygeoapi/provider/base_edr.py:99`-`pygeoapi/provider/base_edr.py:106`).
- `get_parameters(self, parameters: set | list = [], as_list=False) -> dict | list` (`pygeoapi/provider/base_edr.py:108`-`pygeoapi/provider/base_edr.py:110`): iterates `self.fields` (not `get_fields()`, `pygeoapi/provider/base_edr.py:123`, `pygeoapi/provider/base_edr.py:127`); per field builds a CoverageJSON Parameter with `name`/`observedProperty.label.en` from `title` (may be `None`), `unit.symbol.value` from **`x-ogc-unit` (KeyError if missing)**, fixed `unit.symbol.type = 'http://www.opengis.net/def/uom/UCUM/'`, optional `observedProperty.description.en` from `description` (`pygeoapi/provider/base_edr.py:128`-`pygeoapi/provider/base_edr.py:152`). Executed: missing `x-ogc-unit` -> `KeyError: 'x-ogc-unit'`; missing `title` -> `name: None`; a string-valued field -> `AttributeError`; unknown name in `parameters` -> `KeyError`; a provider that only returns fields from `get_fields()` (no `_fields`) -> `{}`.
- `query(self, **kwargs)` dispatches to `getattr(self, kwargs['query_type'])(**kwargs)`; missing `query_type` -> `ProviderQueryError('Query type is required')`; unknown attribute -> `ProviderQueryError('Query type not implemented')` (`pygeoapi/provider/base_edr.py:159`-`pygeoapi/provider/base_edr.py:186`) (executed). The **full kwargs dict, including `query_type`,** is forwarded, so query methods must accept `**kwargs`.

### 7.3 How the API calls EDR providers (`pygeoapi/api/environmental_data_retrieval.py`)

Routing (Flask): only `position`, `area`, `cube`, `radius`, `trajectory`, `corridor`, `locations[/<id>]` and `instances[/<id>[/<qt>]]` are routed (`pygeoapi/flask_app.py:484`-`pygeoapi/flask_app.py:501`); the query type is the last path segment (`pygeoapi/flask_app.py:526`). A registered `items` query type is never routed to the EDR provider (`/items` belongs to the Features routes), hence the warning at `pygeoapi/provider/base_edr.py:75`-`pygeoapi/provider/base_edr.py:79` (read).

`get_collection_edr_query` (`pygeoapi/api/environmental_data_retrieval.py:249`), in order:

1. Load the `edr` provider; `ProviderGenericError` -> mapped (`pygeoapi/api/environmental_data_retrieval.py:277`-`pygeoapi/api/environmental_data_retrieval.py:284`). An import-time `ProviderInvalidDataError` (section 7.1) therefore yields 500 `generic error (check logs)` (executed).
2. `if instance is not None and not p.instance(instance)` -> 400 `Invalid instance identifier` (`pygeoapi/api/environmental_data_retrieval.py:286`-`pygeoapi/api/environmental_data_retrieval.py:290`); not wrapped in `try`, so an exception from `instance()` propagates (executed).
3. `query_type not in p.get_query_types()` -> 400 `Unsupported query type` (`pygeoapi/api/environmental_data_retrieval.py:292`-`pygeoapi/api/environmental_data_retrieval.py:296`) (executed).
4. `crs` handled only if present in the request (`pygeoapi/api/environmental_data_retrieval.py:313`-`pygeoapi/api/environmental_data_retrieval.py:325`); `datetime` validated as for features (`pygeoapi/api/environmental_data_retrieval.py:330`-`pygeoapi/api/environmental_data_retrieval.py:338`).
5. `parameter-name` split on commas (`pygeoapi/api/environmental_data_retrieval.py:341`-`pygeoapi/api/environmental_data_retrieval.py:343`) and validated with `any(fld in parameternames for fld in p.get_fields().keys())` (`pygeoapi/api/environmental_data_retrieval.py:395`-`pygeoapi/api/environmental_data_retrieval.py:400`): **`get_fields()` must be implemented** (the base raises `NotImplementedError`), and a list is rejected only if *none* of its names is known (executed: `t,nope` accepted, `nope` -> 400).
6. `bbox` parsed only for `cube`/`locations`; `cube` without bbox -> 400 (`pygeoapi/api/environmental_data_retrieval.py:345`-`pygeoapi/api/environmental_data_retrieval.py:355`) (executed). `coords` parsed with `shapely.wkt.loads`; required except for `cube`/`locations` (`pygeoapi/api/environmental_data_retrieval.py:358`-`pygeoapi/api/environmental_data_retrieval.py:372`) (executed). `within`/`within-units` only for radius, corridor params only for corridor, `z` via `get_typed_value` (`pygeoapi/api/environmental_data_retrieval.py:374`-`pygeoapi/api/environmental_data_retrieval.py:393`). `limit` via `evaluate_limit` (`pygeoapi/api/environmental_data_retrieval.py:408`-`pygeoapi/api/environmental_data_retrieval.py:415`).
7. `p.query(**query_args)` with exactly these 17 keys (`pygeoapi/api/environmental_data_retrieval.py:417`-`pygeoapi/api/environmental_data_retrieval.py:438`), forwarded unchanged to the query method (executed): `query_type`, `instance` (str or None), `format_` (`request.format`, None if no `f`), `datetime_`, `select_properties` (list of parameter names), `wkt` (shapely geometry or None), `z`, `bbox` (list or None), `within`, `within_units`, `corridor_width`, `width_units`, `corridor_height`, `height_units` (raw strings or None), `limit` (int), `location_id` (str or None), `crs_transform_spec`. `ProviderGenericError` -> mapped (`pygeoapi/api/environmental_data_retrieval.py:439`-`pygeoapi/api/environmental_data_retrieval.py:442`).
8. Output: for HTML the API adds keys to the returned dict (`pygeoapi/api/environmental_data_retrieval.py:456`-`pygeoapi/api/environmental_data_retrieval.py:476`); dataset formatters get `data` (`pygeoapi/api/environmental_data_retrieval.py:481`-`pygeoapi/api/environmental_data_retrieval.py:510`); otherwise `Content-Type: application/vnd.cov+json` and `to_json(data)` (`pygeoapi/api/environmental_data_retrieval.py:512`-`pygeoapi/api/environmental_data_retrieval.py:514`). So a query method must return a JSON-serialisable dict (CoverageJSON; GeoJSON for `locations` listings) (read).

`get_collection_edr_instances` (`pygeoapi/api/environmental_data_retrieval.py:73`): with an id, `if p.instance(instance_id): instances = [instance_id]`, `except ProviderItemNotFoundError` -> 404 (`pygeoapi/api/environmental_data_retrieval.py:114`-`pygeoapi/api/environmental_data_retrieval.py:121`); without, `instances = p.instances()` (`pygeoapi/api/environmental_data_retrieval.py:123`); each instance gets one `data_queries` entry per query type except `instances` (`pygeoapi/api/environmental_data_retrieval.py:152`-`pygeoapi/api/environmental_data_retrieval.py:167`). Executed failure modes:

| Provider behaviour | `/instances` | `/instances/{bad}` | `/instances/{bad}/position` |
|---|---|---|---|
| base `instances()`/`instance()` (not overridden) | uncaught `TypeError: 'NotImplementedError' object is not iterable` | 200 (any id accepted) | 200 (any id accepted) |
| `instance()` returns `False` for unknown | 200 | uncaught `UnboundLocalError` (`pygeoapi/api/environmental_data_retrieval.py:125`) | 400 `Invalid instance identifier` |
| `instance()` raises `ProviderItemNotFoundError` for unknown | 200 | 404 `Instance not found` | uncaught `ProviderItemNotFoundError` |

No provider behaviour gives a clean error on both instance endpoints in 0.24.0 (pygeoapi bug; see open questions).

Collection metadata: EDR collections get `parameter_names = p.get_parameters()` (`pygeoapi/api/collection.py:408`) and one `data_queries` entry plus JSON/HTML (and formatter) `data` links per `p.get_query_types()` (`pygeoapi/api/collection.py:410`-`pygeoapi/api/collection.py:450`), plus `crs`/`storageCrs` (`pygeoapi/api/collection.py:273`-`pygeoapi/api/collection.py:277`) (executed: `data_queries` = `['position', 'cube', 'locations']`; with `instances()` defined -> `['position', 'instances']`). A `get_parameters()` failure (e.g. a field without `x-ogc-unit`) makes `/collections/{id}` return 500 and silently drops the collection from `/collections` (executed).

OpenAPI: each EDR collection's provider is instantiated (`pygeoapi/api/environmental_data_retrieval.py:547`); one path per query type except `locations` (`pygeoapi/api/environmental_data_retrieval.py:551`-`pygeoapi/api/environmental_data_retrieval.py:556`), per-instance variants when `instances` is registered (`pygeoapi/api/environmental_data_retrieval.py:557`-`pygeoapi/api/environmental_data_retrieval.py:562`), `/instances` paths (`pygeoapi/api/environmental_data_retrieval.py:625`-`pygeoapi/api/environmental_data_retrieval.py:657`) and `/locations` paths (`pygeoapi/api/environmental_data_retrieval.py:658`) (executed for `position`+`instances`: `/position`, `/instances`, `/instances/{instanceId}`, `/instances/{instanceId}/position`).

### 7.4 Engine-class pattern that works (executed)

```python
class SpecEngineEDR(BaseEDRProvider):
    def position(self, **kwargs): ...        # >=1 query method in the engine's own body
    def area(self, **kwargs): ...

    def __init_subclass__(cls, **kwargs):     # replaces BaseEDRProvider's check for descendants
        enabled = getattr(cls, 'ENABLED_QUERY_TYPES', None)
        found = [q for q in EDR_QUERY_TYPES
                 if any(q in k.__dict__ and callable(k.__dict__[q])
                        for k in cls.__mro__ if k is not BaseEDRProvider)]
        cls.query_types = [q for q in found if enabled is None or q in enabled]

class GeneratedA(SpecEngineEDR): pass                      # -> ['position', 'area']
class GeneratedB(SpecEngineEDR): ENABLED_QUERY_TYPES = ['position']   # -> ['position']
```

Python resolves `__init_subclass__` on the parent, so the engine's hook runs for generated subclasses; `BaseEDRProvider.__init_subclass__` still runs once, for the engine class itself. Caveats: this depends on the 0.24 internals (`query_types` class attribute, `__init_subclass__`); `query()` still dispatches to any inherited method via `getattr`, but the API rejects types not in `query_types` (section 7.3 step 3). The simpler, internals-free alternative is to emit explicit re-binds in every generated class body (`position = SpecEngineEDR.position`).

---

## 8. Tiles and MVT base classes

### 8.1 `BaseTileProvider` (`pygeoapi/provider/tile.py:44`)

**Not a `BaseProvider` subclass** (executed: `issubclass(BaseTileProvider, BaseProvider) == False`), so none of the section 2.1 attributes (`type`, `fields`, `id_field`, `storage_crs`, ...) exist unless a subclass adds them.

`__init__(self, provider_def)` (`pygeoapi/provider/tile.py:47`): `name` (`pygeoapi/provider/tile.py:56`), `data` (`pygeoapi/provider/tile.py:57`), `format_type = provider_def['format']['name']` (`pygeoapi/provider/tile.py:58`), `mimetype = provider_def['format']['mimetype']` (`pygeoapi/provider/tile.py:59`), `options = provider_def.get('options')` (`pygeoapi/provider/tile.py:60`), `schemes = self.options.get('schemes', [])` (`pygeoapi/provider/tile.py:61`) -> **`options` is effectively required** (executed: without it `AttributeError: 'NoneType' object has no attribute 'get'`), `tile_type = None` (`pygeoapi/provider/tile.py:62`). `type` is not read.

| Method | Default | Citation |
|---|---|---|
| `get_layer(self)` | no body -> returns `None` | `pygeoapi/provider/tile.py:64` |
| `get_fields(self)` | `raise NotImplementedError()` | `pygeoapi/provider/tile.py:71`, `pygeoapi/provider/tile.py:78` |
| `get_tiling_schemes(self)` | `raise NotImplementedError()` | `pygeoapi/provider/tile.py:80`, `pygeoapi/provider/tile.py:87` |
| `get_tiles_service(self, baseurl, servicepath, dirpath, tile_type)` | `raise NotImplementedError()` | `pygeoapi/provider/tile.py:89`-`pygeoapi/provider/tile.py:90`, `pygeoapi/provider/tile.py:102` |
| `get_tiles(self, layer, tileset, z, y, x, format_)` | `raise NotImplementedError()`; returns tile bytes | `pygeoapi/provider/tile.py:104`, `pygeoapi/provider/tile.py:118` |
| `get_metadata(self)` | `raise NotImplementedError()` - **no parameters**, but the API calls it with keywords (8.3) | `pygeoapi/provider/tile.py:120`, `pygeoapi/provider/tile.py:128` |
| `is_in_limits(self, tilematrixset, z, x, y)` | int-casts (non-int -> False), checks `matrixWidth`/`matrixHeight` of `tileMatrices[z]` and `options['zoom']['min'] <= z <= options['zoom']['max']` | `pygeoapi/provider/tile.py:130`-`pygeoapi/provider/tile.py:152` |
| `get_tilematrixset(self, tileMatrixSetId)` | `TileMatrixSetEnum` value, or `None` (error only logged) | `pygeoapi/provider/tile.py:154`-`pygeoapi/provider/tile.py:175` |

Executed: `is_in_limits(WebMercatorQuad, z=2, x=3, y=3) -> True`, `z=6` (max 5) -> False, `x='a'` -> False; `get_tilematrixset('nope') -> None`. The API never calls `is_in_limits`; providers call it themselves (e.g. `pygeoapi/provider/mvt_postgresql.py:155`, `pygeoapi/provider/wmts_facade.py:182`) (read).

### 8.2 `BaseMVTProvider(BaseTileProvider)` (`pygeoapi/provider/base_mvt.py:43`)

- `tile_type = 'vector'` (`pygeoapi/provider/base_mvt.py:57`).
- `__repr__` raises `NotImplementedError` (`pygeoapi/provider/base_mvt.py:59`-`pygeoapi/provider/base_mvt.py:60`) - subclasses must override it (executed: `repr()` raises).
- `service_url` / `service_metadata_url` properties return `_service_url` / `_service_metadata_url` (`pygeoapi/provider/base_mvt.py:62`-`pygeoapi/provider/base_mvt.py:68`), which the base never sets.
- `get_layer` raises (`pygeoapi/provider/base_mvt.py:70`-`pygeoapi/provider/base_mvt.py:71`); `get_tiles(self, layer=None, tileset=None, z=None, y=None, x=None, format_=None)` raises (`pygeoapi/provider/base_mvt.py:110`-`pygeoapi/provider/base_mvt.py:125`).
- `get_tiling_schemes()` returns the `TileMatrixSetEnum` values named in `options.schemes`, else `[WorldCRS84Quad, WebMercatorQuad]` (`pygeoapi/provider/base_mvt.py:73`-`pygeoapi/provider/base_mvt.py:87`) (executed).
- `get_tiles_service(self, baseurl=None, servicepath=None, dirpath=None, tile_type=None)` computes a service path but **returns `None`** (`pygeoapi/provider/base_mvt.py:89`-`pygeoapi/provider/base_mvt.py:108`) (executed); subclasses must set `self._service_url` and return `self.get_tms_links()` as MVT-tippecanoe does (`pygeoapi/provider/mvt_tippecanoe.py:161`-`pygeoapi/provider/mvt_tippecanoe.py:165`). `get_tms_links()` builds `{'links': [self, item, describedby]}` from `service_url` (`pygeoapi/provider/base_mvt.py:224`-`pygeoapi/provider/base_mvt.py:255`).
- `get_metadata(self, dataset, server_url, layer=None, tileset=None, metadata_format=None, title=None, description=None, keywords=None, **kwargs)` dispatches on `metadata_format.upper()` to `get_default_metadata` (JSON, JSONLD), `get_vendor_metadata` (TileJSON) or `get_html_metadata` (HTML), all of which raise by default (`pygeoapi/provider/base_mvt.py:127`-`pygeoapi/provider/base_mvt.py:222`).

### 8.3 How the API uses tile providers (`pygeoapi/api/tiles.py`)

- `/tiles` (`pygeoapi/api/tiles.py:72`): load; `(KeyError, ProviderTypeError)` -> 400, `ProviderGenericError` -> mapped (`pygeoapi/api/tiles.py:94`-`pygeoapi/api/tiles.py:106`); `p.get_tiles_service(baseurl=..., servicepath=.../tiles/{tileMatrixSetId}/{tileMatrix}/{tileRow}/{tileCol}?f={p.format_type})` must return a dict with a `links` list (`pygeoapi/api/tiles.py:132`-`pygeoapi/api/tiles.py:138`) (executed: an MVT subclass without an override -> uncaught `TypeError` at `pygeoapi/api/tiles.py:137`); `p.get_tiling_schemes()` must yield objects with `tileMatrixSetURI`, `crs`, `tileMatrixSet` (`pygeoapi/api/tiles.py:140`-`pygeoapi/api/tiles.py:160`); `dataType` is `map` when the config mimetype starts with `image` (`pygeoapi/api/tiles.py:142`-`pygeoapi/api/tiles.py:145`); HTML reads `p.options['zoom']['min'/'max']` and `p.tile_type` (`pygeoapi/api/tiles.py:185`-`pygeoapi/api/tiles.py:188`).
- tile data (`pygeoapi/api/tiles.py:199`): `p.get_tiles(layer=p.get_layer(), tileset=matrix_id, z=z_idx, y=y_idx, x=x_idx, format_=p.format_type)` (`pygeoapi/api/tiles.py:240`-`pygeoapi/api/tiles.py:245`), with z/y/x as **strings** from the URL (`pygeoapi/flask_app.py:376`-`pygeoapi/flask_app.py:382`); `Content-Type` comes from the config `format.mimetype` (`pygeoapi/api/tiles.py:241`); `None` -> 204 (`pygeoapi/api/tiles.py:246`-`pygeoapi/api/tiles.py:249`); `KeyError` -> 400; `ProviderTileNotFoundError` -> 404 plain-text body; other `ProviderGenericError` -> mapped (`pygeoapi/api/tiles.py:254`-`pygeoapi/api/tiles.py:266`).
- tile metadata: `p.get_metadata(dataset=..., server_url=..., layer=p.get_layer(), tileset=matrix_id, metadata_format=request._format, title=..., description=..., language=prv_locale)` (`pygeoapi/api/tiles.py:318`-`pygeoapi/api/tiles.py:327`) - `keywords` is not passed and `language` is, so the method needs `**kwargs`; not wrapped in `try` (read).
- collection metadata: rel `tilesets-{p.tile_type}` (`pygeoapi/api/collection.py:343`), so a `BaseTileProvider` subclass must set `tile_type` (else `tilesets-None`); a default tile provider whose format is not png/jpeg is treated as vector tiles and also gets `itemType`/items links (`pygeoapi/api/collection.py:82`-`pygeoapi/api/collection.py:84`, `pygeoapi/api/collection.py:243`-`pygeoapi/api/collection.py:246`) (read). OpenAPI instantiates the tile provider and reads `tp.format_type` (`pygeoapi/api/tiles.py:469`, `pygeoapi/api/tiles.py:519`).

---

## 9. Coverage and map contracts at base-class level

Neither has a dedicated base class: both subclass `BaseProvider` (docs: `docs/source/plugins.rst:35`).

### 9.1 Coverage (`pygeoapi/api/coverages.py`)

- Required instance state: `self._fields` (range fields; `properties` validation `a not in p.fields`, `pygeoapi/api/coverages.py:160`), `self.axes` (subset axis names, `pygeoapi/api/coverages.py:176`), `self.filename` (optional download name, `pygeoapi/api/coverages.py:195`) and **`self._coverage_properties`** with `width`, `height`, `resx`, `resy` (optional `time_range`, `restime`, `uad`) read by collection metadata (`pygeoapi/api/collection.py:313`-`pygeoapi/api/collection.py:329`) - not declared anywhere on `BaseProvider` (executed: absent -> `/collections/{id}` 500; present -> `extent.spatial.grid` built).
- Config: `format: {name, mimetype}` is required - read unguarded at `pygeoapi/api/coverages.py:193` (executed: missing -> uncaught `KeyError: 'format'`).
- `query(**query_args)` (`pygeoapi/api/coverages.py:187`) receives always `bbox` (`[]` or floats), `datetime_`, `format_` (`request.format` or `json`; `json` also for HTML); plus `bbox_crs` (raw string, neither validated nor transformed), `properties` (list) and `subsets` (`{axis: [values]}`) only when requested (`pygeoapi/api/coverages.py:124`-`pygeoapi/api/coverages.py:182`) (executed: default `['bbox', 'datetime_', 'format_']`; full `['bbox', 'bbox_crs', 'datetime_', 'format_', 'properties', 'subsets']`). An invalid bbox returns **500** with code `InvalidParameterValue` (`pygeoapi/api/coverages.py:116`-`pygeoapi/api/coverages.py:122`) (executed).
- Return: native bytes when `format_` equals the config format name (`pygeoapi/api/coverages.py:194`-`pygeoapi/api/coverages.py:200`), else a CoverageJSON dict (`application/prs.coverage+json`, `pygeoapi/api/coverages.py:234`-`pygeoapi/api/coverages.py:236`); for HTML the API adds keys to the dict (`pygeoapi/api/coverages.py:213`-`pygeoapi/api/coverages.py:217`).
- Coverage providers also serve `/queryables` (probed at `pygeoapi/api/itemtypes.py:127`) and `/schema` without a geometry entry (`pygeoapi/api/__init__.py:1072`).

### 9.2 Map (`pygeoapi/api/maps.py`)

- `query(**query_args)` (`pygeoapi/api/maps.py:245`) receives (executed): `format_` (`f` param, default `png`), `style` (path segment or None), `crs` (storage_crs or `get_uri(crs)`), **`'bbox-crs'` (hyphenated key - only reachable through `**kwargs`)**, `transparent` (raw request string or `True`), `width`/`height` (ints, default 500/300), `bbox` (floats, default `[-180, -90, 180, 90]`, transformed to `crs` with `always_xy=True`), `datetime_`; plus `subsets` and `select_properties` when requested (`pygeoapi/api/maps.py:107`-`pygeoapi/api/maps.py:241`). `select_properties` validation calls `p.get_fields()`; `NotImplementedError` -> 501 (`pygeoapi/api/maps.py:218`-`pygeoapi/api/maps.py:226`) (executed). Returns image bytes; `Content-Type` from config `format.mimetype`, `Content-Crs`/`Content-Bbox` set by the API (`pygeoapi/api/maps.py:252`-`pygeoapi/api/maps.py:262`).
- Legend: `p.get_legend(style, request.params.get('f', 'png'))` (`pygeoapi/api/maps.py:311`), not declared on `BaseProvider`.
- Collection metadata needs config `format.mimetype`/`format.name` (`pygeoapi/api/collection.py:362`-`pygeoapi/api/collection.py:363`); `p._fields` truthy adds schema links (`pygeoapi/api/collection.py:375`), with a tuple-vs-string bug (`schema_reltype = ...,` trailing comma, `pygeoapi/api/collection.py:376`) that makes the duplicate check always false (read).

---

## 10. `docs/source/plugins.rst` (and `crs.rst`) vs code

What `plugins.rst` says a plugin must implement: subclass the relevant base class (`docs/source/plugins.rst:31`-`docs/source/plugins.rst:37`), be an importable package referenced by dotted `name` (`docs/source/plugins.rst:59`-`docs/source/plugins.rst:74`), and "implement the API contract of its base provider" (`docs/source/plugins.rst:216`-`docs/source/plugins.rst:221`). Disagreements (code wins):

1. **Field shape.** Vector example `get_fields` returns `{'field1': 'string', 'field2': 'string'}` (`docs/source/plugins.rst:177`-`docs/source/plugins.rst:183`); code requires `{'field1': {'type': 'string'}}` (`pygeoapi/provider/base.py:99`, `pygeoapi/api/itemtypes.py:200`) - the doc shape crashes `/queryables`, `/schema` and OpenAPI (executed, section 6.1).
2. **`_fields` not populated.** The vector example never stores fields (`docs/source/plugins.rst:172`-`docs/source/plugins.rst:183`), so `fields` stays `{}` (queryables empty, filters dropped, `sortby` 400) (executed). The raster example does it correctly (`docs/source/plugins.rst:251`-`docs/source/plugins.rst:260`).
3. **`get_schema` signature.** `def get_schema():` has neither `self` nor `schema_type` (`docs/source/plugins.rst:211`-`docs/source/plugins.rst:213`) vs `get_schema(self, schema_type: SchemaType = SchemaType.item)` (`pygeoapi/provider/base.py:123`); the error would be swallowed during OpenAPI generation (`pygeoapi/api/itemtypes.py:1341`) (read).
4. **Raster `query` parameters.** `query(self, bands=[], subsets={}, format_='json', **kwargs)` (`docs/source/plugins.rst:262`): the API never passes `bands`; it passes `bbox`, `datetime_`, `format_`, optionally `bbox_crs`, `properties`, `subsets` (`pygeoapi/api/coverages.py:124`-`pygeoapi/api/coverages.py:182`) (executed). The example's second comment line lacks `#` (`docs/source/plugins.rst:266`-`docs/source/plugins.rst:267`), a syntax error if copied. It also omits `_coverage_properties`, which collection metadata requires (`pygeoapi/api/collection.py:314`) (executed 500).
5. **EDR example.** `instance()` calls bare `instances()` (`docs/source/plugins.rst:312`-`docs/source/plugins.rst:313`) -> `NameError`. The text says the example advertises `position` and `trajectory` (`docs/source/plugins.rst:326`-`docs/source/plugins.rst:328`), but defining `instances` also registers `instances` as a query type (`pygeoapi/provider/base_edr.py:37`-`pygeoapi/provider/base_edr.py:39`; executed case "only instances()"). Not mentioned: the class-definition-time `ProviderInvalidDataError`, inheritance shadowing, and the `x-ogc-unit` requirement of `get_parameters()`.
6. **Base-class list** (`docs/source/plugins.rst:33`-`docs/source/plugins.rst:37`) omits coverage (uses `BaseProvider`) and `BaseMVTProvider`.
7. **Base `query(self)`** is declared without parameters (`pygeoapi/provider/base.py:172`) while the docs' examples and the API use keywords (`pygeoapi/api/itemtypes.py:557`) - harmless for overriding, but `super().query(**kw)` would fail (read).
8. **Unstated contracts** (not in the docs at all): `user_msg` needed for client-visible error text (section 3.2); per-request instantiation (section 1.2); `create`/`update` receive bytes (section 2.2); `numberMatched`/`numberReturned`/`features` responsibilities and pagination (section 5.2).
9. **Entry points** are documented only for CLI commands (`docs/source/plugins.rst:80`-`docs/source/plugins.rst:84`), consistent with `pygeoapi/__init__.py:57`; there is no provider entry-point discovery (consistent, stated for clarity).
10. `crs.rst` default `crs` list shows CRS84 **and** CRS84h (`docs/source/crs.rst:64`-`docs/source/crs.rst:66`); code appends only `DEFAULT_CRS_LIST[0]` (CRS84) (`pygeoapi/crs.py:119`-`pygeoapi/crs.py:120`) (executed: "List of supported CRSs: http://www.opengis.net/def/crs/OGC/1.3/CRS84."). `crs.rst` says `storage_crs` "must be in `crs` list" (`docs/source/crs.rst:51`) - not enforced (read). It says any CRS outside the list errors (`docs/source/crs.rst:57`), but `filter-crs` is never checked against the list (`pygeoapi/api/itemtypes.py:487`) (read). It says pygeoapi passes "the `crs`" to natively transforming providers (`docs/source/crs.rst:134`-`docs/source/crs.rst:136`); it passes `crs_transform_spec` (`pygeoapi/api/itemtypes.py:562`).
11. Config schema cross-check (for the config/forms agent): the provider item schema (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:513`-`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:643`) has no `additionalProperties: false`, so extra provider keys are allowed. It lacks `uri_field`, top-level `x_field`/`y_field`/`z_field` (only `geometry.x_field`/`y_field`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:559`-`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:571`), `file_types`, `always_xy`, `geom_field` and `languages`, and constrains `options` with a `oneOf` pointing at the PostgreSQL options (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:591`-`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595`) (read).

---

## 11. What a generated third-party provider must do (stock pygeoapi 0.24)

Import path and packaging:
- Live in an importable module (installed distribution, or a directory on `sys.path`/`PYTHONPATH` of the pygeoapi process) and be referenced as `name: <package>.<module>.<Class>` - at least one dot, class after the last dot (`pygeoapi/plugin.py:123`-`pygeoapi/plugin.py:132`). No entry point or registry hook exists (section 1.3).
- Importing the module must not raise: EDR classes are validated at class-definition time (section 7.1); an import error surfaces as an unmapped exception or a generic 500.

Constructor:
- Accept exactly one positional argument, the provider_def dict (`pygeoapi/plugin.py:133`), and call `super().__init__(provider_def)` (requires `name`, `type`, `data`, `pygeoapi/provider/base.py:62`-`pygeoapi/provider/base.py:67`; tile classes require `format` and `options`, `pygeoapi/provider/tile.py:58`-`pygeoapi/provider/tile.py:61`).
- Populate `self._fields` (dict of dicts, each with `type`) before returning (section 6).
- Be cheap and idempotent: it runs on every request, for OpenAPI generation, and (coverage/tile/map/EDR) for `/collections` (section 1.2).
- Signal backend failures with `ProviderConnectionError` (`user_msg=` if the client should see why); never raise bare exceptions from `__init__`, `query`, `get`.
- Unknown extra provider_def keys (e.g. a `spec:` block for the spec engine) are fine: `BaseProvider` ignores them and the config schema allows them (section 10 item 11).

Per provider type:
- **feature/record**: `query(self, offset=0, limit=10, resulttype='results', bbox=[], datetime_=None, properties=[], sortby=[], select_properties=[], skip_geometry=False, q=None, **kwargs)` returning a FeatureCollection dict that always has `features` (also for hits) and, when counted, `numberMatched`/`numberReturned`; `get(self, identifier, **kwargs)` returning a Feature or raising `ProviderItemNotFoundError`; decorate both with `@crs_transform` unless transforming natively; honour `properties`, `select_properties`, `skip_geometry`, `sortby`, `bbox` (storage CRS), `datetime_` (string), `filterq` (pygeofilter AST) or ignore them knowingly; if `editable`: `create(bytes) -> id`, `update(id, bytes)`, `delete(id)` (with `_load_and_prepare_item` for parsing).
- **EDR**: subclass `BaseEDRProvider`; each advertised query method present in the class body (or the section 7.4 hook), all accepting `**kwargs`; `_fields` entries with `title` and `x-ogc-unit`; implement `get_fields()` (used for `parameter-name` validation); override `instances()`/`instance()` if instances are advertised.
- **coverage**: `_fields`, `axes`, `_coverage_properties` (`width`, `height`, `resx`, `resy`), `query(**kwargs)`; config `format`.
- **map**: `query(**kwargs)` reading `kwargs['bbox-crs']`; returns bytes; config `format`; optional `get_legend`.
- **tile**: subclass `BaseTileProvider`/`BaseMVTProvider`; set `tile_type`; implement `__repr__` (MVT), `get_layer`, `get_tiling_schemes`, `get_tiles_service` returning `{'links': [...]}`, `get_tiles`, `get_metadata(**kwargs)`; config `format` and `options.zoom`.

---

## 12. Checks the harness can perform mechanically

Static / load:
1. `load_plugin('provider', provider_def)` succeeds for the generated `name` (no `ModuleNotFoundError`, `AttributeError`, `ProviderInvalidDataError`), and `name` contains a dot.
2. Instance has `name`, `type`, `data` equal to the def and `isinstance(p, BaseProvider)` (or `BaseTileProvider` for tiles).
3. Constructor is side-effect-light: two consecutive `load_plugin` calls both succeed and give equal `fields`.

Fields (feature/record/coverage/EDR):
4. `p.fields` (property, not `get_fields()`) is a non-empty `dict` whose values are `dict`s with a `str` `type`; `p.fields == p.get_fields()`.
5. `type` is a JSON Schema type (`string`, `number`, `integer`, `boolean`, `object`, `array`) or `float` (tolerated); `format`, if present, is `str` or `None`; `values`, if present, is a list.
6. `id_field`, `time_field`, `title_field`, `uri_field` (when configured) and every entry of `properties` refer to existing fields (except where the backend only exposes them in features).
7. EDR: every field has `x-ogc-unit`; `get_parameters()` succeeds and returns one Parameter per field; `get_fields()` does not raise.

`query()` (feature/record):
8. Called with the exact 13 API kwargs (section 5.1) it returns a `dict` with `type == 'FeatureCollection'` and a `features` list.
9. Every feature has `type == 'Feature'`, an `id`, `geometry` (GeoJSON or `None`), `properties` dict; `str(id)` is accepted by `get()`.
10. `numberReturned`, if present, `== len(features)`; `numberMatched`, if present, `>= numberReturned`; `len(features) <= limit`.
11. Paging: `query(offset=0, limit=n)` + `query(offset=n, limit=n)` yield disjoint id sets; results are stable across calls.
12. `resulttype='hits'` returns a dict that still has `features` (empty list) and an integer `numberMatched` equal to the count of `results` over all pages.
13. `skip_geometry=True` -> every `geometry is None`; `select_properties=[f]` -> properties keys subset of `{f} | set(p.properties)`; `properties=[(f, v)]` -> every feature has `str(properties[f]) == str(v)`; `sortby=[{'property': f, 'order': '-'}]` -> non-increasing order; `bbox` -> every geometry intersects (in storage CRS).
14. `crs_transform_spec` for a CRS in the def's `crs` list changes coordinates as `pyproj` predicts (e.g. CRS84 -> EPSG:4326 swaps axes when `always_xy` is false); `None` leaves them unchanged.
15. Unsupported query input raises a `ProviderGenericError` subclass (typically `ProviderInvalidQueryError`), never a bare exception; user-facing texts use `user_msg=`.

`get()`:
16. `get(existing_id, language=None, crs_transform_spec=None)` returns a Feature with `properties`; `get('<unknown>')` raises `ProviderItemNotFoundError` (never returns `None`).

Transactions (if `editable`):
17. `create(json_bytes)` returns a non-empty identifier, then `get(id)` succeeds; `update(id, json_bytes)` then `get(id)` reflects the change; `delete(id)` then `get(id)` raises `ProviderItemNotFoundError`; invalid JSON bytes -> `ProviderInvalidDataError`.

EDR:
18. `query_types` is non-empty and a subset of `EDR_QUERY_TYPES` minus `items`; each is callable via `query(query_type=qt, **the_17_api_kwargs)` and returns a JSON-serialisable dict (CoverageJSON `type` in `Coverage`/`CoverageCollection`; `FeatureCollection` for `locations` without id).
19. If `instances` is registered: `instances()` returns a list of str and `instance(i)` is truthy for each; behaviour for an unknown instance is pinned by project policy (open question 2).

Coverage / map / tile:
20. Coverage: `_coverage_properties` has numeric `width`, `height`, `resx`, `resy`; `axes` is a list; config has `format.name`/`format.mimetype`; `query(bbox=[], datetime_=None, format_='json')` returns a dict with `type == 'Coverage'`; with `format_=<native>` returns bytes.
21. Map: `query(**api_map_kwargs)` (including the key `'bbox-crs'`) returns non-empty bytes.
22. Tile: `tile_type` in `('vector', 'map')`; `get_tiles_service(baseurl=..., servicepath=...)` returns a dict with a `links` list; `get_tiling_schemes()` items have `tileMatrixSet`, `tileMatrixSetURI`, `crs`; `get_tiles(layer=p.get_layer(), tileset=..., z='0', y='0', x='0', format_=p.format_type)` returns bytes or `None`; `repr(p)` works.

API-level (in-process, as done in these experiments: build `API(config, {})` and call the handler functions with an `APIRequest` built from a werkzeug `Request`):
23. `/collections/{id}` returns 200 (this is the only check that instantiates coverage/tile/map/EDR providers for metadata).
24. `/items`, `/items/{id}`, `/queryables`, `/schema` return 200 with no uncaught exception; `/items?resulttype=hits&limit=<max>` returns 200.
25. `get_oas(config)` (default `fail_on_invalid_collection=True`) succeeds and contains `/collections/{id}/queryables` for feature collections (proves `_fields` is populated).
26. Error-path table: provider raising each `Provider*Error` produces the status in section 3.1 through the API.

---

## 13. Open questions for the user

1. **`register()` decorator in earlier releases: unknown.** The clone is shallow (1 commit), and there is no changelog/release-notes file and no mention in `docs/`; only the test `test_unique_subclass_query_types` (`tests/provider/test_base_provider.py:407`) pins per-subclass isolation. Verifying would require fetching an older sdist (not done: no downloads in this phase).
2. **EDR `instance()` policy.** Every choice makes one instance endpoint return an unhandled 500 in 0.24.0 (section 7.3). Standardise on raising `ProviderItemNotFoundError` (clean 404 on `/instances/{id}`) or returning `False` (clean 400 on `/instances/{id}/{qt}`)?
3. **Engine hook vs explicit re-binds** for generated EDR classes (section 7.4): the `__init_subclass__` override is neater but depends on internals; re-binds are internals-free but noisier.
4. **Strictness:** should the harness require `numberMatched`/`numberReturned` (pygeoapi does not), JSON-Schema-only field types (core providers violate this), and `features` on hits (pygeoapi crashes without it only for small counts)?
5. **Harness transport:** in-process API calls (used here; uncaught exceptions surface as Python exceptions) vs a Flask/Starlette test client (uncaught exceptions become bare 500s). Both are possible; the API needs only a config dict.
6. Core bugs seen in passing that may affect Mode A presets: GeoJSON `create()` indexes the bytes body as a dict (`pygeoapi/provider/geojson.py:229`; executed `TypeError`, surfacing as 400); ERDDAP `get_fields` keeps only the last field (`pygeoapi/provider/erddap.py:81`); a resulttype=hits response gets a `next` link (`pygeoapi/api/itemtypes.py:613`).

---

## Appendix: experiments (all executed with `.venv/bin/python -I` from the scratchpad)

| Script | What it established |
|---|---|
| `exp1_load_plugin.py` | loader errors (`InvalidPluginError`, `ModuleNotFoundError`, `AttributeError`, `KeyError`, `RuntimeError`), whole-dict constructor call, GeoJSON/CSV `get_fields()` and `query()` keys (`limit=2`: `type, features, numberMatched, numberReturned`; hits: `type, features, numberMatched`), `get(unknown)` -> `ProviderItemNotFoundError`, `@crs_transform` output |
| `exp2_edr.py` | EDR registration matrix (section 7.1), `query()` dispatch, `instances()`/`instance()` defaults, `get_parameters()` with/without `title`/`x-ogc-unit` |
| `exp3_api.py` + `harness_providers.py` | in-process API: 13 query kwargs, links/timeStamp/pagination, hits edge cases, full error-mapping table, `get()` kwargs and None handling, CRS transform, transactions (bytes, Location, get-before-delete), field-shape crashes, uncached `_fields`, constructor failures, OpenAPI blast radius |
| `exp4_edr_tiles_cov.py` | EDR API kwargs and validation, instances failure modes, EDR collection metadata and OpenAPI paths, engine `__init_subclass__` pattern, import-time failure mapping, tile/MVT base behaviour, coverage `_coverage_properties`/`format` requirements and query kwargs, `InvalidPluginError` at request time, GeoJSON `create(bytes)` |
| `exp5.py` + `gen_misc.py` | `instance()` raising vs returning False, map query kwargs (incl. `'bbox-crs'`), map `properties` 501, MVT `get_tiles_service` returning None -> `TypeError` in `/tiles` |
