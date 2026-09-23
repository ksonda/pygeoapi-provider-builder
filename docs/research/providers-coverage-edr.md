# Coverage & EDR providers and their API layer — pygeoapi 0.24.0

Phase 0 research for *pygeoapi Provider Builder* (descriptors, "time series → EDR" template engine, contract harness).

- **Ground truth:** pygeoapi **0.24.0** (tag commit f765a64), source in `vendor/pygeoapi`. All citations are `path:line` relative to that repo root.
- **Legend:** **(read)** = established by reading source/docs; **(executed)** = observed by running code (IDs like `R4`, `X5`, `E7` refer to §E Execution log).
- **Environments used:** project `.venv` (pygeoapi 0.24.0, rasterio 1.5.1 / GDAL 3.12.4, numpy 2.5.3) for rasterio; scratch venv (pygeoapi 0.24.0 + xarray 2026.7.0, netCDF4 1.7.4, zarr 2.18.7, fsspec 2026.9.0, cftime 1.6.5, numpy 2.5.3; **no dask, no s3fs**) for xarray/xarray-edr. Both installed `pygeoapi/` trees were verified byte-identical to `vendor/pygeoapi/pygeoapi` with `diff -rq` (executed).
- **Note on numpy:** pygeoapi's own provider requirements pin `numpy==2.0.1` (`requirements-provider.txt:11`); our project venv has numpy 2.5.3, where `float()` of a size-1 1-D array is a `TypeError` (executed). This breaks one xarray code path (see xarray gotchas). Our harness must run with the numpy we ship.

---

## 0. Registry and shared plumbing

| Registry name | Class | Provider `type` | Source |
|---|---|---|---|
| `rasterio` | `pygeoapi.provider.rasterio_.RasterioProvider` | `coverage` | `pygeoapi/plugin.py:63` |
| `xarray` | `pygeoapi.provider.xarray_.XarrayProvider` | `coverage` | `pygeoapi/plugin.py:72` |
| `xarray-edr` | `pygeoapi.provider.xarray_edr.XarrayEDRProvider` | `edr` | `pygeoapi/plugin.py:73` |
| `SensorThingsEDR` | `pygeoapi.provider.sensorthings_edr.SensorThingsEDRProvider` | `edr` | `pygeoapi/plugin.py:65` |

- These are the only coverage/EDR providers in the registry: `BaseEDRProvider` is subclassed only in `pygeoapi/provider/xarray_edr.py:45` and `pygeoapi/provider/sensorthings_edr.py:52`; `pygeoapi/provider/xarray_.py:47` imports it only to reuse `get_parameters()` (read). Custom providers are referenced by dotted path in `name` (`pygeoapi/plugin.py:123`).
- The provider classes never check `type`; the API selects the provider by `type` (`coverage` in `pygeoapi/api/coverages.py:96`, `edr` in `pygeoapi/api/environmental_data_retrieval.py:279`). Allowed `type` values per config schema: `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:522` (enum incl. `coverage`, `edr`). **Doc conflict:** `docs/source/configuration.rst:205` lists only `feature`, `coverage`, `tile` as allowed types (omits `edr`), while the YAML comment at `docs/source/configuration.rst:255` lists `edr` too.
- **A new provider instance per request:** `load_plugin` instantiates the class every call (`pygeoapi/plugin.py:133`) and each handler calls it (`pygeoapi/api/coverages.py:98`, `pygeoapi/api/environmental_data_retrieval.py:280`, `pygeoapi/api/collection.py:307`, `pygeoapi/api/collection.py:398`). So datasets are re-opened / remote services re-queried on every request, including `GET /collections/{id}` (read). Per-instance state bugs (e.g. xarray-edr `_fields` mutation) therefore only leak within one request in the server — but **will** leak in a harness that reuses an instance.
- **Common keys (BaseProvider)** — `name`/`type`/`data` required, else `RuntimeError('name/type/data are required')` (`pygeoapi/provider/base.py:67`); also read: `options` (`pygeoapi/provider/base.py:71`), `x_field` (`:74`), `y_field` (`:75`), `z_field` (`:76`), `time_field` (`:77`), `properties` (`:79`), `storage_crs` (`pygeoapi/provider/base.py:86`). Coverage-specific attributes initialised to empty: `axes`, `crs`, `num_bands` (`pygeoapi/provider/base.py:90`). `self.filename = None` (`pygeoapi/provider/base.py:83`) — used by the coverage API for `Content-Disposition` (`pygeoapi/api/coverages.py:195`); **none** of the four providers ever sets it (read).
- **Default provider:** `default: true` or the first provider (`pygeoapi/provider/__init__.py:81`); collection-description logic keys off the *default* provider's type (`pygeoapi/api/collection.py:74`). Note `get_provider_by_type` returns the **first** provider of a type (`pygeoapi/provider/__init__.py:63`) but `filter_providers_by_type` (used for EDR OpenAPI) returns the **last** (`pygeoapi/provider/__init__.py:47`).
- **Error → HTTP mapping** (`pygeoapi/provider/base.py`): `ProviderGenericError` 500 (inherits `http_status_code` from `pygeoapi/error.py:41`); `ProviderConnectionError` (`:327`) 500; `ProviderTypeError` 400 (`pygeoapi/provider/base.py:335`); `ProviderInvalidQueryError` 400 (`:341`); `ProviderQueryError` 500 (`:345`); `ProviderItemNotFoundError` 404 (`:353`); `ProviderNoDataError` **204** (`pygeoapi/provider/base.py:360`); `ProviderInvalidDataError` 500 (`:374`). **Gotcha:** the client-visible message is `user_msg` or the class `default_msg` (`pygeoapi/error.py:53`), so `ProviderQueryError('z-axis not supported')` reaches the client as "query error (check logs)" (read). Non-`ProviderGenericError` exceptions (TypeError, CRSError, NotImplementedError, AttributeError) escape the API handlers' `except ProviderGenericError` (`pygeoapi/api/coverages.py:188`, `pygeoapi/api/environmental_data_retrieval.py:439`) → 500.

---

## 1. Providers

### `rasterio` — `pygeoapi.provider.rasterio_.RasterioProvider`

- **Provider type(s) / base:** `coverage`; subclasses `BaseProvider` (`pygeoapi/provider/rasterio_.py:45`). Test/doc configs use `type: coverage` (`tests/pygeoapi-test-config.yml:285`, `docs/source/publishing/ogcapi-coverages.rst:44`).
- **Dependencies:** `numpy`, `pyproj` (`CRS`, `Transformer`), `rasterio`, `rasterio.io.MemoryFile`, `rasterio.mask` (`pygeoapi/provider/rasterio_.py:32`–`36`). pip: `rasterio` (a **core** requirement, `requirements.txt:14`), `pyproj`, `numpy`. GRIB/NetCDF/etc. support depends on the GDAL build bundled in the rasterio wheel.
- **`data` forms:** anything `rasterio.open()` accepts — local path, URL, GDAL `/vsi*` path (`pygeoapi/provider/rasterio_.py:58`). For "native, no-arguments" output the raw bytes are read with `read_data(self.data)` (`pygeoapi/provider/rasterio_.py:125`), which only handles local paths or `http*`/`s3*` via `urlopen` (`pygeoapi/util.py:622`) — so `/vsi*` paths or `s3://` fail on that path (read).
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | str | yes | — | `rasterio` | `pygeoapi/plugin.py:63` |
  | `type` | str | yes | — | must be `coverage` for the Coverages API to find it | `pygeoapi/api/coverages.py:96` |
  | `data` | str | yes | — | dataset opened with `rasterio.open` | `pygeoapi/provider/rasterio_.py:58` |
  | `format.name` | str | **yes (effectively)** | — | Read in `__init__` (`KeyError` → `ProviderConnectionError`, executed R11). Used as GDAL output driver for subset native output (`pygeoapi/provider/rasterio_.py:222`) and compared verbatim to `?f=` by the API (`pygeoapi/api/coverages.py:193`). Docs: must be a GDAL raster driver short name (`docs/source/publishing/ogcapi-coverages.rst:54`). | `pygeoapi/provider/rasterio_.py:64` |
  | `format.mimetype` | str | yes (API) | — | `Content-Type` of native responses; also the native link `type` in collection metadata | `pygeoapi/api/coverages.py:199`, `pygeoapi/api/collection.py:296` |
  | `options` | dict | no | `None` | **Every** key/value is copied into the output rasterio profile (GDAL creation options, but also overrides profile keys like `crs`, `nodata`, `dtype`) for both JSON and native output paths | `pygeoapi/provider/rasterio_.py:206` |
  | `options.crs` | str | no | dataset CRS | Destination CRS for reprojecting a `bbox` (`CRS.from_string`); because of the loop above it is *also* written into the output profile as `crs` (read) | `pygeoapi/provider/rasterio_.py:139` |
  | `options.DATA_ENCODING` | str | no | — | **Doc/test-only example** of a GRIB creation option passed through `options` (no code reads it by name) | `docs/source/publishing/ogcapi-coverages.rst:48`, `tests/provider/test_rasterio_provider.py:47`, `pygeoapi-config.yml:232` |
  | `storage_crs` | str | no | CRS84 | Parsed by `BaseProvider` (`pygeoapi/provider/base.py:86`) but **not used** by rasterio; coverage collections do not advertise `crs`/`storageCrs` (only `edr`/`feature` do, `pygeoapi/api/collection.py:273`) | (read) |
  | `x_field`/`y_field`/`time_field`/`properties` | — | — | — | **Not used** by rasterio (axis names are fixed, see Capabilities) | (read) |

