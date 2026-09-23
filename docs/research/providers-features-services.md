# Database- and service-backed feature providers (pygeoapi 0.24.0)

Phase 0 research for the pygeoapi Provider Builder. It is raw material for the provider descriptors and for the "SQL query → Features" and "HTTP JSON API → Features" template designs.

- **Ground truth:** pygeoapi **0.24.0** (tag 0.24.0, commit f765a64), cloned at `vendor/pygeoapi`. Citations are `path:line` relative to that repo root.
- **Executed checks** ran in a scratch venv (Python 3.12.14, `pygeoapi==0.24.0` from PyPI plus the provider drivers). Its installed `pygeoapi/` tree was compared with `diff -rq` and is **byte-identical** to `vendor/pygeoapi/pygeoapi`. The project `.venv` has none of these drivers (no `psycopg2`, `pymysql`, `oracledb`, `pymongo`, `elasticsearch`, `opensearch-py`, `sodapy`, `geoalchemy2`), so every provider in this file is "missing dependency" there. **(executed)**
- **Markers:** **(read)** means established by reading code or docs. **(executed)** means observed by running code. "(docs only)" means a key appears in the docs but the code never reads it. "(undocumented)" means the code reads a key the docs don't mention. **SECRET** marks credentials.
- **No database or search server was available** (Docker isn't running). DB-side behaviour is therefore read from code, and error paths were exercised against closed local ports (127.0.0.1:1) and unresolvable hosts.

---

## 0. Cross-cutting facts (apply to every provider below)

### 0.1 Keys every provider inherits from `BaseProvider`

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `name` | string | yes | — | Registry short name or dotted class path. A missing `name`, `type` or `data` raises a bare `RuntimeError('name/type/data are required')`. | `pygeoapi/provider/base.py:63`, `pygeoapi/provider/base.py:67` |
| `type` | string | yes | — | `feature` for everything in this file. | `pygeoapi/provider/base.py:64` |
| `data` | string or object | yes | — | Provider-specific (see each section). | `pygeoapi/provider/base.py:65` |
| `editable` | bool | no | `false` | Enables transactions at the API layer. Providers don't check it themselves. | `pygeoapi/provider/base.py:69`, `pygeoapi/api/itemtypes.py:788` |
| `count` | bool | no | `true` | Run a count for `numberMatched`. Passed through `str2bool`. **Ignored** by Elasticsearch, OpenSearch, OracleDB, SensorThings and ERDDAPTabledap (see below). | `pygeoapi/provider/base.py:70` |
| `options` | object | no | `None` | Free-form. Only the SQL providers and ERDDAPTabledap read it. | `pygeoapi/provider/base.py:71` |
| `id_field` | string | varies | `None` | | `pygeoapi/provider/base.py:72` |
| `uri_field` | string | no | `None` | Used by the item API for the canonical link. | `pygeoapi/provider/base.py:73`, `pygeoapi/api/itemtypes.py:949` |
| `time_field` | string | no | `None` | | `pygeoapi/provider/base.py:77` |
| `title_field` | string | no | `None` | HTML/templating only. | `pygeoapi/provider/base.py:78` |
| `properties` | list[string] | no | `[]` | Allow-list and order of output properties. Each provider applies it differently. | `pygeoapi/provider/base.py:79` |
| `include_extra_query_parameters` | bool | no | `false` | Passes unknown query params to the provider as `properties` tuples. | `pygeoapi/provider/base.py:81`, `pygeoapi/api/itemtypes.py:431` |
| `storage_crs` | CRS URI | no | CRS84 | Parsed with `get_crs()` at construction, so a malformed URI fails every request. | `pygeoapi/provider/base.py:86`, `pygeoapi/provider/base.py:87` |
| `crs` | list of CRS URIs | no | `[CRS84]` | **Not read by any provider in this file.** Used only by the API layer (`get_supported_crs_list`). See §6.1 for the ESRI `crs: 4326` case. | `pygeoapi/crs.py:111`, `pygeoapi/provider/base.py:91` |
| `always_xy` | bool | no | `false` | API-level CRS transform option. | `pygeoapi/crs.py:556` |

### 0.2 How the API layer calls these providers
- **A provider is constructed on every request.** `get_collection_items` calls `load_plugin` per request (`pygeoapi/api/itemtypes.py:339`), and so do the single-item, manage-item and queryables handlers (`pygeoapi/api/itemtypes.py:895`, `pygeoapi/api/itemtypes.py:767`, `pygeoapi/api/itemtypes.py:130`). Constructor work repeats on every request:
  - ESRI: an HTTP login plus a metadata fetch.
  - Socrata: a catalog lookup.
  - SensorThings: a `$top=1` fetch.
  - ERDDAPTabledap: a full-dataset download.
  - MongoDB: `create_index`.
  - Elasticsearch and OpenSearch: `ping`, `info` and a mapping fetch.

  Only `sql.py` caches anything (the engine and the reflected table model, via `functools.cache`: `pygeoapi/provider/sql.py:644`, `pygeoapi/provider/sql.py:708`). **(read)**
- **Every provider receives the same arguments:** `p.query(offset, limit, resulttype, bbox, datetime_, properties, sortby, skip_geometry, select_properties, crs_transform_spec, q, language, filterq)` (`pygeoapi/api/itemtypes.py:557`). A provider without a `filterq` parameter absorbs it in `**kwargs`, so **a CQL2 `filter=` sent to ESRI, Socrata, SensorThings or MongoDB is silently ignored and the unfiltered result comes back** (OracleDB passes it only to its SQL manipulator). **(read)**
- **Property filters** (`?name=value`) are forwarded only for keys in `p.fields`, unless `include_extra_query_parameters` is on (`pygeoapi/api/itemtypes.py:431`). `sortby` and `properties=` values are validated against `p.fields` (`pygeoapi/api/itemtypes.py:453`, `pygeoapi/api/itemtypes.py:468`). **(read)**
- **`bbox` reaches the provider already transformed** from `bbox-crs` (default CRS84) to `storage_crs` (`pygeoapi/api/itemtypes.py:411`, `pygeoapi/api/itemtypes.py:421`). **(read)**
- **`datetime_` is a string**, not a `datetime` object. `validate_datetime` returns the (normalized) input string (`pygeoapi/api/__init__.py:1245`). **(read)**
- **Transactions pass raw bytes.** `request.data` is `bytes` (`pygeoapi/api/__init__.py:335`), and `p.create(payload)` / `p.update(identifier, payload)` get it unchanged (`pygeoapi/api/itemtypes.py:804`, `pygeoapi/api/itemtypes.py:823`). Only providers that call `_load_and_prepare_item` (SQL, Elasticsearch, OpenSearch) parse it (`pygeoapi/provider/base.py:250`). A `TypeError` becomes HTTP 400 (`pygeoapi/api/itemtypes.py:805`). **(read)**
- **Error surfacing:** `ProviderGenericError` subclasses are mapped to `err.http_status_code` and `err.message` (`pygeoapi/api/itemtypes.py:564`). `message` returns `user_msg` or the class `default_msg` (`pygeoapi/error.py:52`), and nearly every provider raises with a positional message and no `user_msg`. So **HTTP clients see only generic text** such as "connection error (check logs)" (`pygeoapi/provider/base.py:329`), and the detail lives in `str(exc)`. Our probe runner should read `str(exc)` plus the exception type and chain. Any non-`ProviderGenericError` exception escapes as a framework 500. **(read)**

### 0.3 Exception classes used here (`pygeoapi/provider/base.py`)
| Class | HTTP | default_msg | Line |
|---|---|---|---|
| `ProviderGenericError` | 500 | generic error (check logs) | `pygeoapi/provider/base.py:322` |
| `ProviderConnectionError` | 500 | connection error (check logs) | `pygeoapi/provider/base.py:327` |
| `ProviderTypeError` | 400 | invalid provider type | `pygeoapi/provider/base.py:332` |
| `ProviderInvalidQueryError` | 400 | query error | `pygeoapi/provider/base.py:338` |
| `ProviderQueryError` | 500 | query error (check logs) | `pygeoapi/provider/base.py:345` |
| `ProviderItemNotFoundError` | 404 | identifier not found | `pygeoapi/provider/base.py:350` |
| `ProviderNotFoundError` | 500 | (inherits generic) | `pygeoapi/provider/base.py:364` |
| `ProviderInvalidDataError` | 500 | (inherits generic) | `pygeoapi/provider/base.py:374` |

---

## 1. SQL family (`pygeoapi/provider/sql.py`)

### 1.0 Shared base `sql.GenericSQLProvider` (not registered; base of PostgreSQL and MySQL)
- **Base class:** `GenericSQLProvider(BaseProvider)` (`pygeoapi/provider/sql.py:87`). Its constructor takes `(provider_def, driver_name, extra_conn_args)` (`pygeoapi/provider/sql.py:93`). Subclasses set `default_port` and implement `_get_bbox_filter`, which raises `NotImplementedError` in the base (`pygeoapi/provider/sql.py:528`). **(read)**
- **Constructor order** (`pygeoapi/provider/sql.py:115` onward):
  1. Read `table`, `id_field` and `geom_field`.
  2. `options = provider_def.get('options', {}) | extra_conn_args` (`pygeoapi/provider/sql.py:127`).
  3. `store_db_parameters(...)` (`pygeoapi/provider/sql.py:128`).
  4. `get_engine(...)`, which is cached (`pygeoapi/provider/sql.py:129`).
  5. `get_table_model(...)`, cached reflection (`pygeoapi/provider/sql.py:139`).
  6. `get_fields()` (`pygeoapi/provider/sql.py:143`).

  The first actual DB connection happens in step 5 (reflection). **(read)**
- **`data` forms** (`store_db_parameters`, `pygeoapi/provider/sql.py:583`):
  1. **String.** The whole string is passed to `create_engine` as the URL (`pygeoapi/provider/sql.py:598`, `pygeoapi/provider/sql.py:694`). **The subclass `driver_name` is then not used at all**, so the URL scheme picks the DBAPI: `postgresql://` means SQLAlchemy's default psycopg2, `postgresql+psycopg://` would select psycopg 3, and `mysql://` means mysqlclient (MySQLdb), not PyMySQL. Host, port, dbname and user fields are all `None`. Only `options.search_path` can set the schema list. **(read)**
  2. **Object.** It is decomposed into fields and rebuilt with `URL.create(drivername=driver_name, username, password, host, port=int(port), database)` (`pygeoapi/provider/sql.py:674`). **(read)**
- **Merging `options`:** in `options | extra_conn_args` **the right side wins**, so a user's `application_name`/`client_encoding` (PostgreSQL) or `charset` (MySQL) is silently overridden (`pygeoapi/provider/sql.py:127`, `pygeoapi/provider/sql.py:811`, `pygeoapi/provider/sql.py:851`). `options: null` in YAML would make `None | dict` raise `TypeError`. **(read)**
- **Where options go:** after `search_path` is popped, the non-dict options become `db_options` (`pygeoapi/provider/sql.py:623`). `get_engine` pops the five pool keys (defaults in `POOL_OPTION_DEFAULTS`, `pygeoapi/provider/sql.py:635`) and passes **everything else to the DBAPI `connect()` as `connect_args`** (`pygeoapi/provider/sql.py:689`, `pygeoapi/provider/sql.py:696`). An option the driver doesn't know therefore fails at first connect, with a driver-specific error. **(read)**
- **Engine cache and pool:** `get_engine` is `@functools.cache`d on *all* its arguments (driver, host, port, db, user, password, DSN and every connect arg) (`pygeoapi/provider/sql.py:644`). Providers with identical connection settings share one engine, and one SQLAlchemy `QueuePool`, per worker process. Any differing option (including pool options) creates a separate engine, as the docs note (`docs/source/publishing/ogcapi-features.rst:725`). Consequences:
  - All option values must be hashable, so a list-valued option (other than `search_path`) raises `TypeError: unhashable type`.
  - Engines are never disposed for the life of the process. **(read)**
- **Reflection cache:** `get_table_model(table, id_field, search_path, engine)` is also `@functools.cache`d (`pygeoapi/provider/sql.py:708`). A column added to the table needs a **process restart** before pygeoapi sees it. **(read)**

#### Keys (shared by `PostgreSQL` and `MySQL`)

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | string (SQLAlchemy URL) **or** object | yes | — | String: a URL such as `postgresql://user:pass@host:5432/db`. **SECRET** when it embeds a password. | `pygeoapi/provider/sql.py:597` |
| `data.host` | string | no | `None` (driver default; libpq uses the local Unix socket) | For PostgreSQL, a directory path selects a Unix socket, e.g. `/cloudsql/<instance>` (`docs/source/publishing/ogcapi-features.rst:735`). | `pygeoapi/provider/sql.py:604` |
| `data.port` | int (or numeric string) | no | 5432 (PostgreSQL), 3306 (MySQL) | Cast with `int()` (`pygeoapi/provider/sql.py:679`). | `pygeoapi/provider/sql.py:605`, `pygeoapi/provider/sql.py:797`, `pygeoapi/provider/sql.py:837` |
| `data.dbname` | string | no* | `None` | *Required in practice. | `pygeoapi/provider/sql.py:607` |
| `data.database` | string | no | `None` | (undocumented) Alias of `dbname`. `dbname` wins. | `pygeoapi/provider/sql.py:607` |
| `data.user` | string | no* | `None` | | `pygeoapi/provider/sql.py:603` |
| `data.password` | string | no* | `None` | **SECRET** | `pygeoapi/provider/sql.py:610` |
| `data.search_path` | list[string] | no | `['public']` | Schemas tried **in order** during reflection; the first schema containing the table wins (`pygeoapi/provider/sql.py:729`, `pygeoapi/provider/sql.py:746`). **Not** sent to the server as `SET search_path`. For MySQL it must be the database name (`docs/source/publishing/ogcapi-features.rst:250`). | `pygeoapi/provider/sql.py:615` |
| `data.query` | any | no | — | (undocumented) Stored as `self.db_query` but **never used**, so URL query parameters can't be passed this way. | `pygeoapi/provider/sql.py:609` |
| `options.search_path` | list[string] | no | `['public']` | Used only when `data.search_path` is absent, and popped out of the options. The only way to set schemas with a DSN string. If both are set, `options.search_path` stays in `options` and is forwarded to the driver (a list: see the hashability note). | `pygeoapi/provider/sql.py:616` |
| `options.pool_size` | int | no | 5 | SQLAlchemy `QueuePool`; schema-typed. | `pygeoapi/provider/sql.py:636`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:737` |
| `options.max_overflow` | int | no | 10 | | `pygeoapi/provider/sql.py:637`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:740` |
| `options.pool_recycle` | int (s) | no | -1 (never) | | `pygeoapi/provider/sql.py:638`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:743` |
| `options.pool_timeout` | int (s) | no | 30 | | `pygeoapi/provider/sql.py:639`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:746` |
| `options.pool_pre_ping` | bool | no | `true` | | `pygeoapi/provider/sql.py:640`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:749` |
| `options.connect_timeout` | int (s) | no | driver default | Forwarded to the DBAPI. Valid for both psycopg2 and PyMySQL. | `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:725`, `docs/source/publishing/ogcapi-features.rst:690` |
| `options.tcp_user_timeout`, `options.keepalives`, `options.keepalives_idle`, `options.keepalives_count`, `options.keepalives_interval` | int | no | driver default | **libpq (psycopg2) parameters only.** The schema types them for every provider; the MySQL docs example uses them too, which PyMySQL doesn't accept (§1.2). | `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:727`, `docs/source/publishing/ogcapi-features.rst:275` |
| `options.<any other scalar>` | scalar | no | — | Forwarded to DBAPI `connect()`. Dict-valued options are silently dropped (`pygeoapi/provider/sql.py:626`). | `pygeoapi/provider/sql.py:623` |
| `table` | string | **yes** | — | A table, view or materialized-view **name** (reflection uses `views=True`: `pygeoapi/provider/sql.py:733`). A missing key raises a bare `KeyError`. Don't schema-qualify it; use `search_path`. | `pygeoapi/provider/sql.py:115` |
| `id_field` | string | **yes** | — | Declared as the model's primary key, so views work too (`pygeoapi/provider/sql.py:764`). A missing key raises a bare `KeyError`. | `pygeoapi/provider/sql.py:116` |
| `geom_field` | string | no | `geom` | If the column doesn't exist it's silently skipped and `geometry` is `null` (`pygeoapi/provider/sql.py:576`). | `pygeoapi/provider/sql.py:117` |
| `time_field` | string | cond. | `None` | Required for `datetime=`; without it a bare `ProviderQueryError()` is raised. | `pygeoapi/provider/sql.py:534` |
| `properties` | list[string] | no | `[]` | Restricts loaded columns and output. | `pygeoapi/provider/sql.py:562`, `pygeoapi/provider/sql.py:329` |
| `count` | bool | no | `true` | `numberMatched` via `Query.count()`. | `pygeoapi/provider/sql.py:211` |
| `editable` | bool | no | `false` | Create/replace/delete. | `pygeoapi/provider/base.py:69` |
| `storage_crs` | CRS URI | no | CRS84 | Its SRID is used for the PostGIS bbox envelope and for inserted geometries (`pygeoapi/provider/sql.py:824`, `pygeoapi/provider/sql.py:471`). Output is transformed in Python, not in the DB. | `pygeoapi/provider/base.py:86` |

### `PostgreSQL` — `sql.PostgreSQLProvider`
- **Provider type(s) and base:** `feature` only. `PostgreSQLProvider(GenericSQLProvider)` (`pygeoapi/provider/sql.py:793`), registered as `PostgreSQL` (`pygeoapi/plugin.py:62`). **(read)**
- **Dependencies:**
  - Imports: `geoalchemy2` (`pygeoapi/provider/sql.py:49`), `pygeofilter.backends.sqlalchemy` (`pygeoapi/provider/sql.py:52`), `shapely` (`pygeoapi/provider/sql.py:53`) and `sqlalchemy` (`pygeoapi/provider/sql.py:55`). The DBAPI is `psycopg2`, from the fixed driver string `postgresql+psycopg2` (`pygeoapi/provider/sql.py:810`).
  - pip: `SQLAlchemy`, `GeoAlchemy2`, `pygeofilter[backend-sqlalchemy]`, and **`psycopg2-binary` or `psycopg2`**. The docs say `psycopg2-binary` (`docs/source/publishing/ogcapi-features.rst:640`); `requirements-provider.txt` says `psycopg2` (`requirements-provider.txt:16`). Both install the `psycopg2` module. psycopg 3 is used only if a DSN string says `postgresql+psycopg://`.
  - PostGIS must be installed in the database (`docs/source/publishing/ogcapi-features.rst:642`). **(read)**
- **Extra DBAPI args:** `client_encoding=utf8` and `application_name=pygeoapi` (`pygeoapi/provider/sql.py:812`). Users can't override them (§1.0). **(read)**
- **`data` forms:** object or URL string (§1.0). Both are shown in the docs (`docs/source/publishing/ogcapi-features.rst:649`, `docs/source/publishing/ogcapi-features.rst:668`).
- **Keys:** as in the shared table.
- **Capabilities** (all **read**):
  - `offset`/`limit`: `ORDER BY … OFFSET … LIMIT`, with the default order being `id_field ASC` (`pygeoapi/provider/sql.py:225`, `pygeoapi/provider/sql.py:491`).
  - `bbox`: `ST_Intersects(ST_MakeEnvelope(minx, miny, maxx, maxy, <storage SRID>), geom)` (`pygeoapi/provider/sql.py:825`, `pygeoapi/provider/sql.py:828`).
  - `datetime_`: an instant uses `=`, a range uses `BETWEEN`/`>=`/`<=` on `time_field`. Values are compared as the ISO strings the API passes (`pygeoapi/provider/sql.py:540`).
  - `properties`: equality, AND-ed (`pygeoapi/provider/sql.py:517`).
  - `sortby` (`pygeoapi/provider/sql.py:485`).
  - `select_properties`: `load_only` (`pygeoapi/provider/sql.py:578`).
  - `skip_geometry` (`pygeoapi/provider/sql.py:566`).
  - `resulttype=hits` (`pygeoapi/provider/sql.py:218`).
  - `filterq` (CQL2 text or JSON), via `pygeofilter` `to_filter`, with every table column mapped (`pygeoapi/provider/sql.py:504`).
  - `q`: **accepted and ignored** (`pygeoapi/provider/sql.py:156`).
  - `get()`: adds `prev`/`next` ids (`pygeoapi/provider/sql.py:350`).
  - Transactions: `create` (`pygeoapi/provider/sql.py:363`), `update` via `session.merge` (`pygeoapi/provider/sql.py:401`) and `delete` (`pygeoapi/provider/sql.py:417`).
  - `get_schema`/`get_domains`: not implemented.
  - CRS: **no `@crs_transform` decorator.** It transforms natively in Python with `get_transform_from_spec` (`pygeoapi/provider/sql.py:221`, `pygeoapi/provider/sql.py:451`).
- **Docs examples:**
  - Dict form, `docs/source/publishing/ogcapi-features.rst:644` (block ends line 659). Keys: `type`, `name`, `data.{host, port, dbname, user, password, search_path}`, `id_field`, `table`, `geom_field`, `count`.
  - DSN form, `docs/source/publishing/ogcapi-features.rst:663`. Keys: `data` (string), `id_field`, `table`, `geom_field`.
  - Options and pool, `docs/source/publishing/ogcapi-features.rst:676` (to line 723). Keys: `options.{connect_timeout, tcp_user_timeout, keepalives, keepalives_idle, keepalives_count, keepalives_interval, pool_size, max_overflow, pool_recycle, pool_timeout, pool_pre_ping}`.
  - Cloud SQL socket, `docs/source/publishing/ogcapi-features.rst:729`. Keys: `data.{host: /cloudsql/…, dbname, user, password}`, `id_field`, `table`, `count`.
  - CQL: `docs/source/cql2.rst:29`, `docs/source/cql2.rst:34`.
- **Tests/fixtures:**
  - `tests/provider/test_postgresql_provider.py`: its fixture is parametrized over dict and DSN forms (`tests/provider/test_postgresql_provider.py:82`); the DSN variant sets `options.search_path` (`tests/provider/test_postgresql_provider.py:104`). It also covers a materialized view (`tests/provider/test_postgresql_provider.py:215`), error messages (`tests/provider/test_postgresql_provider.py:522`), bad credentials (`tests/provider/test_postgresql_provider.py:542`) and engine caching (`tests/provider/test_postgresql_provider.py:560`).
  - `tests/provider/test_sql_pool_options.py`: pool/connect-arg separation, mocked (`tests/provider/test_sql_pool_options.py:40`).
  - Config blocks: `tests/pygeoapi-test-config-postgresql.yml:132` (`hot_osm_waterways`, `editable: true`, libpq options, `crs` list, `storage_crs` EPSG:4326) and `tests/pygeoapi-test-config-postgresql.yml:188` (`dummy_naming_conflicts`, `search_path: [dummy]`).
  - Data: `tests/data/hotosm_bdi_waterways.sql.gz` (schema `osm`, table `hotosm_bdi_waterways`, materialized view `hotosm_bdi_drains`), `tests/data/dummy_data.sql:51` (`naming_conflicts_table`) and `tests/data/dummy_types_data.sql:27` (`public.foo`).
- **Errors raised** (the texts come from the tests' `match=` patterns; **read**):
  - Unreachable host, wrong port, bad password, unknown database, or `data` without a host: SQLAlchemy `OperationalError` during reflection becomes `ProviderConnectionError("Could not connect to <engine url with password masked as ***> (password hidden).")` (`pygeoapi/provider/sql.py:737`). Test samples: `Could not connect to postgresql+psycopg2://:5432 (password hidden).` and `…bad_user:***@…` (`tests/provider/test_postgresql_provider.py:526`, `tests/provider/test_postgresql_provider.py:546`). **The driver's own message** ("connection refused", "password authentication failed", "could not translate host name") **is discarded**, so plain-language classification has to re-inspect `__context__`. (Executed checks: §1.4.)
  - Table not in any `search_path` schema: `ProviderQueryError("Table '<t>' not found in schema '<last schema>' on <url>.")` (`pygeoapi/provider/sql.py:740`).
  - `id_field` not a column: `ProviderQueryError('No such id_field column (<id>) on <schema>.<table>.')` (`pygeoapi/provider/sql.py:767`).
  - Unknown item id: `ProviderItemNotFoundError('No such item: <id_field>=<id>.')` (`pygeoapi/provider/sql.py:323`).
  - Bad inserted properties: `ProviderInvalidDataError(str(e))` (`pygeoapi/provider/sql.py:479`).
  - Missing `table` or `id_field` raises a bare `KeyError`. A missing `psycopg2` raises `ModuleNotFoundError` from `create_engine`. A missing `geoalchemy2` fails at import. Query-time SQL errors (for example permission denied) aren't wrapped.
- **Gotchas / conflicts:**
  - `search_path` is used only to *find* the table. After that the model is schema-qualified.
  - A reflected-model cache per process means schema changes need a restart.
  - Output CRS transformation is done per feature in Python (shapely), not with `ST_Transform`.
  - The `hot_osm_waterways` test config links to `pygeoapi/provider/postgresql.py`, which no longer exists (`tests/pygeoapi-test-config-postgresql.yml:121`).
  - The config schema defines the typed `options` block under `definitions.provider.properties.PostgreSQL` and applies it to **every** provider's `options` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595`).

### `MySQL` — `sql.MySQLProvider`
- **Provider type(s) and base:** `feature`. `MySQLProvider(GenericSQLProvider)` (`pygeoapi/provider/sql.py:833`), registered as `MySQL` (`pygeoapi/plugin.py:56`). **(read)**
- **Dependencies:** the same SQLAlchemy stack as PostgreSQL, with DBAPI **PyMySQL** from driver `mysql+pymysql` (`pygeoapi/provider/sql.py:850`). pip: `SQLAlchemy`, `GeoAlchemy2`, `pygeofilter[backend-sqlalchemy]`, `pymysql` (`docs/source/publishing/ogcapi-features.rst:235`, `requirements-provider.txt:22`). A DSN string must say `mysql+pymysql://…` (as in `tests/provider/test_mysql_provider.py:74`); `mysql://` asks for mysqlclient. **(read)**
- **Extra DBAPI arg:** `charset=utf8mb4`, which can't be overridden (`pygeoapi/provider/sql.py:852`).
- **Keys:** the shared table, with these differences:
  - Default port 3306 (`pygeoapi/provider/sql.py:837`).
  - **`search_path` must name the database.** The default `['public']` makes reflection look in a database called `public` (`docs/source/publishing/ogcapi-features.rst:250`, `pygeoapi/provider/sql.py:729`).
  - Only `connect_timeout` and the pool keys are valid options. The libpq keepalive options in the docs example aren't accepted by PyMySQL (§1.2).
- **Capabilities:** the same as PostgreSQL, with these differences (**read**):
  - The bbox uses `MBRContains(ST_GeomFromText('POLYGON(...)'), geom)` (`pygeoapi/provider/sql.py:872`). That is **"within the bbox MBR" semantics, not intersects**. The polygon carries **no SRID**, so SRID 0. The fixture's `POINT` column has no SRID (`tests/data/mysql_data.sql:13`). With an SRID-4326 column MySQL 8 is expected to reject the mixed-SRID comparison (not verified: no MySQL available).
  - `get()` is overridden to require an exact string match (`pygeoapi/provider/sql.py:898`), because MySQL comparisons coerce types and case.
- **Docs examples:**
  - `docs/source/publishing/ogcapi-features.rst:239`. Keys: `data.{host, port, dbname, user, password, search_path}`, `id_field`, `table`, `geom_field`.
  - `docs/source/publishing/ogcapi-features.rst:258`. The same, plus `options.{connect_timeout, tcp_user_timeout, keepalives, keepalives_idle, keepalives_count, keepalives_interval}`.
- **Tests/fixtures:** `tests/provider/test_mysql_provider.py:53` (dict and `mysql+pymysql://` DSN, `options.connect_timeout: 10`, `search_path: ['test_geo_app']`). `test_valid_connection_options` only checks key *names* and never connects with the libpq options (`tests/provider/test_mysql_provider.py:81`). Data: `tests/data/mysql_data.sql:9` (table `location`, PK `locationID`, `POINT` column `locationCoordinates`). There's no test-config YAML block.
- **Errors:** the same wrapping as PostgreSQL. PyMySQL's `OperationalError` (for example `(2003, "Can't connect to MySQL server on …")`) becomes `ProviderConnectionError('Could not connect to mysql+pymysql://… (password hidden).')`. See §1.4 for executed samples.
- **Gotchas / conflicts:** the docs example passes libpq keepalive options to PyMySQL (§1.2). `MBRContains` means features straddling the bbox edge are excluded, unlike PostgreSQL.

### 1.1 How the SQL query is built (model for the "SQL query → Features" template)
- **Reflection, not raw SQL.** `MetaData().reflect(bind=engine, schema=<first schema>, only=[table], views=True)` (`pygeoapi/provider/sql.py:732`), then `PrimaryKeyConstraint(id_field)` is appended (`pygeoapi/provider/sql.py:764`) and `automap_base().prepare()` builds an ORM class (`pygeoapi/provider/sql.py:770`). Tables, **views and materialized views** all work (the fixture's `hotosm_bdi_drains` is a materialized view: `tests/provider/test_postgresql_provider.py:218`). **(read)**
- **Queries go through the SQLAlchemy ORM with bound parameters:** `session.query(model).filter(property_filters).filter(cql_filters).filter(bbox_filter).filter(time_filter).options(load_only(...))` (`pygeoapi/provider/sql.py:196`). Then:
  - `numberMatched` comes from `Query.count()`, a `SELECT count(*) FROM (subquery)` (`pygeoapi/provider/sql.py:212`).
  - Rows come from `.order_by(...).offset(offset).limit(limit)` (`pygeoapi/provider/sql.py:225`).
  - Property values, datetime strings and CQL literals are bound parameters. Column names come only from the reflected model (`getattr(model, name)`), so identifiers can't be injected. **(read; SQL shape executed in §1.4)**
- **Can `table` hold a custom `SELECT`?** **No.** `table` is a reflection target name inside `only=[...]`, and `metadata.tables[f'{schema}.{table_name}']` has to exist (`pygeoapi/provider/sql.py:762`). A `SELECT …` string fails with `ProviderQueryError("Table '…' not found …")`. **To publish a custom query, create a VIEW (or MATERIALIZED VIEW) in the database and point `table` at it**, with `id_field` naming a unique column of the view. **(read; executed with SQLite in §1.4)**
- **Implications for our template:**
  1. The spec engine can reuse the same pattern: reflect or `text().columns()`, bind every value, and whitelist column names from the reflected schema.
  2. A true "custom SELECT" needs either (a) a DB view created by the steward or DBA, or (b) our engine wrapping the SELECT as a subquery (`select(...).subquery()`) with an explicitly declared id column, which pygeoapi core can't do.
  3. Keep pygeoapi's per-process engine caching, but add connect/statement timeouts (core has none unless `options.connect_timeout` is set).

### 1.2 `options` validity by driver (docs vs code)
The PostgreSQL and MySQL docs share one options list (`docs/source/publishing/ogcapi-features.rst:270`, `docs/source/publishing/ogcapi-features.rst:688`), and the config schema types it for all providers (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:725`). Everything that isn't a pool key goes into `create_engine(connect_args=...)` (`pygeoapi/provider/sql.py:696`). `tcp_user_timeout` and the `keepalives*` keys are libpq connection parameters that PyMySQL's `connect()` doesn't accept. Executed result: §1.4.

### 1.3 Descriptor hints
Two variants, `fields` (object) and `dsn` (a string marked **SECRET**). `table` needs a picker for views as well as tables. Keep `search_path` under `data` for the object variant and under `options` for the DSN variant. For MySQL, default `search_path` to `[<dbname>]`.

### 1.4 Executed checks (SQL)
_(filled in below after execution)_

---

## 2. `OracleDB` — `oracle.OracleProvider`
- **Provider type(s) and base:** `feature`. `OracleProvider(BaseProvider)` (`pygeoapi/provider/oracle.py:386`), registered as `OracleDB` (`pygeoapi/plugin.py:58`). The tests construct it with `name: "Oracle"` because they bypass the registry (`tests/provider/test_oracle_provider.py:139`). **(read)**
- **Dependencies:** `oracledb` (`pygeoapi/provider/oracle.py:38`) and `pyproj` (`pygeoapi/provider/oracle.py:39`). pip: `oracledb` (`docs/source/publishing/ogcapi-features.rst:443`, `requirements-provider.txt:14`). python-oracledb runs in thin mode unless `init_oracle_client` is set (`pygeoapi/provider/oracle.py:148`). **No SQLAlchemy**: it builds raw SQL strings with bind variables. **(read)**
- **`data` forms:** **object only** (`self.conn_dic = provider_def["data"]`, `pygeoapi/provider/oracle.py:403`). A DSN is assembled with `oracledb.makedsn(host, port, service_name=|sid=)` or taken from `tns_name` (`pygeoapi/provider/oracle.py:175`, `pygeoapi/provider/oracle.py:192`, `pygeoapi/provider/oracle.py:204`). A string `data` fails because it has no `.get`. **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data.host` | string | cond. | — | Required with `service_name` or `sid`; otherwise `ProviderConnectionError("Host must be set …")`. | `pygeoapi/provider/oracle.py:170`, `pygeoapi/provider/oracle.py:176` |
| `data.port` | int | no | 1521 | | `pygeoapi/provider/oracle.py:177` |
| `data.service_name` | string | one of three | — | Checked first. | `pygeoapi/provider/oracle.py:164` |
| `data.sid` | string | one of three | — | | `pygeoapi/provider/oracle.py:182` |
| `data.tns_name` | string | one of three | — | A tnsnames.ora alias. Required for wallet auth. | `pygeoapi/provider/oracle.py:199`, `pygeoapi/provider/oracle.py:156` |
| `data.tns_admin` | path | no | env `TNS_ADMIN` | Passed as `config_dir`. | `pygeoapi/provider/oracle.py:236` |
| `data.external_auth` | `"wallet"` | no | — | Wallet (external) auth. | `pygeoapi/provider/oracle.py:151`, `pygeoapi/provider/oracle.py:226` |
| `data.init_oracle_client` | bool | no | `false` | Calls `oracledb.init_oracle_client()` (thick mode). | `pygeoapi/provider/oracle.py:147` |
| `data.user` | string | yes | — | Also the default table owner. | `pygeoapi/provider/oracle.py:244`, `pygeoapi/provider/oracle.py:267` |
| `data.password` | string | yes | — | **SECRET** | `pygeoapi/provider/oracle.py:245` |
| `table` | string | yes | — | `OWNER.TABLE` is allowed. Tables, views, materialized views, and private or public synonyms are all resolved. | `pygeoapi/provider/oracle.py:402`, `pygeoapi/provider/oracle.py:262`, `pygeoapi/provider/oracle.py:320` |
| `id_field` | string | yes (in practice) | — | Used in `get`, `prev`/`next` and the feature id. Lower-cased output keys must match it. | `pygeoapi/provider/oracle.py:965`, `pygeoapi/provider/oracle.py:1037` |
| `geom_field` | string | **yes** | — | An `SDO_GEOMETRY` column. A missing key raises a bare `KeyError`. | `pygeoapi/provider/oracle.py:404` |
| `title_field` | string | no | — | Present in every docs example; HTML only. | `docs/source/publishing/ogcapi-features.rst:467` |
| `sdo_operator` | `sdo_filter` \| `sdo_relate` | no | **`sdo_filter`** | The bbox operator. | `pygeoapi/provider/oracle.py:417` |
| `sdo_param` | string | no | `None`; with `sdo_relate`, `mask=anyinteract` | | `pygeoapi/provider/oracle.py:416`, `pygeoapi/provider/oracle.py:479` |
| `mandatory_properties` | list[string] | no | `None` | Query fails with `ProviderInvalidQueryError` if one is missing. The special name `bbox` requires a bbox. | `pygeoapi/provider/oracle.py:406`, `pygeoapi/provider/oracle.py:674` |
| `extra_properties` | list[SQL expr] | no | `[]` | Injected **verbatim** into `SELECT`. Trusted config only. | `pygeoapi/provider/oracle.py:407`, `pygeoapi/provider/oracle.py:572` |
| `sql_manipulator` | dotted class path | no | `None` | A plugin class with `process_query`, `process_get`, `process_create`, `process_update` and `process_delete`. | `pygeoapi/provider/oracle.py:410`, `pygeoapi/provider/oracle.py:605` |
| `sql_manipulator_options` | object | no | `None` | Passed to the manipulator. | `pygeoapi/provider/oracle.py:411` |
| `properties` | list[string] | no | `[]` | Lower-cased. Restricts output columns. | `pygeoapi/provider/oracle.py:405`, `pygeoapi/provider/oracle.py:285` |
| `storage_crs` | CRS URI | no | CRS84 | Its SRID is used for the bbox `sdo_geometry`, `sdo_cs.transform` and inserts. | `pygeoapi/provider/oracle.py:695`, `pygeoapi/provider/oracle.py:779`, `pygeoapi/provider/oracle.py:1117` |
| `include_extra_query_parameters` | bool | no | `false` | Unknown params go to the manipulator as `extra_params`. | `pygeoapi/provider/oracle.py:666`, `docs/source/publishing/ogcapi-features.rst:567` |
| env `ORACLE_POOL_MIN` / `ORACLE_POOL_MAX` | int | no | 0 | Both non-zero means a **class-level** session pool is created. | `pygeoapi/provider/oracle.py:127`, `pygeoapi/provider/oracle.py:130` |
| `count` | — | — | — | **Ignored**: it always runs `SELECT COUNT(1)`. | `pygeoapi/provider/oracle.py:702` |

- **Capabilities** (all **read**):
  - `offset`/`limit`: `OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY` (`pygeoapi/provider/oracle.py:814`).
  - `bbox`: `sdo_filter` or `sdo_relate` (`pygeoapi/provider/oracle.py:491`, `pygeoapi/provider/oracle.py:522`).
  - `properties`: equality with bind variables (`pygeoapi/provider/oracle.py:470`).
  - `sortby` (`pygeoapi/provider/oracle.py:564`).
  - `select_properties` (`pygeoapi/provider/oracle.py:754`).
  - `skip_geometry` (`pygeoapi/provider/oracle.py:788`).
  - CRS: native `sdo_cs.transform(...).get_geojson()` (`pygeoapi/provider/oracle.py:792`).
  - `get` with `prev`/`next` (`pygeoapi/provider/oracle.py:1003`).
  - `create` (`pygeoapi/provider/oracle.py:1055`), `update` (`pygeoapi/provider/oracle.py:1150`) and `delete` (`pygeoapi/provider/oracle.py:1245`).
  - **Not honoured:** `datetime_`, `q` and `filterq`, which are passed only to the SQL manipulator (`pygeoapi/provider/oracle.py:715`). `resulttype=hits` isn't short-circuited: features are fetched anyway and `numberReturned` is never set (`pygeoapi/provider/oracle.py:849`). There's no `get_schema`/`get_domains`.
- **Docs examples:**
  - Connection, `docs/source/publishing/ogcapi-features.rst:447`. Keys: `data.{host, port, service_name, user, password}` (commented: `sid`, `external_auth`, `tns_name`, `tns_admin`, `init_oracle_client`), `id_field`, `table`, `geom_field`, `title_field`.
  - SDO, `docs/source/publishing/ogcapi-features.rst:478`. Adds `sdo_operator` and `sdo_param`.
  - Mandatory properties, `docs/source/publishing/ogcapi-features.rst:502`.
  - Extra properties, `docs/source/publishing/ogcapi-features.rst:526`.
  - Pooling env vars, `docs/source/publishing/ogcapi-features.rst:554`.
- **Tests/fixtures:**
  - `tests/provider/test_oracle_provider.py`: config (`tests/provider/test_oracle_provider.py:136`), synonyms (`tests/provider/test_oracle_provider.py:169`), SQL manipulator (`tests/provider/test_oracle_provider.py:45`, `tests/provider/test_oracle_provider.py:207`), properties/extra (`tests/provider/test_oracle_provider.py:216`), pool (`tests/provider/test_oracle_provider.py:656`).
  - Loader: `tests/load_oracle_data.py`; data in `tests/data/oracle/`. There's no test-config YAML block.
- **Errors raised** (**read**):
  - No service_name, sid or tns_name: `ProviderConnectionError("One of service_name, sid or tns_name must be specified!")` (`pygeoapi/provider/oracle.py:208`).
  - Missing host: `ProviderConnectionError("Host must be set for connection with service_name!")` (`pygeoapi/provider/oracle.py:172`).
  - Wallet without `tns_name`: `ProviderConnectionError("tns_name must be set for external authentication!")` (`pygeoapi/provider/oracle.py:158`).
  - Any `oracledb.DatabaseError` on connect (refused, bad credentials, unknown service) becomes `ProviderConnectionError(e)`, which carries the driver text such as `DPY-6005…`/`ORA-01017…` (`pygeoapi/provider/oracle.py:258`). The log line includes the **whole conn dict, password included** (`pygeoapi/provider/oracle.py:255`).
  - Missing table: `ProviderGenericError(f"Table {schema}.{table} not found!")` (`pygeoapi/provider/oracle.py:352`).
  - SQL errors: a bare `ProviderQueryError()` (`pygeoapi/provider/oracle.py:738`).
  - Missing mandatory property: `ProviderInvalidQueryError` (`pygeoapi/provider/oracle.py:680`).
  - Unknown id: `ProviderItemNotFoundError` (`pygeoapi/provider/oracle.py:1013`).
- **Gotchas / conflicts:**
  - **Docs contradict themselves on `sdo_operator`.** The YAML comment says "defaults to sdo_filter" (`docs/source/publishing/ogcapi-features.rst:493`), but the prose says "When not set, the default is sdo_relate!" (`docs/source/publishing/ogcapi-features.rst:496`). The **code default is `sdo_filter`** (`pygeoapi/provider/oracle.py:417`).
  - The docs table claims no datetime support, which matches the code (`docs/source/publishing/ogcapi-features.rst:31`).
  - **Transactions through the API fail.** `create`/`update` call `request_data.get("properties")` (`pygeoapi/provider/oracle.py:1069`), but the API passes bytes (§0.2), giving an `AttributeError` (500). The tests pass dicts directly (`tests/provider/test_oracle_provider.py:463`). The docs table says transactions ❌ (`docs/source/publishing/ogcapi-features.rst:31`).
  - **The session pool is a process-wide singleton** created from the *first* provider's credentials (`pygeoapi/provider/oracle.py:58`, `pygeoapi/provider/oracle.py:135`). Two OracleDB collections with different users or databases would share the first one's pool.
  - The wallet branch has a dangling `if`: after the wallet connect, the `tns_admin`/`else` branch runs again and reads `conn_dict["user"]` (`pygeoapi/provider/oracle.py:226`, `pygeoapi/provider/oracle.py:231`, `pygeoapi/provider/oracle.py:242`).
  - Each `query()` opens **two** connections, one for the count and one for the rows (`pygeoapi/provider/oracle.py:684`, `pygeoapi/provider/oracle.py:743`).
  - Output property names are lower-cased (`pygeoapi/provider/oracle.py:1033`).

---

## 3. `MongoDB` — `mongo.MongoProvider`
- **Provider type(s) and base:** `feature`. `MongoProvider(BaseProvider)` (`pygeoapi/provider/mongo.py:45`), registered as `MongoDB` (`pygeoapi/plugin.py:52`). **(read)**
- **Dependencies:** `pymongo` (`pygeoapi/provider/mongo.py:34`). pip: `pymongo`, pinned `pymongo==4.6.3` in `requirements-provider.txt:21`. The docs require MongoDB ≥ 5 (`docs/source/publishing/ogcapi-features.rst:208`). **(read)**
- **`data` forms:** a **MongoDB connection-string URI that includes the default database path** (`mongodb://host:27017/<db>`). It's passed as-is to `MongoClient(self.data)` and then `get_default_database()` (`pygeoapi/provider/mongo.py:66`, `pygeoapi/provider/mongo.py:67`). All pymongo URI options (credentials, `authSource`, `tls`, `replicaSet`, `serverSelectionTimeoutMS`, …) therefore go in the URI. **(read; executed in §3.1)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | string (mongodb URI) | yes | — | Must name a database. **SECRET** when it embeds `user:password@`. | `pygeoapi/provider/mongo.py:66` |
| `collection` | string | **yes** | — | The MongoDB collection. A missing key raises a bare `KeyError`. | `pygeoapi/provider/mongo.py:68` |
| `id_field` | string | no | `_id` (via `setdefault`) | **Effectively ignored.** The feature id is always `str(_id)` and `get()` requires an ObjectId. | `pygeoapi/provider/mongo.py:60`, `pygeoapi/provider/mongo.py:109`, `pygeoapi/provider/mongo.py:178` |
| `count` | bool | no | `true` | `count_documents`. | `pygeoapi/provider/mongo.py:151` |
| `editable` | bool | no | `false` | | `pygeoapi/provider/base.py:69` |
| `properties` | list | no | — | **Not applied** (mongo.py never reads `self.properties`). | `pygeoapi/provider/mongo.py:137` |
| `time_field` | string | no | — | **Not used.** The datetime filter hard-codes `properties.datetime`. | `pygeoapi/provider/mongo.py:135` |

- **Capabilities** (all **read**):
  - `offset`/`limit`: `skip`/`limit` (`pygeoapi/provider/mongo.py:105`).
  - `bbox`: `$geoWithin: {$box: …}` (`pygeoapi/provider/mongo.py:129`).
  - `properties`: `$eq` on `properties.<k>` with **string** values from the URL (`pygeoapi/provider/mongo.py:138`).
  - `sortby` (`pygeoapi/provider/mongo.py:142`).
  - `skip_geometry` (`pygeoapi/provider/mongo.py:110`).
  - `resulttype=hits` (`pygeoapi/provider/mongo.py:157`).
  - `@crs_transform` on `query` and `get` (`pygeoapi/provider/mongo.py:115`, `pygeoapi/provider/mongo.py:170`).
  - **Not honoured:** `select_properties`, `q` and `filterq`.
  - **`datetime_` is broken**: `assert isinstance(datetime_, datetime)`, but the API passes a string (`pygeoapi/provider/mongo.py:134`, §0.2), so every `datetime=` query raises `AssertionError` (500). The code comment admits "This parameter is not working yet!" (`pygeoapi/provider/mongo.py:131`). **The docs table claims datetime ✅** (`docs/source/publishing/ogcapi-features.rst:27`).
  - Transactions: `create` does `insert_one(bytes)` (`pygeoapi/provider/mongo.py:189`), a `TypeError` that the API turns into 400. The docs say ❌.
- **Docs example:** `docs/source/publishing/ogcapi-features.rst:220`. Keys: `type`, `name`, `data: mongodb://localhost:27017/testdb`, `collection`.
- **Tests/fixtures:** `tests/provider/test_mongo_provider.py:35` (URI), `tests/provider/test_mongo_provider.py:39` (config). Loader: `tests/load_mongo_data.py`. No datetime test. No config YAML block.
- **Errors raised** (**read**):
  - Unknown id: `ProviderItemNotFoundError(f'item {identifier} not found')` (`pygeoapi/provider/mongo.py:183`). A malformed id raises `bson.errors.InvalidId` from `ObjectId()` (500) (`pygeoapi/provider/mongo.py:178`).
  - **Connection errors are not wrapped.** The constructor's `create_index` is the first round trip (`pygeoapi/provider/mongo.py:69`), so an unreachable server blocks for pymongo's server-selection timeout (30 s by default) and then raises `pymongo.errors.ServerSelectionTimeoutError`.
  - A URI without a database name raises `pymongo.errors.ConfigurationError`.
- **Gotchas:**
  - `create_index` is a **write** on every request, so a read-only user fails.
  - `get_fields()` only looks at the first aggregation group's keys and types every field as `string` (`pygeoapi/provider/mongo.py:93`, `pygeoapi/provider/mongo.py:94`). Numeric property filters therefore never match (the string is compared to a number).

---

## 4. `Elasticsearch` — `elasticsearch_.ElasticsearchProvider`
- **Provider type(s) and base:** `feature`. `ElasticsearchProvider(BaseProvider)` (`pygeoapi/provider/elasticsearch_.py:52`), registered as `Elasticsearch` (`pygeoapi/plugin.py:44`). The `ElasticsearchCatalogue` record subclass is out of scope. **(read)**
- **Dependencies:** `elasticsearch` (`pygeoapi/provider/elasticsearch_.py:38`), `elasticsearch_dsl` (`pygeoapi/provider/elasticsearch_.py:41`) and `pygeofilter.backends.elasticsearch` (`pygeoapi/provider/elasticsearch_.py:39`). pip: `elasticsearch`, `elasticsearch-dsl` (`docs/source/publishing/ogcapi-features.rst:85`, `requirements-provider.txt:4`), and pygeofilter. ES ≥ 8 is enforced at runtime (`pygeoapi/provider/elasticsearch_.py:84`). **(read)**
- **`data` forms:** a string `http[s]://[user:password@]host:port/<index>`. It's split on the **last** `/` into host and index (`pygeoapi/provider/elasticsearch_.py:68`). The index may be a wildcard or alias (`pygeoapi/provider/elasticsearch_.py:108`, `pygeoapi/provider/elasticsearch_.py:116`). Credentials are only possible inside the URL (`docs/source/publishing/ogcapi-features.rst:109`). There's no API-key, CA-cert or `verify_certs` option, and no `options` are read. **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | string URL + `/index` | yes | — | **SECRET** when it has `user:pass@`. | `pygeoapi/provider/elasticsearch_.py:68` |
| `id_field` | string | no | — | The feature id is `_source.properties[id_field]`, falling back to `_source.id` and then `_id`. | `pygeoapi/provider/elasticsearch_.py:542`, `pygeoapi/provider/elasticsearch_.py:545` |
| `time_field` | string | cond. | — | Required for `datetime=`; without it a bare `ProviderQueryError()`. | `pygeoapi/provider/elasticsearch_.py:260` |
| `properties` | list | no | `[]` | `_source.includes`. | `pygeoapi/provider/elasticsearch_.py:350` |
| `editable` | bool | no | `false` | | `docs/source/publishing/ogcapi-features.rst:100` |
| `count` | — | — | — | **Ignored.** Always `track_total_hits: true`. | `pygeoapi/provider/elasticsearch_.py:229` |

- **Index requirements:**
  - Documents must be GeoJSON Features, with `geometry` mapped as `geo_shape` (`docs/source/publishing/ogcapi-features.rst:92`).
  - Fields are read from `mappings.properties.properties.properties` (nested objects are flattened with dots) (`pygeoapi/provider/elasticsearch_.py:110`, `pygeoapi/provider/elasticsearch_.py:138`).
  - **Sorting or domains on text fields need a `.raw` keyword sub-field** (`pygeoapi/provider/elasticsearch_.py:319`, `pygeoapi/provider/elasticsearch_.py:182`), as the test loader creates (`tests/load_es_data.py:63`).
- **Capabilities** (all **read**):
  - `offset`/`limit`: `from`/`size`. **When `offset+limit > 10000`** it switches to `helpers.scan` (a scroll) and **`numberMatched` becomes `returned + offset`**, not the real total (`pygeoapi/provider/elasticsearch_.py:377`, `pygeoapi/provider/elasticsearch_.py:391`).
  - `bbox`: `geo_shape` envelope intersects (`pygeoapi/provider/elasticsearch_.py:245`).
  - `datetime_`: `range`/`match` (`pygeoapi/provider/elasticsearch_.py:270`, `pygeoapi/provider/elasticsearch_.py:287`).
  - `properties`: `match` with `minimum_should_match: 100%` unless the value contains `|` (`pygeoapi/provider/elasticsearch_.py:297`, `pygeoapi/provider/elasticsearch_.py:305`).
  - `sortby` (`pygeoapi/provider/elasticsearch_.py:308`).
  - `q`: `query_string`, with comma-separated tokens OR-ed as phrases (`pygeoapi/provider/elasticsearch_.py:337`).
  - `select_properties` (`pygeoapi/provider/elasticsearch_.py:350`), `skip_geometry` (`pygeoapi/provider/elasticsearch_.py:363`), `resulttype=hits` (`pygeoapi/provider/elasticsearch_.py:237`).
  - `filterq`: pygeofilter to ES DSL (`pygeoapi/provider/elasticsearch_.py:373`, `pygeoapi/provider/elasticsearch_.py:644`).
  - `get_domains`: terms aggregation, size 500 (`pygeoapi/provider/elasticsearch_.py:153`).
  - `get()` (`pygeoapi/provider/elasticsearch_.py:425`).
  - `create` (auto-uuid id), `update` (full re-index) and `delete` (`pygeoapi/provider/elasticsearch_.py:472`, `pygeoapi/provider/elasticsearch_.py:494`, `pygeoapi/provider/elasticsearch_.py:512`).
  - `@crs_transform` on `query` and `get` (`pygeoapi/provider/elasticsearch_.py:204`, `pygeoapi/provider/elasticsearch_.py:424`).
- **Docs examples:** `docs/source/publishing/ogcapi-features.rst:95` (keys `type`, `name`, `editable`, `data`, `id_field`, `time_field`); credentials in the URL, including `${ENV}` interpolation (`docs/source/publishing/ogcapi-features.rst:113`); CQL notes (`docs/source/cql2.rst:40`).
- **Tests/fixtures:** `tests/provider/test_elasticsearch__provider.py:43` (plus the ordered-properties and CQL configs at `tests/provider/test_elasticsearch__provider.py:53` and `tests/provider/test_elasticsearch__provider.py:67`). Loader and mapping: `tests/load_es_data.py:53`. Data: `tests/data/ne_110m_populated_places_simple.geojson`.
- **Errors raised** (**read**):
  - `ping()` is False: `ProviderConnectionError(f'Cannot connect to Elasticsearch: {es_host}')` (`pygeoapi/provider/elasticsearch_.py:78`). **`es_host` includes any `user:password@`, so the password ends up in the exception text and the logs.**
  - ES < 8: `ProviderConnectionError('only ES 8+ supported')` (`pygeoapi/provider/elasticsearch_.py:85`).
  - Missing index: `NotFoundError` becomes `ProviderQueryError(err)` (`pygeoapi/provider/elasticsearch_.py:94`).
  - Query time: a connection error becomes a bare `ProviderConnectionError()`; `RequestError` and `NotFoundError` become a bare `ProviderQueryError()` (`pygeoapi/provider/elasticsearch_.py:400`).
  - Unknown id: `ProviderItemNotFoundError` (`pygeoapi/provider/elasticsearch_.py:460`).
- **Gotchas:** the docs table says hits ✅ and count ❌, which matches `count` being ignored (`docs/source/publishing/ogcapi-features.rst:23`). Deep paging uses a scroll.

## 5. `OpenSearch` — `opensearch_.OpenSearchProvider`
- **Provider type(s) and base:** `feature`. `OpenSearchProvider(BaseProvider)` (`pygeoapi/provider/opensearch_.py:51`), registered as `OpenSearch` (`pygeoapi/plugin.py:60`). `OpenSearchCatalogueProvider` exists (`pygeoapi/provider/opensearch_.py:523`) but is **not registered** in `PLUGINS`, so it's reachable only by dotted path. **(read)**
- **Dependencies:** `opensearchpy` (`pygeoapi/provider/opensearch_.py:38`), `opensearch_dsl` (`pygeoapi/provider/opensearch_.py:39`) and `pygeofilter.backends.opensearch` (`pygeoapi/provider/opensearch_.py:41`). pip: `opensearch-py`, `opensearch-dsl` (`docs/source/publishing/ogcapi-features.rst:405`, `requirements-provider.txt:12`, `requirements-provider.txt:13`). **(read)**
- **`data` forms and keys:** the same as Elasticsearch (`pygeoapi/provider/opensearch_.py:67`, `docs/source/publishing/ogcapi-features.rst:418`). Differences:
  - The client is created with **`verify_certs=0`**, so TLS certificates are never verified. That's hard-coded and there's no way to turn it on (`pygeoapi/provider/opensearch_.py:75`).
  - There's no version check (`pygeoapi/provider/opensearch_.py:82`).
  - Fields come only from top-level `properties` mappings, not nested ones (`pygeoapi/provider/opensearch_.py:119`).
- **Capabilities:** the same as ES, except:
  - `q` is passed raw to `query_string` (`pygeoapi/provider/opensearch_.py:265`).
  - There's **no `get_domains`**.
  - When a configured property is missing from a doc, `ProviderQueryError` is raised (ES only warns) (`pygeoapi/provider/opensearch_.py:485`). **(read)**
- **Docs examples:** `docs/source/publishing/ogcapi-features.rst:412` (keys `type`, `name`, `editable`, `data`, `id_field`, `time_field`) and credentials at `docs/source/publishing/ogcapi-features.rst:426`.
- **Tests/fixtures:** `tests/provider/test_opensearch__provider.py:43` (port 9209). Loader: `tests/load_opensearch_data.py:56`.
- **Errors raised:**
  - `ping()` is False: `ProviderConnectionError(f'Cannot connect to OpenSearch: {os_host}')`, which leaks credentials the same way ES does (`pygeoapi/provider/opensearch_.py:77`).
  - Any mapping error becomes `ProviderQueryError(err)` (`pygeoapi/provider/opensearch_.py:90`).
  - **Query errors are not wrapped.** Only `ValueError` is caught, and it's swallowed, which then leaves `matched` unbound: `UnboundLocalError` (`pygeoapi/provider/opensearch_.py:326`, `pygeoapi/provider/opensearch_.py:329`). **(read)**

---

## 6. `ESRI` — `esri.ESRIServiceProvider`
- **Provider type(s) and base:** `feature`. `ESRIServiceProvider(BaseProvider)` (`pygeoapi/provider/esri.py:47`), registered as `ESRI` (`pygeoapi/plugin.py:47`). **(read)**
- **Dependencies:** `requests` only (`pygeoapi/provider/esri.py:34`), which is a core pygeoapi requirement (`requirements.txt:15`). **(read)**
- **`data` forms:** the URL of a **layer** (`…/FeatureServer/<n>` or `…/MapServer/<n>`). Fields are read from `GET <data>?f=pjson` (`pygeoapi/provider/esri.py:90`) and queries go to `<data>/query` (`pygeoapi/provider/esri.py:63`). The layer must advertise `supportsPagination`, `supportsOrderBy` and the `geoJSON` format (`pygeoapi/provider/esri.py:104`). **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | URL | yes | — | A layer URL (not the service root). | `pygeoapi/provider/esri.py:63` |
| `id_field` | string | **yes in practice** | — | `OBJECTID`, `objectid` or `fid` use ESRI's own id and `objectIds=` for `get` (`pygeoapi/provider/esri.py:74`, `pygeoapi/provider/esri.py:201`). Any other value (including `FID`, which the docs suggest) sets `id = properties[id_field]`, a `KeyError` when that field isn't in `outFields` (`pygeoapi/provider/esri.py:410`). | `pygeoapi/provider/esri.py:73` |
| `time_field` | string | cond. | — | Used in the `where` clause for `datetime`. | `pygeoapi/provider/esri.py:323` |
| `username` | string | no | `None` | Token login happens only if **both** `username` and `password` are set. | `pygeoapi/provider/esri.py:65`, `pygeoapi/provider/esri.py:220` |
| `password` | string | no | `None` | **SECRET** | `pygeoapi/provider/esri.py:66` |
| `token_service` | URL | no | **`https://www.arcgis.com`** (`ARCGIS_URL`) | The generateToken endpoint the credentials are POSTed to. | `pygeoapi/provider/esri.py:67`, `pygeoapi/provider/esri.py:43` |
| `referer` | string | no | **`https://www.arcgis.com/sharing/rest/generateToken`** (`GENERATE_TOKEN_URL`) | Sent as the `referer` form field. | `pygeoapi/provider/esri.py:68`, `pygeoapi/provider/esri.py:44` |
| `storage_crs` | CRS URI | no | CRS84 | Its SRID becomes `outSR` (`pygeoapi/provider/esri.py:64`, `pygeoapi/provider/esri.py:362`). The test uses EPSG:3857 (`tests/provider/test_esri_provider.py:112`). | `pygeoapi/provider/base.py:86` |
| `crs` | list[CRS URI] | no | `[CRS84]` | **Not read by esri.py.** API-level only. See §6.1. | `pygeoapi/crs.py:111` |
| `properties` | list | no | `[]` | Becomes `outFields`, intersected with `properties=` when both are given. | `pygeoapi/provider/esri.py:291` |
| `count` | bool | no | `true` | `returnCountOnly=true`. | `pygeoapi/provider/esri.py:162`, `pygeoapi/provider/esri.py:345` |

- **Capabilities** (all **read**):
  - `offset`/`limit`: `resultOffset`/`resultRecordCount` plus a client-side loop (§10) (`pygeoapi/provider/esri.py:173`, `pygeoapi/provider/esri.py:174`).
  - `bbox`: `geometry=xmin,ymin,xmax,ymax&geometryType=esriGeometryEnvelope`, with **`inSR` hard-coded to `4326`** even though the API has already transformed the bbox to `storage_crs` (`pygeoapi/provider/esri.py:153`).
  - `datetime_`: ranges work, as `TIMESTAMP 'YYYY-mm-dd HH:MM:SS'` (`pygeoapi/provider/esri.py:321`). A **single instant calls the non-existent `self.esri_date`, an `AttributeError`** (`pygeoapi/provider/esri.py:331`).
  - `properties`: a `where` of `k = '<sanitized>'` (`pygeoapi/provider/esri.py:315`).
  - `sortby`: `orderByFields` (`pygeoapi/provider/esri.py:170`). `select_properties`: `outFields` (`pygeoapi/provider/esri.py:147`). `skip_geometry`: `returnGeometry=false` (`pygeoapi/provider/esri.py:172`). `resulttype=hits` (`pygeoapi/provider/esri.py:167`). `get()` (`pygeoapi/provider/esri.py:183`).
  - **Not honoured:** `q` and `filterq` (the signature lacks both: `pygeoapi/provider/esri.py:121`). There are no transactions, and no `get_schema`/`get_domains`.
  - CRS: there's no `@crs_transform`. It asks the server for `outSR`, but a `crs=` request that differs from `storage_crs` hits `crs_transform_spec.target_crs`, an attribute **`CrsTransformSpec` doesn't have** (its fields are `target_crs_uri`/`target_crs_wkt`: `pygeoapi/crs.py:68`), giving an `AttributeError` (`pygeoapi/provider/esri.py:360`).
- **Docs examples:**
  - ArcGIS Online, `docs/source/publishing/ogcapi-features.rst:158`. Keys: `type`, `name`, `data`, `id_field`, `time_field`, `crs: 4326`, `username`, `password`, `token_service`, `referer`.
  - Self-hosted, `docs/source/publishing/ogcapi-features.rst:174`. The same keys.
- **Tests/fixtures:** `tests/provider/test_esri_provider.py:39`. It runs **live** against `https://sampleserver6.arcgisonline.com/arcgis/rest/services/Hurricanes/MapServer/0` (`id_field: OBJECTID`, `time_field: Date_Time`, 406 features: `tests/provider/test_esri_provider.py:83`) and `EmergencyFacilities/FeatureServer/0` with `id_field: facilityid` (`tests/provider/test_esri_provider.py:62`). Only datetime *ranges* are tested (`tests/provider/test_esri_provider.py:205`). No config YAML block.
- **Errors raised** (**read**):
  - HTTP 400: `ProviderConnectionError('Bad http response code')` (`pygeoapi/provider/esri.py:256`). Inside `get_fields()` it's **caught and logged, and fields become `{}`** (`pygeoapi/provider/esri.py:91`).
  - An ESRI JSON `{"error": …}` body (ESRI usually returns HTTP 200 with an error object, e.g. an invalid token) is also caught, logged and turned into `{}` fields. **No exception**, so the collection loads empty (`pygeoapi/provider/esri.py:96`).
  - A non-layer URL (no `advancedQueryCapabilities`): `ProviderConnectionError(f'Could not access resource {data}')` (`pygeoapi/provider/esri.py:110`).
  - A layer without pagination or orderBy: `ProviderTypeError('Unsupported Feature/Map Server: ')` (400) (`pygeoapi/provider/esri.py:114`).
  - Non-JSON body: `ProviderQueryError(err)` (`pygeoapi/provider/esri.py:261`).
  - **DNS failure, refused connection or TLS error:** a raw `requests.exceptions.ConnectionError`, not wrapped (`pygeoapi/provider/esri.py:252`).
  - **Unknown id in `get()`:** `[feature] = []` raises `ValueError` (500), not `ProviderItemNotFoundError` (`pygeoapi/provider/esri.py:208`).
  - **Bad credentials:** `r.json().get('token')` is `None` and the header `X-Esri-Authorization: Bearer None` is set anyway, with no error (`pygeoapi/provider/esri.py:233`, `pygeoapi/provider/esri.py:236`). An HTML response from the token URL raises a raw JSON decode error.

### 6.1 Settled: `crs: 4326` in the ESRI docs example
- **Validator (executed).** The docs example (`docs/source/publishing/ogcapi-features.rst:160`–170) was merged into the repo's `pygeoapi-config.yml` (which validates on its own) as the only resource, then checked with `pygeoapi.config.validate_config`:
  - `crs: 4326` → **INVALID**: `ValidationError: 4326 is not of type 'array'` at `resources.esri_demo.providers[0].crs`.
  - `crs: 'http://www.opengis.net/def/crs/EPSG/0/4326'` (string, not list) → **INVALID** (not of type 'array').
  - `crs: [http://www.opengis.net/def/crs/OGC/1.3/CRS84, http://www.opengis.net/def/crs/EPSG/0/4326]` → **VALID**.
  - `crs` omitted → **VALID**.
  - `crs: ['EPSG:4326']` → **VALID**, because `validate_config` doesn't enable `format: uri` checking (`pygeoapi/config.py:83`; the schema's `format: uri` is at `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:615`). The runtime rejects it, though (§6.2).
- **Schema:** `crs` is `type: array` of `string` with `format: uri`, defaulting to `[CRS84]` (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:608`).
- **How esri.py uses crs:** **it doesn't.** esri.py never reads `crs`. The output spatial reference is `outSR = get_srid(storage_crs)` (`pygeoapi/provider/esri.py:64`, `pygeoapi/provider/esri.py:146`). `crs` is consumed only by the API: `get_supported_crs_list` iterates it (`pygeoapi/crs.py:113`) during every items request (`pygeoapi/api/itemtypes.py:377`, via `pygeoapi/crs.py:569`) and on the collection page (`pygeoapi/api/collection.py:274`).
- **Correct form for 0.24:** omit `crs` (the default `[CRS84]`, returned as EPSG:4326 lon/lat), or give a **list of CRS URIs**. To have the ESRI server return another SR, set `storage_crs: http://www.opengis.net/def/crs/EPSG/0/<wkid>` (the test does this with 3857: `tests/provider/test_esri_provider.py:112`). Don't list CRSs other than the storage CRS (plus CRS84) in `crs`: requesting one crashes in `_get_srid` (§6.2). Runtime evidence: §6.2.

### 6.2 Executed checks (ESRI)
_(filled in below after execution)_

---

## 7. `Socrata` — `socrata.SODAServiceProvider`
- **Provider type(s) and base:** `feature`. `SODAServiceProvider(BaseProvider)` (`pygeoapi/provider/socrata.py:49`), registered as `Socrata` (`pygeoapi/plugin.py:67`). **(read)**
- **Dependencies:** `sodapy` (`pygeoapi/provider/socrata.py:35`). pip: `sodapy` (`docs/source/publishing/ogcapi-features.rst:827`, `requirements-provider.txt:24`). **(read)**
- **`data` forms:** a URL string. **Only its `netloc` (domain) is used** (`pygeoapi/provider/socrata.py:68`), so the scheme is required, and the path and query are ignored. **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | URL | yes | — | Domain, e.g. `https://soda.demo.socrata.com/`. | `pygeoapi/provider/socrata.py:68` |
| `resource_id` | string (4x4) | **yes** | — | A missing key raises a bare `KeyError`. | `pygeoapi/provider/socrata.py:65` |
| `id_field` | string | **yes in practice** | — | `id = properties[id_field]`, and always added to `$select`. | `pygeoapi/provider/socrata.py:146`, `pygeoapi/provider/socrata.py:205` |
| `geom_field` | string | cond. | `None` | Required for bbox (`within_polygon`). | `pygeoapi/provider/socrata.py:67`, `pygeoapi/provider/socrata.py:244` |
| `time_field` | string | cond. | — | For `datetime`. | `pygeoapi/provider/socrata.py:253` |
| `token` | string | no | `None` | A Socrata **app token**, passed to `sodapy.Socrata(domain, token)`. Treat as **SECRET**. | `pygeoapi/provider/socrata.py:66`, `pygeoapi/provider/socrata.py:69` |
| `properties` | list | no | `[]` | Also limits `get_fields()`. A name that isn't a column raises `ValueError` from `.index()`. | `pygeoapi/provider/socrata.py:88`, `pygeoapi/provider/socrata.py:90` |
| `count` | bool | no | `true` | `$select=count(*)`. | `pygeoapi/provider/socrata.py:129`, `pygeoapi/provider/socrata.py:274` |

- **Capabilities** (all **read**):
  - `offset`/`limit`: `$offset`/`$limit` through sodapy kwargs (`pygeoapi/provider/socrata.py:142`).
  - `bbox`: `within_polygon(geom_field, 'POLYGON(...)')` (`pygeoapi/provider/socrata.py:244`).
  - `datetime_`: SoQL comparisons (`pygeoapi/provider/socrata.py:253`).
  - `properties`: `k = "v"` with **double quotes and no escaping** (`pygeoapi/provider/socrata.py:237`).
  - `sortby`: `$order` (`pygeoapi/provider/socrata.py:140`). `select_properties`: `$select` (`pygeoapi/provider/socrata.py:120`). `skip_geometry`: applied locally (`pygeoapi/provider/socrata.py:147`). `resulttype=hits` (`pygeoapi/provider/socrata.py:134`). `get()` (`pygeoapi/provider/socrata.py:167`).
  - `@crs_transform` on `query` and `get` (`pygeoapi/provider/socrata.py:95`, `pygeoapi/provider/socrata.py:166`).
  - **Not honoured:** `q` and `filterq`. No transactions.
- **Docs example:** `docs/source/publishing/ogcapi-features.rst:837`. Keys: `type`, `name`, `data`, `resource_id`, `id_field`, `geom_field`, `time_field`, `token`.
- **Tests/fixtures:** `tests/provider/test_socrata_provider.py:40` (mocked sodapy `get`/`datasets`; the property filter is asserted as `region = "Nevada"`: `tests/provider/test_socrata_provider.py:62`). The live variant is skipped (`tests/provider/test_socrata_provider_live.py:36`) and targets `https://soda.demo.socrata.com/`, resource `emdb-u46w` (`tests/provider/test_socrata_provider_live.py:48`).
- **Errors raised** (**read**):
  - Metadata JSON decode failure: `ProviderConnectionError(err)` (`pygeoapi/provider/socrata.py:86`). Other sodapy or requests errors in `get_fields` (HTTP errors, DNS) are **not wrapped**.
  - Query failures: `ProviderQueryError(f'Provider query error: {err}')` (`pygeoapi/provider/socrata.py:159`). The count query isn't inside that `try`.
  - Unknown id: `pop()` on an empty list raises `IndexError` (500) (`pygeoapi/provider/socrata.py:185`).

## 8. `SensorThings` — `sensorthings.SensorThingsProvider`
- **Provider type(s) and base:** `feature`. `SensorThingsProvider(BaseProvider)` (`pygeoapi/provider/sensorthings.py:76`), registered as `SensorThings` (`pygeoapi/plugin.py:64`). The `SensorThingsEDR` variant is out of scope. **(read)**
- **Dependencies:** `requests` (`pygeoapi/provider/sensorthings.py:34`), which is core. **(read)**
- **`data` forms:**
  1. The STA base URL plus an `entity` key, joined with `url_join` (`pygeoapi/provider/sensorthings.py:582`).
  2. A URL that **ends with the entity name** (for example `…/v1.1/Datastreams`), when `entity` is absent (`pygeoapi/provider/sensorthings.py:587`).

  Otherwise it raises `RuntimeError('Entity type required')` (`pygeoapi/provider/sensorthings.py:586`). **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | URL | yes | — | See above. | `pygeoapi/provider/sensorthings.py:582` |
| `entity` | `Things` \| `Datastreams` \| `Observations` \| `ObservedProperties` | cond. | parsed from `data` | Other names in `ENTITY` (e.g. `Sensors`) have no default `$expand`, so they give a `KeyError` unless `expand` is set (`pygeoapi/provider/sensorthings.py:344`). | `pygeoapi/provider/sensorthings.py:581`, `pygeoapi/provider/sensorthings.py:55` |
| `id_field` | string | no | `@iot.id` | String ids are quoted `'…'` in the feature id. | `pygeoapi/provider/sensorthings.py:598`, `pygeoapi/provider/sensorthings.py:309` |
| `time_field` | string | cond. | — | Required for `datetime`, else `ProviderQueryError`. | `pygeoapi/provider/sensorthings.py:384` |
| `uri_field` | string | no | — | Read from `entity.properties[uri_field]`; missing gives `ProviderInvalidDataError`. | `pygeoapi/provider/sensorthings.py:489`, `pygeoapi/provider/sensorthings.py:493` |
| `expand` | string (OData `$expand`) | no | built-in per entity | **Overwrites the module-level `EXPAND` dict**, which affects every SensorThings provider for that entity in the process. | `pygeoapi/provider/sensorthings.py:601`, `pygeoapi/provider/sensorthings.py:602` |
| `intralink` | bool | no | `false` | Links to sibling STA collections. Reads the **global** config through `get_config()`, so `PYGEOAPI_CONFIG` must be set (`pygeoapi/config.py:54`). | `pygeoapi/provider/sensorthings.py:605`, `pygeoapi/provider/sensorthings.py:612` |
| `rel_link` | URL | no | server URL | (undocumented, "For pytest") Base URL for intralinks. Skips `get_config()`. | `pygeoapi/provider/sensorthings.py:606`, `pygeoapi/provider/sensorthings.py:608` |
| `properties` | list | no | `[]` | Applied locally. | `pygeoapi/provider/sensorthings.py:477` |
| `count` | — | — | — | **Ignored.** `$count=true` is sent only for `resulttype=hits`. | `pygeoapi/provider/sensorthings.py:261` |

- **Capabilities** (all **read**):
  - `offset`/`limit`: `$skip`/`$top`, then follows `@iot.nextLink` (§10) (`pygeoapi/provider/sensorthings.py:247`, `pygeoapi/provider/sensorthings.py:278`).
  - `bbox`: `st_within(<location path>, geography'POLYGON(...)')` (`pygeoapi/provider/sensorthings.py:412`).
  - `datetime_`: `ge`/`le`/`eq` (`pygeoapi/provider/sensorthings.py:392`).
  - `properties`: `name eq value`, **with the value unquoted**, so strings must be sent with quotes (`pygeoapi/provider/sensorthings.py:377`).
  - `sortby`: `$orderby` (`pygeoapi/provider/sensorthings.py:255`). `select_properties` and `skip_geometry`: applied locally (`pygeoapi/provider/sensorthings.py:290`). `resulttype=hits`: `@iot.count` (`pygeoapi/provider/sensorthings.py:263`). `get()`: `<url>(<id>)` (`pygeoapi/provider/sensorthings.py:162`).
  - `create`/`update`/`delete` via POST/PUT/DELETE (`pygeoapi/provider/sensorthings.py:173`, `pygeoapi/provider/sensorthings.py:197`, `pygeoapi/provider/sensorthings.py:216`).
  - `@crs_transform` (`pygeoapi/provider/sensorthings.py:127`).
  - **Not honoured:** `q` and `filterq`.
- **Docs example:** `docs/source/publishing/ogcapi-features.rst:805`. Keys: `type`, `name`, `data`, `uri_field`, `entity`, `time_field`, `intralink`, `expand`. Intralinks and JSON-LD are covered at `docs/source/configuration.rst:663`.
- **Tests/fixtures:** `tests/provider/test_sensorthings_provider.py:36`. It runs against a local FROST server at `http://localhost:8888/FROST-Server/v1.1/Datastreams` with `rel_link` and `intralink` set. Also covered: Observations (`tests/provider/test_sensorthings_provider.py:113`), custom expand (`tests/provider/test_sensorthings_provider.py:165`), `uri_field` (`tests/provider/test_sensorthings_provider.py:189`) and transactions with dict bodies (`tests/provider/test_sensorthings_provider.py:208`).
- **Errors raised** (**read**):
  - Unreachable host during `get_fields`: `ProviderConnectionError(f'Unable to contact SensorThings endpoint at {url}')` (`pygeoapi/provider/sensorthings.py:114`).
  - Any non-2xx: `ProviderConnectionError('Bad http response code')` (`pygeoapi/provider/sensorthings.py:350`).
  - Non-JSON: `ProviderQueryError(err)` (`pygeoapi/provider/sensorthings.py:356`).
  - Create failure: `ProviderInvalidDataError` (`pygeoapi/provider/sensorthings.py:183`).
  - A connection error during `query()` isn't wrapped.
- **Gotchas / conflicts:**
  - **Transactions through the API:** the body arrives as bytes and goes into `http.post(json=item)` (`pygeoapi/provider/sensorthings.py:173`), so it fails before any request is sent. The docs table says transactions ✅ (`docs/source/publishing/ogcapi-features.rst:35`). Executed check below.
  - `self.data.rstrip('/')` discards its result (`pygeoapi/provider/sensorthings.py:579`).
  - `rstrip(f'/{entity}')` strips a *character set*, not a suffix (`pygeoapi/provider/sensorthings.py:589`).
  - The docs refer to "docker examples for SensorThings" (`docs/source/publishing/ogcapi-features.rst:822`), but there are none in this repo (`docker/` holds only `default.config.yml`).

## 9. `ERDDAPTabledap` — `erddap.TabledapProvider`
- **Provider type(s) and base:** `feature`. `TabledapProvider(BaseProvider)` (`pygeoapi/provider/erddap.py:59`), registered as `ERDDAPTabledap` (`pygeoapi/plugin.py:46`). **(read)**
- **Dependencies:** `requests` (`pygeoapi/provider/erddap.py:50`; docs: `docs/source/publishing/ogcapi-features.rst:126`). **(read)**
- **`data` forms:** a tabledap dataset URL without extension. `.geoJson` is appended (`pygeoapi/provider/erddap.py:94`). **(read)**
- **Keys:**

| Key | Type | Req? | Default | Meaning / notes | Source |
|---|---|---|---|---|---|
| `data` | URL | yes | — | `…/erddap/tabledap/<datasetID>` | `pygeoapi/provider/erddap.py:94` |
| `id_field` | string | yes | — | The station/platform id. The feature id is `"<id>.<time>"`. | `pygeoapi/provider/erddap.py:150`, `pygeoapi/provider/erddap.py:160` |
| `options` | object | **yes (even `{}`)** | `None` | `self.options.get(...)` is called unconditionally, so `options` absent means `AttributeError: 'NoneType' object has no attribute 'get'`. | `pygeoapi/provider/erddap.py:64` |
| `options.filters` | string | no | `None` | Raw ERDDAP constraint string, **prepended verbatim** (the docs example starts with `&`). | `pygeoapi/provider/erddap.py:64`, `pygeoapi/provider/erddap.py:98` |
| `options.max_age_hours` | number | no | `None` | Adds `time>=now-N h`. **Takes precedence over the client's `datetime`.** | `pygeoapi/provider/erddap.py:93`, `pygeoapi/provider/erddap.py:100` |
| `time_field` | string | — | — | (docs only) Never read. The time variable is hard-coded as `time`. | `docs/source/publishing/ogcapi-features.rst:137`, `pygeoapi/provider/erddap.py:159` |

- **Capabilities** (all **read**):
  - `bbox`: `latitude`/`longitude` constraints, with those variable names hard-coded (`pygeoapi/provider/erddap.py:127`).
  - `datetime_`: `time>=`/`time<=`/`time=`, **only when `max_age_hours` is unset** (`pygeoapi/provider/erddap.py:107`).
  - **Paging is done client-side on the full result:** the entire filtered dataset is downloaded on every call and sliced with `features[offset:limit]`, which is wrong for `offset > 0`. It should be `[offset:offset+limit]` (`pygeoapi/provider/erddap.py:144`). `numberMatched` is `len(features)` and **`numberReturned` is always `limit`** (`pygeoapi/provider/erddap.py:141`, `pygeoapi/provider/erddap.py:142`).
  - **Not honoured:** `properties`, `sortby`, `select_properties`, `skip_geometry`, `q`, `filterq`, `resulttype=hits` and `count`.
  - `get()`: re-queries by id and time (`pygeoapi/provider/erddap.py:181`).
  - `@crs_transform` (`pygeoapi/provider/erddap.py:85`).
- **Docs example:** `docs/source/publishing/ogcapi-features.rst:130`. Keys: `type`, `name`, `data`, `id_field`, `time_field`, `options.filters`, `options.max_age_hours`. There's also an in-module sample (`pygeoapi/provider/erddap.py:37`).
- **Tests/fixtures:** **none** (no `tests/provider/test_erddap*`).
- **Errors raised:**
  - Unknown id: `ProviderNotFoundError('No features found')` (`pygeoapi/provider/erddap.py:199`). That's HTTP **500**, not 404 (`pygeoapi/provider/base.py:364`).
  - Everything else is raw: `requests` connection errors (`pygeoapi/provider/erddap.py:136`); JSON decode errors on ERDDAP's plain-text error bodies (`pygeoapi/provider/erddap.py:138`), which ERDDAP returns with HTTP 404 when a query matches nothing; and `IndexError` in `get_fields` when the dataset is empty (`pygeoapi/provider/erddap.py:70`). **(read)**
- **Gotchas / conflicts:**
  - The **docs say `datetime` overrides `max_age_hours`** (`docs/source/publishing/ogcapi-features.rst:143`), but **the code does the opposite** (`pygeoapi/provider/erddap.py:100`, `pygeoapi/provider/erddap.py:107`).
  - `get_fields()` registers only the **last** property: the assignment sits outside the `for` loop (`pygeoapi/provider/erddap.py:81`).
  - `get_fields()` itself triggers a full download (`pygeoapi/provider/erddap.py:70`), on every request.
  - `get()` splits the id on `.` and fails if the id or time contains one (`pygeoapi/provider/erddap.py:181`).

---

## 10. API-backed providers compared (models for "HTTP JSON API → Features")

| Aspect | ESRI | Socrata | SensorThings | ERDDAPTabledap |
|---|---|---|---|---|
| HTTP client | `requests.Session`, one per provider instance (`pygeoapi/provider/esri.py:70`) | sodapy `Socrata` client (`pygeoapi/provider/socrata.py:69`) | `requests.Session` (`pygeoapi/provider/sensorthings.py:97`) | bare `requests.get` (`pygeoapi/provider/erddap.py:136`) |
| Paging params | `resultOffset` / `resultRecordCount` (`pygeoapi/provider/esri.py:173`) | `$offset` / `$limit` via sodapy (`pygeoapi/provider/socrata.py:142`) | `$skip` / `$top` (`pygeoapi/provider/sensorthings.py:247`) | none; downloads everything and slices locally (`pygeoapi/provider/erddap.py:144`) |
| Server page cap handling | Loops while fewer than `min(limit, numberMatched)` came back, adding `step` (the first page size) to **both** `resultOffset` and `resultRecordCount` (`pygeoapi/provider/esri.py:384`, `pygeoapi/provider/esri.py:385`). It ignores `exceededTransferLimit` and **can return more than `limit`** (§6.2). | Single request. sodapy passes `$limit` through; there's no loop. | Follows `@iot.nextLink` until `limit` is reached, rewriting its scheme and host to the configured ones (`pygeoapi/provider/sensorthings.py:278`), then trims (`pygeoapi/provider/sensorthings.py:291`). | n/a |
| `numberMatched` | Separate `returnCountOnly=true` request with the same `where`/geometry (`pygeoapi/provider/esri.py:345`), if `count` | Separate `$select=count(*)` (`pygeoapi/provider/socrata.py:274`), if `count` | `$count=true` only for `resulttype=hits` (`pygeoapi/provider/sensorthings.py:261`); otherwise whatever `@iot.count` the server volunteers | `len(all features)` (`pygeoapi/provider/erddap.py:141`) |
| Filters pushed down | `where` (props, datetime), envelope `geometry`, `orderByFields`, `outFields`, `returnGeometry`, `outSR` | `$where` (props, bbox via `within_polygon`, datetime), `$order`, `$select` | `$filter` (props, `st_within` bbox, datetime), `$orderby`, `$expand` | `filters` option, `time`, `latitude`/`longitude` |
| Applied locally | id mapping | `skip_geometry`, id mapping | `select_properties`, `properties`, `skip_geometry`, intralinks, geometry extraction | paging, id |
| Silently ignored | `q`, `filterq` | `q`, `filterq` | `q`, `filterq` | `properties`, `sortby`, `select_properties`, `skip_geometry`, `q`, `filterq`, `resulttype`, `count` |
| Auth | username/password to generateToken, then `X-Esri-Authorization: Bearer <token>` (`pygeoapi/provider/esri.py:236`). No static token or custom headers. | App token (`token`) | none | none (`filters` could carry URL params) |
| Timeouts | **none** (no `timeout=` anywhere) | sodapy's own client default | **none** | **none** |
| Caching | none; client and session rebuilt per request | none | none (module-level `EXPAND` is mutated) | none |
| Value quoting | `sanitize_attribute_value` doubles single quotes (`pygeoapi/provider/base.py:316`) | `"v"`, unescaped (`pygeoapi/provider/socrata.py:237`) | unquoted (`pygeoapi/provider/sensorthings.py:377`) | raw string concatenation |

**Lessons for our HTTP template engine:**
1. Make paging a declared strategy: offset/limit parameter names, `max_page_size`, a stop condition, and "follow next link" with a host allow-list.
2. Trim to `limit` and loop on server caps.
3. Always set connect and read timeouts, and wrap `requests` exceptions in `ProviderConnectionError`, keeping the host and port for the plain-language message.
4. Treat HTTP-200 JSON error bodies (ESRI style) as errors.
5. Quote and escape values per dialect.
6. Declare which OGC params are pushed down, and **reject rather than ignore** unsupported `filter`/`q`.
7. Cache per process with a TTL, not per request.
8. Put auth in headers or query params with `${ENV}` secrets.

---

## 11. Doc/code conflicts (consolidated)
1. **ESRI `crs: 4326`:** schema-invalid, and esri.py doesn't read `crs` at all (§6.1, §6.2).
2. **ESRI defaults:** the docs say `referer` "defaults to https://www.arcgis.com" (`docs/source/publishing/ogcapi-features.rst:170`). In code, **`token_service` defaults to `https://www.arcgis.com`** and **`referer` defaults to the generateToken URL** (`pygeoapi/provider/esri.py:67`, `pygeoapi/provider/esri.py:68`). The two defaults look swapped. With only `username`/`password` set (the ArcGIS Online case in `docs/source/publishing/ogcapi-features.rst:151`), credentials are POSTed to the arcgis.com home page.
3. **ESRI `id_field`:** the docs suggest `FID` (`docs/source/publishing/ogcapi-features.rst:150`), but only lowercase `fid`, `OBJECTID` and `objectid` get default-id handling (`pygeoapi/provider/esri.py:74`).
4. **ERDDAP `max_age_hours` vs `datetime`:** docs and code disagree about which wins (§9). ERDDAP's `time_field` is docs-only.
5. **MongoDB datetime:** docs ✅, code broken (§3).
6. **SensorThings transactions:** docs ✅, but the API passes bytes (§8).
7. **Oracle `sdo_operator` default:** the docs contradict themselves; the code uses `sdo_filter` (§2).
8. **MySQL options:** the docs example passes libpq-only keepalive options to PyMySQL (§1.2).
9. **PostgreSQL driver package:** the docs say `psycopg2-binary`, `requirements-provider.txt` says `psycopg2`.
10. **"CQL" column:** the docs table lists no CQL for ESRI, Socrata, SensorThings and MongoDB, and the code silently ignores a `filter=` for them instead of rejecting it (§0.2).

## 12. Open questions / not verified
- DB-side behaviour (MySQL SRID-mismatch errors, the Mongo `$box` query on GeoJSON with a 2dsphere index, Oracle wallet flows) is unverified because no server was available.
- sodapy's default timeout and its catalog endpoint for non-US domains: check the installed sodapy source.
- Whether ESRI's arcgis.com home page returns JSON to a token POST. Not probed, since we won't send credentials.

## Appendix: experiments
Scripts live in the session scratchpad (`…/scratchpad/features-services/exp/`), outside the repo.