- **Capabilities:**
  - `get_fields()` → one field per band keyed `"1"`, `"2"`, … with `title`, `type` (`float`/`integer`/`string`/raw dtype), `_meta` (band tags) and `x-ogc-unit` only when a unit is known (`pygeoapi/provider/rasterio_.py:89`–`95`). Title/unit come from `_get_parameter_metadata`, which only understands the **GRIB** driver (`pygeoapi/provider/rasterio_.py:406`); for every other driver (GeoTIFF!) title/unit are `None` (executed T1: `{"1": {"title": null, "type": "float", "_meta": {"units": "m3/s"}}, ...}` — band description and `units` tag ignored).
  - Axes: `['Long', 'Lat']` for geographic CRS, `['x', 'y']` for projected (`pygeoapi/provider/rasterio_.py:364`, `pygeoapi/provider/rasterio_.py:377`); executed R1/R12. No time axis ever.
  - `query(properties=[], subsets={}, bbox=None, bbox_crs=4326, datetime_=None, format_='json')` (`pygeoapi/provider/rasterio_.py:99`):
    - `properties` = band numbers as strings → `indexes=list(map(int, bands))` (`pygeoapi/provider/rasterio_.py:198`).
    - `subsets` honoured only if **both** x and y axes are present (`pygeoapi/provider/rasterio_.py:178`); a single-axis subset is silently ignored (executed: `Long`-only subset returned full `[1201, 2400]`).
    - `bbox` → polygon mask with `rasterio.mask.mask(filled=False, crop=True)` (`pygeoapi/provider/rasterio_.py:212`); `bbox` + x/y subset → `ProviderQueryError` (`pygeoapi/provider/rasterio_.py:132`, executed R9).
    - `bbox_crs` → `CRS.from_epsg(bbox_crs)` (`pygeoapi/provider/rasterio_.py:137`): ints and digit strings work, a CRS **URI raises `CRSError`** (not a provider error → HTTP 500 via the API, which passes the raw `bbox-crs` string, `pygeoapi/api/coverages.py:130`) (executed R10). Only two bbox corners are transformed (`pygeoapi/provider/rasterio_.py:161`–`162`).
    - `datetime_` accepted but **ignored** (executed R6).
    - Output: `format_ == 'json'` → CoverageJSON dict (`pygeoapi/provider/rasterio_.py:248`); otherwise native bytes from a `MemoryFile` in driver `format.name` (`pygeoapi/provider/rasterio_.py:254`); with no bands/subsets/bbox and non-json format → the whole source file bytes (`pygeoapi/provider/rasterio_.py:123`) (executed R7: 1,057,634 bytes; R8 bbox native: 687 bytes).
  - CoverageJSON shape (`gen_covjson`, `pygeoapi/provider/rasterio_.py:260`): `type: Coverage`, `domain.domainType: Grid`, axes `x`/`y` as `{start, stop, num}` where start/stop are the **requested** bbox/subset limits (not cell centres), `y` runs max→min (`pygeoapi/provider/rasterio_.py:283`); one `referencing` entry for `['x','y']` (`pygeoapi/provider/rasterio_.py:288`); parameters keyed by `pm['id']` (`pygeoapi/provider/rasterio_.py:326`); ranges `NdArray`, `dataType: 'float'` hard-coded (`pygeoapi/provider/rasterio_.py:333`), `axisNames ['y','x']`, masked cells → `null` (executed R4: 8×8 subset with 36 non-null values).
  - CRS: `self.crs` / referencing id is CRS84 for geographic data; for projected data it is built as `http://www.opengis.net/def/crs/OGC/1.3/{epsg}` (`pygeoapi/provider/rasterio_.py:376`) — wrong authority path, and `…/OGC/1.3/None` when there is no EPSG code (executed R12 on the HRDPS polar-stereographic GRIB). For a projected dataset queried with a lon/lat bbox the returned axes are in lon/lat while `referencing` says ProjectedCRS (executed R12b).
  - `get_schema()`, `get_domains()`, `get_metadata()` not implemented (inherit `NotImplementedError`, `pygeoapi/provider/base.py:133`).
  - Collection metadata uses `_coverage_properties` `width/height/resx/resy` (`pygeoapi/provider/rasterio_.py:366`; consumed at `pygeoapi/api/collection.py:314`) (executed R3b: 2400/1201/0.15/0.15).
- **Docs examples:** `docs/source/publishing/ogcapi-coverages.rst:41`–`51` (keys: `type`, `name`, `data`, `options.DATA_ENCODING`, `format.name`, `format.mimetype`); query examples `docs/source/publishing/ogcapi-coverages.rst:133`–`153`.
- **Tests/fixtures:** `tests/provider/test_rasterio_provider.py:36` (GRIB2 `CMC_glb_TMP_TGL_2…grib2`), config block `tests/provider/test_rasterio_provider.py:41`–`53`, projected fixture `tests/provider/test_rasterio_provider.py:85`; API tests `tests/api/test_coverages.py:96`–`168`; config `tests/pygeoapi-test-config.yml:267`–`290` (`gdps-temperature`), default config `pygeoapi-config.yml:228`–`235`. **There is no GeoTIFF in `tests/data`** (only `.grib2`, `.nc`, `.zarr`) — our checks used a synthetic 2-band GeoTIFF (T1–T5).
- **Errors raised:** `ProviderConnectionError` for any init failure incl. missing `format` (`pygeoapi/provider/rasterio_.py:67`); `ProviderQueryError` for bbox+subset (`pygeoapi/provider/rasterio_.py:132`), mask errors (`pygeoapi/provider/rasterio_.py:220`), range IndexError (`pygeoapi/provider/rasterio_.py:344`); uncaught `CRSError` for URI `bbox_crs`.
- **Gotchas / conflicts:**
  - **Multi-band non-GRIB rasters produce invalid CoverageJSON:** every band gets parameter id `None`, so `parameters`/`ranges` collapse to a single `null` key and all selected bands' values are flattened into one range (executed T2: shape `[3,3]` but 18 values).
  - Docs table claims rasterio supports `datetime` (`docs/source/publishing/ogcapi-coverages.rst:21`) — code ignores it (executed R6).
  - Docs subset example uses `lat`/`long` and repeated `subset=` params (`docs/source/publishing/ogcapi-coverages.rst:145`); code axis names are case-sensitive `Lat`/`Long` (`pygeoapi/provider/rasterio_.py:364`) and the API reads only the first `subset` value from the multidict (`pygeoapi/api/coverages.py:169`) — use one comma-separated `subset=Lat(5:10),Long(5:10)` as the tests do (`tests/api/test_coverages.py:135`).
  - Docs bbox-crs example `&bbox=crs=3857` (`docs/source/publishing/ogcapi-coverages.rst:153`) is a typo for `bbox-crs=3857`; and `bbox-crs` must be an EPSG integer, not the OGC-standard URI (see above).
  - Docs note that native downloads get a filename from the collection name (`docs/source/publishing/ogcapi-coverages.rst:157`) — not implemented; no provider sets `filename` (read).
  - No size guard: a full-grid JSON request serialises 2,882,400 values (executed, ~1.6 s just in the provider).

### `xarray` — `pygeoapi.provider.xarray_.XarrayProvider`

- **Provider type(s) / base:** `coverage`; subclasses `BaseProvider` (`pygeoapi/provider/xarray_.py:53`).
- **Dependencies:** `xarray`, `fsspec`, `numpy`, `pyproj` imported at module import (`pygeoapi/provider/xarray_.py:37`–`40`) — so `fsspec` is required even for local NetCDF. pip (from `requirements-provider.txt`): `xarray` (`:25`), `netCDF4` (`:10`) or `scipy` (`:23`) as NetCDF engine (native output uses `to_netcdf`), `zarr<3` (`:26`), `s3fs<=2023.6.0` for `s3://` (`:27`), `cftime` (`:3`) for non-standard calendars; `dask` is **needed** for glob (`open_mfdataset`) inputs but is not listed anywhere (read); `h5netcdf` is not referenced. `fsspec` is not listed explicitly (it arrives via `s3fs`). (executed: local NetCDF + zarr worked without dask/s3fs.)
- **`data` forms** (`pygeoapi/provider/xarray_.py:70`–`90`): path ending **exactly** in `.zarr` → `xarray.open_zarr` (trailing `/` defeats detection); a path containing `*` → `xarray.open_mfdataset`; anything else → `xarray.open_dataset`; `s3://…` → wrapped with `fsspec.get_mapper(data, **options.s3)` (only sensible for zarr stores). On a `ValueError` mentioning "time" the dataset is re-opened with `decode_times=False` using `self.data` and **without** `open_options`/S3 mapper (`pygeoapi/provider/xarray_.py:97`).
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | str | yes | — | `xarray` | `pygeoapi/plugin.py:72` |
  | `type` | str | yes | — | `coverage` | `tests/pygeoapi-test-config.yml:210` |
  | `data` | str | yes | — | see `data` forms | `pygeoapi/provider/xarray_.py:70` |
  | `format.name` | str | **yes (API)** | — | Not read by the provider; the API compares it to `?f=` (`pygeoapi/api/coverages.py:193`). Provider special-cases `format_ == 'zarr'` (`pygeoapi/provider/xarray_.py:278`); any other non-`json` value yields NetCDF bytes (`pygeoapi/provider/xarray_.py:284`). Use `NetCDF` (exact case) to also match `Accept: application/x-netcdf` (`pygeoapi/formats.py:40`) | `tests/pygeoapi-test-config.yml:217` |
  | `format.mimetype` | str | yes (API) | — | native `Content-Type` | `pygeoapi/api/coverages.py:199` |
  | `options.squeeze` | bool | no | `false` | `Dataset.squeeze()` after open | `pygeoapi/provider/xarray_.py:66` |
  | `options.zarr` | dict | no | `{}` | kwargs for `xarray.open_zarr` (only for `.zarr` data) | `pygeoapi/provider/xarray_.py:67` |
  | `options.s3` | dict | no | `{}` | kwargs for `fsspec.get_mapper` (e.g. `anon`, `requester_pays`, `client_kwargs.endpoint_url`) | `pygeoapi/provider/xarray_.py:82` |
  | `x_field` | str | no | coord whose `units == 'degrees_east'` | x coordinate name; **also** the key used to build output bbox/width | `pygeoapi/provider/xarray_.py:437`, `pygeoapi/provider/xarray_.py:442` |
  | `y_field` | str | no | coord whose `units == 'degrees_north'` | y coordinate name | `pygeoapi/provider/xarray_.py:434`, `pygeoapi/provider/xarray_.py:444` |
  | `time_field` | str | no | coord named `time` (case-insensitive) | time coordinate; `datetime_` slicing uses it | `pygeoapi/provider/xarray_.py:431`, `pygeoapi/provider/xarray_.py:446` |
  | `storage_crs` | str (any `pyproj.CRS.from_user_input`) | no | parsed from CF `grid_mapping` var or a `crs` variable, else CRS84 | only when absent from config is it parsed from data | `pygeoapi/provider/xarray_.py:105`, `pygeoapi/provider/xarray_.py:593` |
  | `z_field` | — | — | — | not used by `xarray` (only `xarray-edr`) | (read) |

- **Capabilities:**
  - `get_fields()`: every **data variable** (not a coord) with a `units` attribute → `{type, title: long_name, x-ogc-unit: units}`; variables without `units` are **skipped** with a warning (`pygeoapi/provider/xarray_.py:130`; executed Z1: `mask` dropped). `type` becomes `float`/`integer`/`string`, otherwise the raw numpy dtype object (e.g. `uint8`, `bool`, `datetime64`) is stored (`pygeoapi/provider/xarray_.py:123`–`128`) (read).
  - Axes: `[x_field, y_field(, time_field)]` (`pygeoapi/provider/xarray_.py:503`; executed X1 `['COADSX','COADSY','TIME']`, Z1 `['lon','lat','time']`). The API only validates `subset` axis names against these (`pygeoapi/api/coverages.py:176`).
  - `query(properties=[], subsets={}, bbox=[], bbox_crs=4326, datetime_=None, format_='json')` (`pygeoapi/provider/xarray_.py:143`): `properties` = variable names; subsets become `.sel(slice)` with automatic handling of descending coords (`pygeoapi/provider/xarray_.py:180`); `datetime_` instant or `a/b` interval on `time_field` (`pygeoapi/provider/xarray_.py:225`), exclusive with a time subset (`pygeoapi/provider/xarray_.py:221`); `bbox_crs` ignored (`pygeoapi/provider/xarray_.py:213`); empty result → `ProviderNoDataError` (204) (`pygeoapi/provider/xarray_.py:245`, executed X13).
  - **`bbox` is only applied when a subset or `datetime_` is also given** — the whole subsetting block is guarded by `any([x in subsets, y in subsets, time in subsets, datetime_ is not None])` (`pygeoapi/provider/xarray_.py:170`). `bbox` alone returns the full grid (executed X5: `[12, 90, 180]`; X6 bbox+datetime: `[1, 5, 5]`).
  - Output: `json` → CoverageJSON; `zarr` → zipped Zarr bytes (`pygeoapi/provider/xarray_.py:280`); other → NetCDF bytes (`pygeoapi/provider/xarray_.py:284`); no properties/subsets + non-json → **whole source file** or whole-store zip (`pygeoapi/provider/xarray_.py:158`–`163`), even when a `bbox` was requested (executed X10: 3,147,886 bytes both with and without bbox).
  - CoverageJSON (`gen_covjson`, `pygeoapi/provider/xarray_.py:290`): `Coverage`/`Grid`, axes `x`/`y` `{start, stop, num}` (`pygeoapi/provider/xarray_.py:329`), `t` as `values` list of numpy ISO strings (nanosecond precision, no `Z`, e.g. `2000-01-16T06:00:00.000000000`; `pygeoapi/provider/xarray_.py:365`), referencing `GeographicCRS`/`TemporalRS` (`pygeoapi/provider/xarray_.py:373`); `parameters` from `BaseEDRProvider.get_parameters` (`pygeoapi/provider/xarray_.py:382`) → UCUM-typed unit symbol objects (executed X4c); ranges `axisNames ['t','y','x']`, `dataType` = field type (`pygeoapi/provider/xarray_.py:393`); if x and y both have size 1 the domain becomes `PointSeries` (`pygeoapi/provider/xarray_.py:352`–`361`).
  - CRS: `bbox_crs` in `_coverage_properties` is CRS84 unless `storage_crs` parses to a non-4326 EPSG, in which case `https://www.opengis.net/def/crs/EPSG/0/{code}` (note `https`) (`pygeoapi/provider/xarray_.py:495`).
  - `_coverage_properties` also carries `time_range`, `restime`, `time_duration` (`pygeoapi/provider/xarray_.py:478`–`488`), which the collection description publishes as temporal extent/grid (`pygeoapi/api/collection.py:320`) (executed X3/Z1b). `get_schema`/`get_domains` not implemented.
- **Docs examples:** NetCDF `docs/source/publishing/ogcapi-coverages.rst:67`–`83` (keys `x_field`, `y_field`, `time_field`, `storage_crs`, `format`); Zarr `docs/source/publishing/ogcapi-coverages.rst:85`–`95` (`options.zarr.consolidated`, `options.squeeze`); S3 note `docs/source/publishing/ogcapi-coverages.rst:107`; `storage_crs` note `docs/source/publishing/ogcapi-coverages.rst:112`.
- **Tests/fixtures:** `tests/provider/test_xarray_netcdf_provider.py:39` (`coads_sst.nc`, config `:43`–`52`); `tests/provider/test_xarray_zarr_provider.py:42` (`analysed_sst.zarr`, config `:47`–`56`, no-time variant `:60`–`70`); API `tests/api/test_coverages.py:170` (`cmip5`, Accept NetCDF); config `tests/pygeoapi-test-config.yml:192`–`218` (`cmip5`, `CMIP5_rcp8.5_annual_abs_latlon1x1_PCP_pctl25_P1Y.nc`).
- **Errors raised:** `ProviderConnectionError` wraps every init failure (`pygeoapi/provider/xarray_.py:115`); `ProviderQueryError` for bbox+x/y subset (`pygeoapi/provider/xarray_.py:192`), datetime without time axis (`pygeoapi/provider/xarray_.py:219`), datetime+time subset (`pygeoapi/provider/xarray_.py:223`), any `.sel` failure (`pygeoapi/provider/xarray_.py:240`; executed X12 out-of-range instant); `ProviderNoDataError` for empty selections (`pygeoapi/provider/xarray_.py:245`).
- **Gotchas / conflicts:**
  - **Docs zarr example fails on the repo's own fixture:** `options.zarr.consolidated: true` (`docs/source/publishing/ogcapi-coverages.rst:94`) on `tests/data/analysed_sst.zarr` (not consolidated) → `ProviderConnectionError: '.zmetadata'` (executed). `consolidated: false` works.
  - **Single-cell results crash with numpy ≥ 2.5:** `float(data.coords[x].values)` on a size-1 1-D coord (`pygeoapi/provider/xarray_.py:356`) raises `TypeError` → HTTP 500 (executed X8/X14). Also hits xarray-edr `cube` (executed E7 `cube-open-start`).
  - `bbox` ignored unless combined with `subset`/`datetime` (above) — docs table claims bbox support (`docs/source/publishing/ogcapi-coverages.rst:22`).
  - Docs NetCDF example uses `format.name: netcdf` (`docs/source/publishing/ogcapi-coverages.rst:82`) while `Accept: application/x-netcdf` resolves to `NetCDF` (`pygeoapi/formats.py:40`); the API compares case-sensitively (`pygeoapi/api/coverages.py:194`) → 400 *after* running the query (read).
  - Zarr export writes `tmpXXXXzarr.zarr` + `.zip` next to (not inside) a deleted temp dir and never cleans them (`pygeoapi/provider/xarray_.py:669`–`672`; executed: both left in `$TMPDIR`), and `_zip_dir` calls `os.chdir` (`pygeoapi/provider/xarray_.py:656`) — process-global, not thread-safe.
  - `options: null` in YAML → `None.get` crash before the `try` (`pygeoapi/provider/xarray_.py:66`) (read).

### `xarray-edr` — `pygeoapi.provider.xarray_edr.XarrayEDRProvider`

- **Provider type(s) / base:** `edr`; `class XarrayEDRProvider(BaseEDRProvider, XarrayProvider)` (`pygeoapi/provider/xarray_edr.py:45`); both `__init__`s run (`pygeoapi/provider/xarray_edr.py:57`–`58`). Registered query types (via `__init_subclass__`, see §3): **`['position', 'cube']`** only (executed E0) — the class defines `position` (`pygeoapi/provider/xarray_edr.py:60`) and `cube` (`pygeoapi/provider/xarray_edr.py:172`).
- **Dependencies:** as `xarray` (inherits all opening logic) + `numpy` (`pygeoapi/provider/xarray_edr.py:32`); `shapely` geometry objects arrive from the API.
- **`data` forms:** identical to `xarray` (inherited `XarrayProvider.__init__`, `pygeoapi/provider/xarray_.py:70`). Docs show local NetCDF, local Zarr, and anonymous S3 Zarr (`docs/source/publishing/ogcapi-edr.rst:53`, `:70`, `:78`).
- **Keys:** all `xarray` keys (same semantics, incl. auto-detection of `x_field`/`y_field`/`time_field`) plus:

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | str | yes | — | `xarray-edr` | `pygeoapi/plugin.py:73` |
  | `type` | str | yes | — | `edr` | `tests/pygeoapi-test-config.yml:311` |
  | `z_field` | str | no | `None` (no auto-detect) | coordinate used for `z` in **position** only; if unset `z` is silently ignored (executed E7 `pos-z-no-zfield`) | `pygeoapi/provider/xarray_edr.py:108` |
  | `format.name` / `format.mimetype` | str | no for EDR | — | **Not used** by the EDR API or provider (EDR output is always CoverageJSON unless a dataset `formatters` entry matches `?f=`); present in docs/tests only | `docs/source/publishing/ogcapi-edr.rst:63`, `tests/pygeoapi-test-config.yml:314` |
  | `crs` | list[str] | no | `[CRS84]` | EDR API: allowed values of `?crs=`, advertised as collection `crs` (list is mutated to append CRS84) | `pygeoapi/crs.py:111`, `pygeoapi/api/collection.py:274` |
  | `storage_crs` | str | no | CRS84 / parsed | advertised as `storageCrs`; source CRS for `crs_transform_spec` | `pygeoapi/api/collection.py:275`, `pygeoapi/crs.py:557` |
  | `always_xy` | bool | no | `false` | copied into `CrsTransformSpec` | `pygeoapi/crs.py:556` |
  | `storage_crs_coordinate_epoch` | number | no | — | advertised as `storageCrsCoordinateEpoch` | `pygeoapi/api/collection.py:276` |

  Doc-only: `docs/source/publishing/ogcapi-edr.rst:58` shows `z_field: z` on `coads_sst.nc`, which has no `z` coordinate (executed X0 coords: `COADSX`, `COADSY`, `TIME`).
- **Capabilities:**
  - `position(wkt, datetime_, select_properties, z, instance, …)`: `Point` → nearest-neighbour `.sel(x, y, method='nearest')` (`pygeoapi/provider/xarray_edr.py:84`, `pygeoapi/provider/xarray_edr.py:144`); `LineString` → vectors of x and y, i.e. an outer-product **grid** of nearest cells, not a trajectory (executed E7 `pos-linestring` → `Grid [12,2,2]`); `Polygon` → slices + `method='nearest'` → xarray `NotImplementedError` → HTTP 500 (executed E7 `pos-polygon`). Output PointSeries: axes `x`/`y` single `values`, `t` list; ranges `axisNames ['t','y','x']`, shape `[n,1,1]` (executed E3/E5).
  - `datetime_` via `_make_datetime` (`pygeoapi/provider/xarray_edr.py:250`): strips `Z` (`pygeoapi/provider/xarray_edr.py:258`), supports `..` open ends (`pygeoapi/provider/xarray_edr.py:261`–`264`); an **instant is matched by calendar date only** (`data[time].dt.date == value`, `pygeoapi/provider/xarray_edr.py:137`) — `2000-01-16` matches, `2000-01-17` → `ProviderNoDataError` 204 (executed E7, `tests/api/test_environmental_data_retrieval.py:225`–`230`).
  - `cube(bbox, datetime_, select_properties)`: 4-value bbox → x/y slices honoring descending coords (`pygeoapi/provider/xarray_edr.py:192`–`196`, `pygeoapi/provider/xarray_edr.py:308`); 6-value bbox → `ProviderQueryError('z-axis not supported')` (`pygeoapi/provider/xarray_edr.py:198`; executed E7 `cube6`); `z` ignored. Output `Grid` (executed E6: `SST [2,5,5]`).
  - `area`, `radius`, `trajectory`, `corridor`, `locations`, `instances`: **not implemented** (docs matrix `docs/source/publishing/ogcapi-edr.rst:23` agrees). `instance` kwarg is logged only (`pygeoapi/provider/xarray_edr.py:99`). `format_`, `limit` (except as `time_steps` fallback, `pygeoapi/provider/xarray_edr.py:305`) and `crs_transform_spec` are ignored — the API still sets `Content-Crs` to the requested CRS (`pygeoapi/crs.py:621`), so a transformed-CRS request gets untransformed coordinates with a misleading header (read).
  - `get_fields()`/`get_parameters()` inherited; `parameter_names` in collection metadata = `get_parameters()` (executed E2: `AIRT, SST, UWND, VWND`; `tests/api/test_environmental_data_retrieval.py:44`–`57`).
- **Docs examples:** `docs/source/publishing/ogcapi-edr.rst:48`–`85` (keys `x_field`, `y_field`, `z_field`, `time_field`, `storage_crs`, `format`, `options.s3.anon`, `options.s3.requester_pays`); query URLs `docs/source/publishing/ogcapi-edr.rst:167`–`173`.
- **Tests/fixtures:** API tests `tests/api/test_environmental_data_retrieval.py:60`–`271` (position, datetime forms, cube, S3 PRISM cube), CRS `tests/api/test_environmental_data_retrieval.py:274`–`291`; configs `tests/pygeoapi-test-config.yml:292`–`316` (`icoads-sst`) and `tests/pygeoapi-test-config.yml:318`–`347` (`usgs-prism`, `s3://mdmf/gdp/PRISM_v2.zarr` with `options.s3.client_kwargs.endpoint_url`), `tests/pygeoapi-test-config-pubsub.yml:315`, `tests/pygeoapi-test-config-pubsub.yml:340`, CITE `tests/cite/cite.config.yml:83`. No `tests/provider/test_xarray_edr*` exists.
- **Errors raised:** `ProviderNoDataError` for `KeyError` during selection (`pygeoapi/provider/xarray_edr.py:146`, `pygeoapi/provider/xarray_edr.py:224`) and empty time (`pygeoapi/provider/xarray_edr.py:283`); `ProviderQueryError` for 6-value bbox; `BaseEDRProvider.query` raises `ProviderQueryError('Query type not implemented')` (`pygeoapi/provider/base_edr.py:184`; executed E7 `area`).
- **Gotchas / conflicts:**
  - **State mutation:** `position` with `select_properties` overwrites `self._fields` (`pygeoapi/provider/xarray_edr.py:117`); later calls on the same instance see only those fields (executed E4: fields → `['SST']`).
  - `position` without `wkt` → `AttributeError` at `wkt.bounds` (`pygeoapi/provider/xarray_edr.py:158`; executed) — only the API's coords check prevents it.
  - **Subclassing narrows query types:** a subclass that defines no query methods fails at class creation with `ProviderInvalidDataError` (executed E8); one that overrides only `position` advertises only `['position']` (executed E9).

### `SensorThingsEDR` — `pygeoapi.provider.sensorthings_edr.SensorThingsEDRProvider`

- **Provider type(s) / base:** `edr`; `class SensorThingsEDRProvider(BaseEDRProvider, SensorThingsProvider)` (`pygeoapi/provider/sensorthings_edr.py:52`). Registered query types **`['items', 'locations', 'cube', 'area']`** (executed E0; methods at `pygeoapi/provider/sensorthings_edr.py:103`, `:115`, `:189`, `:240`). `items` is a stub (`pass`, `pygeoapi/provider/sensorthings_edr.py:113`) and triggers the base-class warning (`pygeoapi/provider/base_edr.py:77`); `/items` is served by a companion `feature` provider (`docs/source/publishing/ogcapi-edr.rst:122`).
- **Dependencies:** `requests` `Session` (`pygeoapi/provider/sensorthings.py:34`, `pygeoapi/provider/sensorthings.py:97`) — core deps only. Talks to an OGC SensorThings API v1.1 server (FROST in tests).
- **`data` forms:** STA service root URL, e.g. `https://…/FROST-Server/v1.1/` (`docs/source/publishing/ogcapi-edr.rst:114`); entity URL = `url_join(data, 'ObservedProperties')` (`pygeoapi/provider/sensorthings.py:582`).
- **Keys:**

  | Key | Type | Req? | Default | Meaning / notes | Source |
  |---|---|---|---|---|---|
  | `name` | str | yes | — | `SensorThingsEDR` | `pygeoapi/plugin.py:65` |
  | `type` | str | yes | — | `edr` | `docs/source/publishing/ogcapi-edr.rst:112` |
  | `data` | str (URL) | yes | — | STA root | `pygeoapi/provider/sensorthings.py:582` |
  | `entity` | str | — | forced | **Overwritten** to `ObservedProperties` (mutates the provider_def dict) | `pygeoapi/provider/sensorthings_edr.py:60` |
  | `time_field` | str | — | forced | Overwritten to `Datastreams/Observations/resultTime` | `pygeoapi/provider/sensorthings_edr.py:66` |
  | `id_field` | str | no | `@iot.id` | id of location Features (string ids get wrapped in quotes) | `pygeoapi/provider/sensorthings.py:598`, `pygeoapi/provider/sensorthings.py:309` |
  | `expand` | str | no | — | Replaces the **module-global** `EXPAND[entity]`; for this provider the value is immediately replaced again | `pygeoapi/provider/sensorthings.py:602`, `pygeoapi/provider/sensorthings_edr.py:63` |
  | `intralink` | bool | no | `false` | cross-collection links (reads the global pygeoapi config) | `pygeoapi/provider/sensorthings.py:605` |
  | `rel_link` | str | no | server URL | base for intralinks ("For pytest") | `pygeoapi/provider/sensorthings.py:608` |
  | `uri_field` | str | no | — | used when building Feature properties | `pygeoapi/provider/sensorthings.py:489` |
  | `properties` | list | no | `[]` | property whitelist in Feature properties | `pygeoapi/provider/sensorthings.py:477` |
  | `crs`/`storage_crs`/`always_xy` | | no | | EDR API-level only (as xarray-edr) | `pygeoapi/crs.py:556` |

- **Capabilities:**
  - `get_fields()`: one GET `ObservedProperties?$expand=Datastreams`; field id = `str(@iot.id)`, `title` = ObservedProperty `name`, `type: number`, `x-ogc-unit` = first Datastream's `unitOfMeasurement.symbol`; ObservedProperties without Datastreams are skipped (`pygeoapi/provider/sensorthings_edr.py:78`–`99`; `tests/provider/test_sensorthings_edr_provider.py:52`–`63`). `__init__` performs this request twice (base init calls the overridden `get_fields`, then `_fields` is reset and fetched again, `pygeoapi/provider/sensorthings.py:98`, `pygeoapi/provider/sensorthings_edr.py:67`–`68`) (read).
  - `select_properties` (from `parameter-name`) → `$filter=@iot.id eq '<p>' or …` on ObservedProperties, quoting **strings** (`pygeoapi/provider/sensorthings_edr.py:145`). The API always passes strings (split of the query string, `pygeoapi/api/environmental_data_retrieval.py:343`), so ids are sent quoted; the provider tests pass ints (`tests/provider/test_sensorthings_edr_provider.py:73`) — the API path is untested.
  - `locations(bbox, datetime_, select_properties, location_id)`:
    - **no `location_id`** → GeoJSON `FeatureCollection` of Things that have ≥1 observation (latest obs via `$top=1`) (`pygeoapi/provider/sensorthings_edr.py:162`–`187`; `tests/provider/test_sensorthings_edr_provider.py:66`–`80`). Duplicate Things appear once per matching Datastream (read: one Feature appended per datastream, `pygeoapi/provider/sensorthings_edr.py:183`).
    - **with `location_id`** (Thing id) → `CoverageCollection` of that Thing's datastreams, ordered by `phenomenonTime` (`pygeoapi/provider/sensorthings_edr.py:152`–`176`; `tests/provider/test_sensorthings_edr_provider.py:83`–`155`).
    - `bbox` → `st_within(Thing/Locations/location, geography'POLYGON(...)')` on Datastreams (`pygeoapi/provider/sensorthings.py:403`–`412`), replacing the location filter when both are given (`pygeoapi/provider/sensorthings_edr.py:167`) (read).
  - `cube(bbox, datetime_, select_properties)` and `area(wkt, …)` → `CoverageCollection` (`pygeoapi/provider/sensorthings_edr.py:238`, `pygeoapi/provider/sensorthings_edr.py:291`); area passes `str(shapely geometry)` into `geography'…'` (`pygeoapi/provider/sensorthings_edr.py:264`).
  - `datetime_` → `phenomenonTime ge/le/eq` filter on expanded Observations (`pygeoapi/provider/sensorthings_edr.py:353`–`375`).
  - Not honoured: `z`, `limit` (no `$top` on observations, no nextLink paging — TODO at `pygeoapi/provider/sensorthings_edr.py:426`), `format_`, `crs_transform_spec`, `instance`; `position`/`radius`/`trajectory`/`corridor`/`instances` not implemented.
  - CoverageJSON built by hand (§3).
- **Docs examples:** `docs/source/publishing/ogcapi-edr.rst:109`–`118` (EDR provider with only `type`, `name`, `data`, plus a companion `feature` SensorThings provider with `title_field`); matrix `docs/source/publishing/ogcapi-edr.rst:24`.
- **Tests/fixtures:** `tests/provider/test_sensorthings_edr_provider.py:35`–`41` (config `data: http://localhost:8888/FROST-Server/v1.1`, needs a live FROST server with the test dataset; not run here). No API-level tests, no config block in `tests/pygeoapi-test-config*.yml` (grep for `SensorThingsEDR`: none) (read).
- **Errors raised:** `ProviderConnectionError('Bad http response code')` (`pygeoapi/provider/sensorthings.py:350`); `ProviderQueryError` on JSON decode (`pygeoapi/provider/sensorthings.py:356`); `ProviderNoDataError('No data found')` when no coverage has data (`pygeoapi/provider/sensorthings_edr.py:410`) → 204.
- **Gotchas / conflicts:**
  - **Parameter identity mismatch:** `parameter_names`/`parameter-name` use ObservedProperty `@iot.id` (`"1"`, `"3"`), but CoverageJSON `parameters`/`ranges` keys are the ObservedProperty **name with spaces → `+`** (`pygeoapi/provider/sensorthings_edr.py:397`, `:403`; test `tests/provider/test_sensorthings_edr_provider.py:102`). Clients cannot join collection metadata to response ranges.
  - Coverage `id` is the Thing id, so a Thing with two datastreams yields two coverages with the same `id` (`tests/provider/test_sensorthings_edr_provider.py:117`–`123`).
  - `t` values prefer `resultTime` over `phenomenonTime` (`pygeoapi/provider/sensorthings_edr.py:429`); `dataType` is hard-coded `float` (`pygeoapi/provider/sensorthings_edr.py:324`) although non-numeric results are kept as strings (`pygeoapi/provider/sensorthings_edr.py:434`).
  - `self.expand` is the class-level alias of the module dict `EXPAND` (`pygeoapi/provider/sensorthings.py:78`), so `self.expand['ObservedProperties'] = …` (`pygeoapi/provider/sensorthings_edr.py:63`) mutates process-global state shared with any `SensorThings` feature provider on `ObservedProperties` (read).
  - `self.data.rstrip('/')` result is discarded (`pygeoapi/provider/sensorthings.py:579`) (read).
  - Docs matrix marks `Items` supported (`docs/source/publishing/ogcapi-edr.rst:24`) — only via the companion feature provider.

---

## 2. Coverage contract (`pygeoapi/api/coverages.py`)

**Routes:** `GET /collections/{id}/coverage` (`pygeoapi/flask_app.py:330`, called with `skip_valid_check=True`, `pygeoapi/flask_app.py:341`, so arbitrary `?f=` values reach the handler). Conformance classes: `pygeoapi/api/coverages.py:61`–`70` (core, oas30, html, geodata-coverage, coverage-subset, coverage-rangesubset, coverage-bbox, coverage-datetime).

**kwargs passed to `query()`** (built in `get_collection_coverage`, `pygeoapi/api/coverages.py:73`):

| kwarg | Always? | Value | Source |
|---|---|---|---|
| `bbox` | yes | `[]` or list of 4/6 floats from `validate_bbox` (invalid → **500**, not 400) | `pygeoapi/api/coverages.py:124`, `pygeoapi/api/coverages.py:121`, `pygeoapi/api/__init__.py:1128` |
| `bbox_crs` | only if `bbox-crs` given | **raw string** from the query string | `pygeoapi/api/coverages.py:130` |
| `datetime_` | yes | `None` or string after `validate_datetime` (range-checked against `extents.temporal`) | `pygeoapi/api/coverages.py:145` |
| `format_` | yes | `request.format or 'json'`; `'json'` when `f=html` | `pygeoapi/api/coverages.py:146`, `pygeoapi/api/coverages.py:150` |
| `properties` | only if given | list of names; each must be in `p.fields` else 400 | `pygeoapi/api/coverages.py:155`, `pygeoapi/api/coverages.py:160` |
| `subsets` | only if `subset` given | `{axis: [low, high] or [value]}` via `validate_subset`; keys must be ⊆ `p.axes` else 400 | `pygeoapi/api/coverages.py:176`, `pygeoapi/api/__init__.py:1288` |

- `F_COVERAGEJSON == F_JSON == 'json'` (`pygeoapi/formats.py:32`–`33`): providers see `format_='json'` for CoverageJSON. `?f=` is taken verbatim (case-sensitive, `pygeoapi/api/__init__.py:314`); `Accept` headers map only to `FORMAT_TYPES` (`pygeoapi/formats.py:43`–`51`: html, jsonld, json, png, jpeg, mvt, NetCDF).
- The plugin docs' raster example signature `query(self, bands=[], subsets={}, format_='json', **kwargs)` (`docs/source/plugins.rst:262`) is wrong for 0.24 — the API passes `properties`, not `bands`.

**Response handling** (`pygeoapi/api/coverages.py:193`–`238`): `mt = collection_def['format']['name']` is **required** (KeyError → 500 otherwise); `format_ == mt` → raw provider bytes with `Content-Type: format.mimetype` (`pygeoapi/api/coverages.py:199`); `f=html` → provider dict gets `query_path`, `dataset_path`, `collections_path`, `links` added and is rendered with `collections/coverage/index.html` (`pygeoapi/api/coverages.py:213`–`230`) — so the JSON result must be a dict; `json` → `Content-Type: application/prs.coverage+json` (`pygeoapi/api/coverages.py:235`); anything else → 400 format exception (`pygeoapi/api/coverages.py:238`), but only **after** the provider query ran.

**What providers must expose for the coverage API in 0.24** (read):
- `fields` / `get_fields()` → `{name: {'type': …, 'title'?: …, 'x-ogc-unit'?: …}}`. `type` is **mandatory** for `/schema` (`pygeoapi/api/__init__.py:1084`: `v['type']`), which rewrites `float` → `number` in place and serves `application/schema+json` (`pygeoapi/api/__init__.py:1108`); `properties` config key filters schema properties (`pygeoapi/api/__init__.py:1080`); coverage schemas get no geometry property (`pygeoapi/api/__init__.py:1072`). OpenAPI enumerates `p.fields.keys()` for `properties` (`pygeoapi/api/coverages.py:273`).
- `axes` (list of subset-able axis names) (`pygeoapi/api/coverages.py:176`).
- `_coverage_properties` dict with `width`, `height`, `resx`, `resy` (and optionally `time_range`, `restime`, `uad`) — read when describing a single collection (`pygeoapi/api/collection.py:313`–`329`). A custom coverage provider without it breaks `GET /collections/{id}` (see §E, pending/executed note). The plugin docs' raster template (`docs/source/plugins.rst:245`–`260`) sets only `num_bands`, `axes`, `_fields`.
- `filename` (optional) → `Content-Disposition` (`pygeoapi/api/coverages.py:196`).
- **No domainset/rangetype methods or endpoints exist in 0.24** (grep for `domainset|rangetype|domainSet|rangeType` over `pygeoapi/`: no hits) (read). `get_schema()` is **not** used for coverages (the schema endpoint builds from `fields`).
- Collection links for the default `coverage` provider: `…/coverage?f=json` typed `application/prs.coverage+json`, HTML, and `?f={format.name}` typed `format.mimetype` (`pygeoapi/api/collection.py:282`–`299`); schema/queryables links (`pygeoapi/api/collection.py:220`). No `crs`/`storageCrs` for coverages (`pygeoapi/api/collection.py:273`).
- `limits.max_distance_x/_y/_units` are documented for coverages/EDR (`docs/source/configuration.rst:580`) and present in the config schema (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:85`) but **not read anywhere in `pygeoapi/`** (grep) — doc/code conflict.

---

## 3. EDR contract (`pygeoapi/api/environmental_data_retrieval.py`)

**Registration** (`pygeoapi/provider/base_edr.py`): `EDR_QUERY_TYPES = ['position','radius','area','cube','trajectory','corridor','items','locations','instances']` (`pygeoapi/provider/base_edr.py:37`). `__init_subclass__` collects the names in **the subclass's own `cls.__dict__`** that are callable and in that list (`pygeoapi/provider/base_edr.py:61`–`64`); none → `ProviderInvalidDataError` at class-definition time (`pygeoapi/provider/base_edr.py:69`). Inherited methods do not count (executed E8/E9). `get_query_types()` returns that list (`pygeoapi/provider/base_edr.py:106`). `query(**kwargs)` dispatches `getattr(self, query_type)(**kwargs)` (`pygeoapi/provider/base_edr.py:182`–`186`), so **every query method receives all kwargs** and must accept `**kwargs`.

**`get_parameters()` / `x-ogc-unit` requirement:** builds CoverageJSON Parameter objects from `self.fields`: `id`, `type: Parameter`, `name` = `title`, `observedProperty {id, label.en = title, description.en?}`, `unit.symbol {value: x-ogc-unit, type: http://www.opengis.net/def/uom/UCUM/}` (`pygeoapi/provider/base_edr.py:129`–`152`). `conf_['x-ogc-unit']` is a hard key access (`pygeoapi/provider/base_edr.py:139`) — **a field without `x-ogc-unit` makes collection description fail** (KeyError → collection skipped in `/collections`, 500 for `/collections/{id}`, `pygeoapi/api/__init__.py:955`–`962`). `as_list=True` returns a list (`pygeoapi/provider/base_edr.py:155`).

**Collection metadata** (`pygeoapi/api/collection.py:404`–`450`): `parameter_names = p.get_parameters()` (dict keyed by field id) (`pygeoapi/api/collection.py:408`); `data_queries[qt] = {link: {href: …/{qt}, rel: data, variables: {query_type: qt}, type: application/vnd.cov+json (only when f=json)}}` for **every** registered qt incl. `items`/`instances` (`pygeoapi/api/collection.py:410`–`424`); JSON+HTML `rel=data` links per qt (`pygeoapi/api/collection.py:431`); dataset formatter links per qt (`pygeoapi/api/collection.py:443`); `crs` and `storageCrs` from provider config (`pygeoapi/api/collection.py:274`–`275`). EDR collections get **no** schema/queryables links (`pygeoapi/api/collection.py:220`) and no `extent.*.grid` enrichment (that is coverage-only).

**Routes** (`pygeoapi/flask_app.py:484`–`501`): `/position`, `/area`, `/cube`, `/radius`, `/trajectory`, `/corridor`, `/locations`, `/locations/<location_id>`, the same under `/instances/<instance_id>/…`, plus `/instances` and `/instances/<instance_id>` (→ `get_collection_edr_instances`, `pygeoapi/flask_app.py:515`–`521`). `query_type` = last path segment, or `locations` when `location_id` is set (`pygeoapi/flask_app.py:523`–`526`). `/items` is **not** an EDR route (features API).

**Request processing and kwargs** (`get_collection_edr_query`, `pygeoapi/api/environmental_data_retrieval.py:249`):
1. `instance` given and `not p.instance(instance)` → 400 (`pygeoapi/api/environmental_data_retrieval.py:286`). Base `instance()` returns a `NotImplementedError()` **object** (truthy) (`pygeoapi/provider/base_edr.py:97`; executed E1 `bool True`) → without an override every instance id is accepted.
2. `query_type not in p.get_query_types()` → 400 (`pygeoapi/api/environmental_data_retrieval.py:292`).
3. Dataset `formatters` extend allowed `?f=` values (`pygeoapi/api/environmental_data_retrieval.py:299`–`310`).
4. `crs` → `create_crs_transform_spec(provider_def, crs)` (400 if not in provider `crs` list or unparsable, `pygeoapi/crs.py:571`), plus `Content-Crs` header (`pygeoapi/api/environmental_data_retrieval.py:317`–`325`).
5. `datetime` → `validate_datetime(extents, …)` (`pygeoapi/api/environmental_data_retrieval.py:332`): a leading/trailing `/` is normalised to `..`, out-of-range vs `extents.temporal` → 400 (`pygeoapi/api/__init__.py:1205`–`1243`); if `extents.temporal.begin/end` are not YAML datetimes → 400 "Configured times should be RFC3339" (`pygeoapi/api/__init__.py:1196`).
6. `parameter-name` → comma-split list of strings (`pygeoapi/api/environmental_data_retrieval.py:341`–`343`); 400 only if **none** of them is a field (`pygeoapi/api/environmental_data_retrieval.py:395`: `not any(...)`) — partially-invalid lists pass through to the provider. Calls `p.get_fields()` (not `fields`).
7. `bbox` parsed **only** for `cube` and `locations` (4 or 6 numbers), required for `cube` (`pygeoapi/api/environmental_data_retrieval.py:346`–`351`); otherwise `bbox=None`.
8. `coords` → `shapely.wkt.loads()` → a shapely **geometry object** (not a string) (`pygeoapi/api/environmental_data_retrieval.py:362`); required for every query type except `cube` and `locations` (`pygeoapi/api/environmental_data_retrieval.py:368`); invalid WKT → 400.
9. `within`/`within-units` only for radius (`pygeoapi/api/environmental_data_retrieval.py:377`–`378`); `corridor-width`, `width-units`, `corridor-height`, `height-units` only for corridor (`pygeoapi/api/environmental_data_retrieval.py:384`–`387`) — raw strings.
10. `z` → `get_typed_value(z)` (`pygeoapi/api/environmental_data_retrieval.py:391`): `850` → int, `1.5` → float, `10/20` or `R5/…` or lists → str (`pygeoapi/util.py:115`–`125`); absent → `None`.
11. `limit` → `evaluate_limit(limit, server.limits, collection.limits)`: default `default_items`=10, cap `max_items`=10, `on_exceed` throttle/error (`pygeoapi/api/__init__.py:1307`–`1334`). **Always passed**, default 10.

The exact kwargs dict (`pygeoapi/api/environmental_data_retrieval.py:417`–`435`): `query_type`, `instance`, `format_` (= `request.format`, may be `None`), `datetime_`, `select_properties` (list, `[]` if absent), `wkt`, `z`, `bbox`, `within`, `within_units`, `corridor_width`, `width_units`, `corridor_height`, `height_units`, `limit`, `location_id`, `crs_transform_spec` (`CrsTransformSpec` or `None`).

**Per query type (what arrives populated):**

| query_type | `wkt` | `bbox` | other | Required by API |
|---|---|---|---|---|
| `position` | shapely geom (Point/MultiPoint/…) | `None` | `z`, `datetime_`, `select_properties` | `coords` |
| `area` | shapely Polygon/MultiPolygon | `None` | as above | `coords` |
| `cube` | `None` unless `coords` also sent | 4 or 6 floats | as above | `bbox` |
| `radius` | shapely geom | `None` | `within`, `within_units` (strings) | `coords` (within not enforced) |
| `trajectory` / `corridor` | shapely geom | `None` | corridor params | `coords` |
| `locations` (no id) | optional | optional | `location_id=None` | none |
| `locations/{id}` | optional | optional | `location_id=<str>` | none |
| `instances/{iid}/{qt}` | per qt | per qt | `instance=<iid>` | per qt + `p.instance(iid)` truthy |

**`locations` semantics** (API is agnostic; convention from SensorThingsEDR): `/locations` → GeoJSON `FeatureCollection` of locations; `/locations/{id}` → CoverageJSON (SensorThingsEDR returns a `CoverageCollection`). Whatever the provider returns is serialised with **`Content-Type: application/vnd.cov+json`** (`pygeoapi/api/environmental_data_retrieval.py:513`), even for GeoJSON — a media-type mismatch for `/locations` (read). OpenAPI documents `/locations` with `bbox`, `datetime`, `f` and `/locations/{locId}` with `locationId`, `datetime`, `parameter-name`, `crs`, `f` (`pygeoapi/api/environmental_data_retrieval.py:658`–`703`); `locations` is excluded from the generic query-path loop (`pygeoapi/api/environmental_data_retrieval.py:551`).

**`instances`:** only advertised when the provider class itself defines an `instances` method (registration rule). `GET …/instances` iterates `p.instances()` (`pygeoapi/api/environmental_data_retrieval.py:123`) — the base default returns a `NotImplementedError()` object (`pygeoapi/provider/base_edr.py:88`), so iterating it is a `TypeError` (500) if a provider advertises `instances` without overriding it (read). `GET …/instances/{iid}`: `p.instance(iid)` truthy → one instance; raising `ProviderItemNotFoundError` → 404 (`pygeoapi/api/environmental_data_retrieval.py:116`–`121`); returning `False` leaves `instances` unbound → `UnboundLocalError` (read). Each instance lists `data_queries` for all qts except `instances` (`pygeoapi/api/environmental_data_retrieval.py:152`–`167`). The plugin docs example `return instance in instances()` (`docs/source/plugins.rst:313`) is a NameError (should be `self.instances()`).

**Output handling** (`pygeoapi/api/environmental_data_retrieval.py:444`–`516`):
- `f=html`: the provider result must be a **dict**; the API adds `query_type` (capitalised), `query_path`, `title`, `dataset_path`, `collections_path`, `links` and renders `collections/edr/query.html` (`pygeoapi/api/environmental_data_retrieval.py:456`–`480`). The template branches on `data.type` ∈ {`Coverage`, `CoverageCollection`} (covjson-reader + leaflet-coverage) vs {`Feature`, `FeatureCollection`} (`pygeoapi/templates/collections/edr/query.html:17`, `pygeoapi/templates/collections/edr/query.html:24`); a `CoverageCollection` **must have top-level `parameters`** for the viewer (`pygeoapi/templates/collections/edr/query.html:100`–`101`).
- `?f=<formatter.f>` (e.g. `csv`): `formatter.write(data=…, options={content_crs, provider_def})` (`pygeoapi/api/environmental_data_retrieval.py:486`). The CSV formatter supports a `Coverage` whose `domainType` contains "point" and `CoverageCollection`s of point coverages, reading `domain.axes.t/x/y.values` and `ranges[param].values` (`pygeoapi/formatter/csv_.py:168`–`175`, `pygeoapi/formatter/csv_.py:196`–`207`) — i.e. PointSeries with ranges indexed by `t` only.
- else (json, jsonld, none): `to_json` with `Content-Type: application/vnd.cov+json` (`pygeoapi/api/environmental_data_retrieval.py:513`). **JSON-LD:** a `?f=jsonld` link is advertised (`pygeoapi/api/environmental_data_retrieval.py:472`–`475`) but there is no JSON-LD branch — `jsonld` falls through to plain CoverageJSON (read). Collection-level JSON-LD comes from the generic `jsonldify_collection` (`pygeoapi/api/__init__.py:1006`).
- Media-type inconsistency: EDR uses `application/vnd.cov+json`, Coverages uses `application/prs.coverage+json` (`pygeoapi/api/coverages.py:235`, `pygeoapi/api/collection.py:282`).
- Conformance: only EDR core (`pygeoapi/api/environmental_data_retrieval.py:69`).
- OpenAPI (`get_oas_30`): paths per registered qt (+ instance variants if `instances` registered) with `bbox` required for cube, `{qt}Coords.yaml` refs otherwise (`pygeoapi/api/environmental_data_retrieval.py:565`–`585`); 200 response schema `$ref` `…/schemas/coverageJSON.yaml` under `https://schemas.opengis.net/ogcapi/edr/1.0/openapi` (`pygeoapi/api/environmental_data_retrieval.py:612`, `pygeoapi/openapi.py:62`). Registered `items` also produces a bogus `/collections/{id}/items` EDR path with `itemsCoords.yaml` (read, from the same loop).

---

## 4. `sensorthings_edr.py` and `xarray_edr.py` as models for the "time series → EDR" template

| Concern | SensorThingsEDR | xarray-edr | Recommendation for our template |
|---|---|---|---|
| Locations | Things (`Datastreams/Thing/Locations`), GeoJSON FeatureCollection from `locations()`; id = Thing `@iot.id` (`pygeoapi/provider/sensorthings_edr.py:184`) | none (grid) | Implement `locations` → FeatureCollection (`id` = station id, point geometry, name/properties); `/locations/{id}` → CoverageJSON for that station |
| Parameters | ObservedProperties: id `@iot.id`, label = name, unit = first datastream symbol (`pygeoapi/provider/sensorthings_edr.py:88`–`98`) | data variables with `units`; label `long_name` | `get_fields()` → `{param_id: {type: number, title, x-ogc-unit (REQUIRED), description?}}` and **use the same `param_id` as CoverageJSON parameter/range key** |
| Observations | expanded `Datastreams/Observations($orderby=phenomenonTime)`, t = resultTime‖phenomenonTime (`pygeoapi/provider/sensorthings_edr.py:429`) | `.sel` on time coord | t = phenomenon/observation time, ISO 8601 with `Z`; sorted ascending |
| CoverageJSON | `CoverageCollection` (`domainType: PointSeries`, collection-level `parameters`, `coverages[]`), each `Coverage` with `domain.domainType: PointSeries`, axes `x:{values:[lon]}`, `y:{values:[lat]}`, `t:{values:[…]}`, referencing CRS84 + TemporalRS Gregorian, ranges `NdArray` `axisNames ['t']`, `shape [n]` (`pygeoapi/provider/sensorthings_edr.py:309`–`330`, `pygeoapi/provider/sensorthings_edr.py:386`–`391`) | single `Coverage`; `Grid` or `PointSeries`, ranges `['t','y','x']` `[n,1,1]` (executed E3) | Follow the SensorThingsEDR shape (it is what the CSV formatter and HTML viewer handle best); one `Coverage` per (location) with **all** requested parameters as ranges, `dataType` inferred |
| Query types | `locations`, `cube`, `area` (+ `items` stub) | `position`, `cube` | `locations`, `cube` (bbox → stations inside), `area` (polygon → stations inside), `position` (point → nearest/exact station, or stations intersecting a small buffer) — each defined **in the class body** |
| Filters | parameter-name → `$filter` on ObservedProperty ids; datetime → phenomenonTime filter; bbox → `st_within` | parameter-name → variable selection; datetime (date-granular instants) | apply parameter-name, datetime (instant = exact or containing interval), bbox/wkt consistently across query types |
| Limits/pagination | none (server default page size, e.g. 100 observations in tests `tests/provider/test_sensorthings_edr_provider.py:218`) | none | honour `limit` (API always passes it, default 10 — decide whether it caps locations or observations) and follow upstream paging |
| No data | `ProviderNoDataError` → 204 | same | same; note 204 has no body |

**Copy:** explicit per-query-type methods accepting `**kwargs`; `get_fields()` with `x-ogc-unit`; PointSeries domain structure and referencing constants (`GEOGRAPHIC_CRS`, `TEMPORAL_RS` at `pygeoapi/provider/sensorthings_edr.py:38`–`49`); `ProviderNoDataError` for empty results; collection-level `parameters` in `CoverageCollection`s.
**Avoid:** mutating `self._fields` or module globals; id/name key mismatch between `parameter_names` and ranges; duplicate coverage ids; hard-coded `dataType`; ignoring `limit`/paging; returning GeoJSON under a CoverageJSON media type without noting it; relying on `wkt.bounds` without a `None` check; `float()` on numpy arrays; implementing `instances` unless `instances()`/`instance()` are both real.

---

## 5. CoverageJSON validity

- **pygeoapi does not validate CoverageJSON anywhere** (read): no CoverageJSON schema file in the repo (`find`/`grep` for covjson/coveragejson schema: none); `jsonschema` is imported only by `pygeoapi/config.py`, `pygeoapi/openapi.py`, `pygeoapi/asyncapi.py`, `pygeoapi/api/admin.py` (config/OpenAPI validation). Provider output is serialised with `to_json` as-is.
- The only schema reference is the EDR OpenAPI response `$ref` to `https://schemas.opengis.net/ogcapi/edr/1.0/openapi/schemas/coverageJSON.yaml` (`pygeoapi/api/environmental_data_retrieval.py:612`, base URL `pygeoapi/openapi.py:62`).
- Known invalid/odd outputs observed: rasterio multi-band `null` parameter key (T2); rasterio projected `…/OGC/1.3/None` CRS id (R12); xarray `t` values without timezone (X4/E3); SensorThingsEDR range/parameter keys not matching `parameter_names`.
- **Official schema:** OGC CoverageJSON Community Standard 1.0 — see §E for the verified URLs (to be vendored into the contract harness; pin a commit).

---

## E. Execution log (condensed)

rasterio — project `.venv`, `tests/data/CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2` unless noted:
- R1 axes `['Long','Lat']`, crs CRS84, num_bands 1. R2 fields `{"1": {"title": "Temperature [C]", "type": "float", "_meta": {GRIB tags…}}}` (no `x-ogc-unit`: GRIB unit label `[C]` is used in CoverageJSON only). R3b width 2400, height 1201, resx 0.15000000000000002, resy 0.15.
- R4 `query(subsets={'Long':[5,6],'Lat':[5,6]})` → keys `domain, parameters, ranges, type`; `Grid`; axes `x {start 5, stop 6, num 8}`, `y {start 6, stop 5, num 8}`; parameter `TMP` (`observedProperty.id` `2-HTGL`, `unit.symbol` `[C]`); range `NdArray float ['y','x'] [8,8]`, 36/64 non-null.
- R5 bbox `[5,5,6,6]` → same shape. R6 `datetime_='1999-01-01'` ignored (full `[1201,2400]`). R7 native no-args → 1,057,634 bytes. R8 native bbox → 687 bytes. R9 bbox+subset → `ProviderQueryError`. R10 `bbox_crs` 4326/`'4326'` OK, URI → `CRSError`. R11 no `format` → `ProviderConnectionError(KeyError('format'))`. R12 HRDPS projected GRIB → axes `['x','y']`, crs `http://www.opengis.net/def/crs/OGC/1.3/None`; R12b lon/lat bbox → axes in degrees with ProjectedCRS referencing.
- T1–T5 synthetic 2-band GeoTIFF (EPSG:4326, band 1 description + `units` tag): titles `null`, no units; CoverageJSON `parameters`/`ranges` keyed `None`; shape `[3,3]` with 18 values; single band `properties=['2']` → 9 values; native GTiff subset 450 bytes (`II*\0`).

xarray — scratch venv:
- X0 `coads_sst.nc` coords `COADSX` (degrees_east, 180), `COADSY` (degrees_north, 90), `TIME` (12); vars SST/AIRT/UWND/VWND. X1 axes `['COADSX','COADSY','TIME']`, storage_crs `OGC:CRS84`. X2 fields e.g. `SST {type float, title SEA SURFACE TEMPERATURE, x-ogc-unit Deg C}`. X3 `restime` `30 days`, `time_range` `2000-01-16T06:00:00.000000000`…`2000-12-16T01:20:05.999999996`.
- X4 subset + datetime → `Grid`, `x {11.0→19.0, 5}`, `y {19.0→11.0, 5}`, `t ['2000-01-16T06:00:00.000000000']`, range `['t','y','x'] [1,5,5]`. X5 bbox only → `[12,90,180]` (bbox ignored). X6 bbox+datetime → `[1,5,5]`. X7 TIME subset → `[2,5,5]`. X8/X14 single-cell subset → `TypeError`. X9/X10 native NetCDF 3,147,886 bytes (with/without bbox). X11 native subset 22,681 bytes (`\x89HDF`). X12 datetime outside index → `ProviderQueryError`. X13 empty subset → `ProviderNoDataError`.
- Z `analysed_sst.zarr` (lon −28…−27, lat 40…41, 32 daily steps): fields analysed_sst/analysis_error/sea_ice_fraction (`mask` skipped, no units); `consolidated: true` → `ProviderConnectionError: '.zmetadata'`; subset `[32,11,11]`; `format_='zarr'` → 12,121-byte zip (`PK`); two temp files left in `$TMPDIR`.

xarray-edr — scratch venv, `coads_sst.nc`:
- E0 `query_types`: xarray-edr `['position','cube']`, SensorThingsEDR `['items','locations','cube','area']`, BaseEDRProvider `[]`. E1 `instances()`/`instance('x')` → `NotImplementedError()` objects, `bool(instance('x'))` → True. E2 parameters `AIRT, SST, UWND, VWND`.
- E3 position `POINT(11 11)`, `SST`, `2000-01-16` → `PointSeries`, axes `x [11.0]`, `y [11.0]`, `t [1 value]`, range `['t','y','x'] [1,1,1]`. E4 fields afterwards `['SST']` (mutation). E5 all params/all times → `[12,1,1]` each. E6 cube `[10,10,20,20]` + `2000-01-01/2000-03-01` → `Grid`, `SST [2,5,5]`.
- E7 `area` → `ProviderQueryError('Query type not implemented')`; 6-value bbox → `ProviderQueryError('z-axis not supported')`; LineString → `Grid [12,2,2]`; Polygon → `NotImplementedError`; `2000-01-17` → `ProviderNoDataError`; `z=850` without `z_field` ignored; single-cell cube → `TypeError`; no `wkt` → `AttributeError`.
- E8 empty subclass → `ProviderInvalidDataError`. E9 subclass overriding only `position` → `['position']`.

Not executed: SensorThingsEDR (requires a FROST server with the test dataset), S3 inputs (no s3fs/network), `open_mfdataset` (no dask).
