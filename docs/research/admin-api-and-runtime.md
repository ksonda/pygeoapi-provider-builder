# Admin API, server lifecycle/reload, and the Docker image (pygeoapi 0.24.0)

Phase 0 research for pygeoapi Provider Builder. Scope: the Admin API (`pygeoapi/api/admin.py`), how a
running server picks up config changes, and the official `geopython/pygeoapi:0.24.0` image. This
feeds the publish targets, the reload story and the sandbox design.

**Ground truth.** pygeoapi 0.24.0 (tag commit f765a64), vendored at `vendor/pygeoapi`. It is identical to
the package in the project `.venv` and to the scratch venv used for the experiments (checked with
`diff -rq`, excluding `__pycache__`) (executed).

**Conventions.**
- Citations are `path:line`, relative to the pygeoapi repo root.
- References that start with `ext:` point at third-party packages (gunicorn 23.0.0, jsonpatch 1.33)
  installed in the scratch venv. They are not pygeoapi repo paths.
- "(executed)" means an experiment settled the fact. "(read)" means it comes from reading source or docs only.
- Experiments ran in a scratch venv: `pygeoapi==0.24.0` plus `jsonpatch filelock "gunicorn<24" gevent
  starlette uvicorn httpx Django`, on Python 3.12. The project `.venv` has no `jsonpatch`, so
  `pygeoapi.api.admin` cannot be imported there (executed: package listing). The Docker daemon was not
  running, so every image fact is (read) from `Dockerfile`, `docker/*` and `requirements*.txt`.

---

## 0. Summary: what this means for Provider Builder

1. **Do not use the Admin API as our publish mechanism, except against a server that restarts after
   every write.** A worker's in-memory config is never refreshed after a write (§1.7):
   - Every write rebuilds the file as *startup config + this one change*. Earlier admin writes are
     silently lost (executed, §2.4).
   - After one POST that contains a `${VAR}`, **every later write in that worker returns 500** until
     the worker restarts (executed, §2.4).
   - New resources are not served until the process restarts (executed).
2. **The Admin API rewrites the whole YAML file with PyYAML.**
   - Lost: comments, quoting style, flow-style lists, anchor names, datetime formatting and indentation.
   - Kept: key order, unknown keys and aliases (renamed to `&id001`).
   - Kept, mostly: `${VAR}` placeholders already in the file are restored by a JSON-Patch trick. That
     same trick can **overwrite a user's edit with the old placeholder**, or crash DELETE/PUT/POST with
     a 500 (executed, §2).
   - Injected: `server.templates.path` (an absolute site-packages path) is written into the file
     (executed).
   - Our own config writer should be round-trip-preserving (e.g. ruamel.yaml) and should manage
     placeholders explicitly.
3. **Resolved secrets do not land in the config file, but they do land in the OpenAPI document.**
   With `server.admin: true`, the generated OpenAPI embeds the **entire resolved config** as request
   examples (`pygeoapi/openapi.py:819`). That covers both the entrypoint-generated file and the
   Admin-written one. The document is served publicly at `/openapi`. A `${FAKE_SECRET}` placeholder
   showed up in clear text in `openapi.yml` (executed, §2.3 / §6).
4. **There is no authentication or authorization** (`docs/source/admin-api.rst:29`,
   `docs/source/security.rst:11`). The Admin API must never be reachable outside a trusted network.
5. **Reload story.**
   - Config and OpenAPI are read once, at import (`pygeoapi/flask_app.py:55-56`,
     `pygeoapi/starlette_app.py:67-72`).
   - The supported hot-reload is gunicorn `--reload --reload-extra-file $PYGEOAPI_CONFIG`
     (`docker/entrypoint.sh:151`), which restarts the workers.
   - The OpenAPI document is **not** regenerated on reload. Only the entrypoint (at container start,
     `docker/entrypoint.sh:87`) and Admin API writes (`pygeoapi/api/admin.py:153-158`) regenerate it.
   - Publish targets that edit the file directly must regenerate OpenAPI themselves, then trigger a
     reload or restart.
6. **Sandbox image.** `FROM geopython/pygeoapi:0.24.0` gives:
   - Ubuntu noble with system GDAL/Fiona/rasterio from the ubuntugis-unstable PPA.
   - A venv at `/venv` created with `--system-site-packages`. There is no `PATH` change, so use
     `/venv/bin/python3 -m pip install ...`.
   - Runs as root. No `USER`, `EXPOSE`, `HEALTHCHECK` or `VOLUME` directive.
   - Listens on port 80.
   - Starts by generating OpenAPI with `--fail-on-invalid-collection`, so a provider that fails to
     load **aborts container start** (read, §4). In a probe container, set
     `PYGEOAPI_OPENAPI_GENERATE_FAIL_ON_INVALID_COLLECTION=false`, or better, bypass the entrypoint.

---

## 1. Admin API (code reading, confirmed by execution where noted)

### 1.1 Enabling and dependencies

- **Enabling.** Set `server.admin: true`. Each framework checks it at import:
  - Flask imports the admin module (`pygeoapi/flask_app.py:61-63`), creates `admin_ = Admin(CONFIG, OPENAPI)`
    and registers the blueprint (`pygeoapi/flask_app.py:651-653`).
  - Starlette does the same (`pygeoapi/starlette_app.py:75-77`, `pygeoapi/starlette_app.py:774-776`).
  - Django checks it in `pygeoapi/django_/views.py:54-55` and `pygeoapi/django_/urls.py:286-305`.
  - Schema: `admin` is `type: boolean`, default false (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:34-37`).
  - With admin disabled, the routes do not exist.
- **Extra requirements.** `requirements-admin.txt:1-3` lists `filelock`, `jsonpatch` and `gunicorn`.
  - `filelock` is also a core dependency (`requirements.txt:3`).
  - `jsonpatch` is imported at module top (`pygeoapi/api/admin.py:39`), so the admin module does not
    import without it (executed: absent from the project `.venv`).
- **filelock usage.** It is used only in `yaml_dump`:
  - It is imported at `pygeoapi/util.py:33`.
  - `yaml_dump` takes `FileLock(f'{destfile}.lock')` (`pygeoapi/util.py:219-221`).
  - Zero-byte `config.yml.lock` and `openapi.yml.lock` files are left next to the targets
    (executed: `ls` after a write). The directory holding `PYGEOAPI_CONFIG` must therefore be writable.
- **Authentication.** There is none in code. The docs say access control is the deployer's job
  (`docs/source/admin-api.rst:29-30`).

### 1.2 Routes and framework wiring

| Route | Methods | Flask | Starlette | Django |
|---|---|---|---|---|
| `/admin/config` | GET, PUT, PATCH | `pygeoapi/flask_app.py:580-598` | `pygeoapi/starlette_app.py:619-634`, `:768` | `pygeoapi/django_/views.py:518-532` |
| `/admin/config/resources` | GET, POST | `pygeoapi/flask_app.py:601-615` (POST→`post_resource`) | `pygeoapi/starlette_app.py:637-649`: **POST is wired to `put_resource`** (bug) | `pygeoapi/django_/views.py:535-546`: **POST is wired to `put_resource`** (bug) |
| `/admin/config/resources/{id}` | GET, PUT, PATCH, DELETE | `pygeoapi/flask_app.py:618-646`, `<resource_id>` (no slashes) | `pygeoapi/starlette_app.py:652-680`, `{resource_id:path}` (`:770`) | `pygeoapi/django_/views.py:549-571`, `<str:resource_id>` (`pygeoapi/django_/urls.py:299`) |

These routes match the task's hypothesis. Framework notes:

- **Flask** is the only fully working wiring (executed).
  - Routes sit under the API-rules URL prefix (`pygeoapi/flask_app.py:78-83`).
  - A 500 comes back as Flask's HTML error page (executed).
- **Starlette.**
  - `POST /admin/config/resources` calls `put_resource(admin, request)` without `resource_id`
    (`pygeoapi/starlette_app.py:648`), so creation via Starlette cannot work (read; execution in §2.7).
  - A `dict` body goes out through `JSONResponse` and a `str` body through `Response`
    (`pygeoapi/starlette_app.py:110-122`).
  - gzip is skipped for 204 (`pygeoapi/starlette_app.py:140-143`).
- **Django.**
  - `execute_from_django` builds a new `Admin` per request from `from pygeoapi.admin import Admin`
    (`pygeoapi/django_/views.py:578-580`). There is no `pygeoapi/admin.py` (executed: `ls`), so with
    `admin: true` **every** Django request should raise `ModuleNotFoundError` (read; execution in §2.7).
  - POST has the same `put_resource` bug (`pygeoapi/django_/views.py:546`).
  - Writes need CSRF middleware disabled (`docs/source/running.rst:166`).
- **All frameworks.** Admin handlers build headers with `request.get_response_headers()` without the
  API-rules headers (`pygeoapi/api/admin.py:174`).
  - The default headers are `Content-Type: application/json` and `X-Powered-By`
    (`pygeoapi/api/__init__.py:77-80`).
  - The request body is parsed with `json.loads` whatever the request `Content-Type`
    (`pygeoapi/api/admin.py:218-219`).
  - Flask reads the body from `request.data` (`pygeoapi/api/__init__.py:216-220`), Starlette from
    `await request.body()` (`pygeoapi/api/__init__.py:223-228`).

### 1.3 Request/response shapes and status codes (Flask, executed unless noted)

| Operation | Body | Success | Errors |
|---|---|---|---|
| `GET /admin/config` | none | **200**, JSON of the **raw** file (`get_config(raw=True)`, `pygeoapi/api/admin.py:176`). `?f=html` renders `admin/index.html` (`pygeoapi/api/admin.py:178-182`) | `?f=xml` → 400 `InvalidParameterValue` |
| `PUT /admin/config` | full config JSON | **204**, empty body (`pygeoapi/api/admin.py:240-242`) | 400 `MissingParameterValue` (empty body, `:205-210`), 400 `InvalidParameterValue` (bad JSON, `:218-228`), 400 `ValidationError` (`:231-238`) |
| `PATCH /admin/config` | partial config JSON | **204**. The handler returns the merged config as content (`pygeoapi/api/admin.py:299-301`), but it is dropped with the 204 | same as PUT |
| `GET /admin/config/resources` | none | **200**, JSON object of raw resources (`pygeoapi/api/admin.py:317-325`) | |
| `POST /admin/config/resources` | `{"<id>": {...}}`. The first key is the id (`pygeoapi/api/admin.py:369`) | **201**. Body is the text `Location: /admin/config/resources/<id>` with `Content-Type: application/json` and **no `Location` header** (`pygeoapi/api/admin.py:393-396`) | 400 `NoApplicableCode` `Resource exists: <id>` (`:371-377`). The check runs against the **in-memory** config, not the file. 400 `ValidationError`. **500** for `{}` (IndexError), a JSON array or string (AttributeError) or an unparsable `extents.temporal.begin` (dateutil `ParserError`) (executed) |
| `GET /admin/config/resources/{id}` | none | **200** raw resource JSON (`pygeoapi/api/admin.py:413-431`) | **400** (not 404) `ResourceNotFound` (`:415-421`) |
| `PUT /admin/config/resources/{id}` | bare resource object (no id wrapper) | **204** (`pygeoapi/api/admin.py:524-536`) | 400 `ResourceNotFound` if the id is not in memory (`:490-497`). PUT **cannot create**. |
| `PATCH /admin/config/resources/{id}` | partial resource object | **204** (`pygeoapi/api/admin.py:588-605`) | 400 `ResourceNotFound` (`:554-561`), 400 `ValidationError` |
| `DELETE /admin/config/resources/{id}` | none | **204** (`pygeoapi/api/admin.py:468-470`) | 400 `ResourceNotFound` (`:449-456`) |

- **Error body.** Built by `API.get_exception` (`pygeoapi/api/__init__.py:582-586`):
  `{"code": "<Code>", "type": "<Code>", "description": "<msg>"}`.
- **Validation failure.** The jsonschema detail is only logged. The client always gets
  `{"code":"ValidationError","type":"ValidationError","description":"Schema validation error"}` with
  400 (`pygeoapi/api/admin.py:233-238`) (executed). The actual jsonschema message, e.g.
  `1 is not valid under any of the given schemas`, appeared only in the server log.
- **Encoding in GET responses.** JSON is produced by `to_json`, which **HTML-escapes `<` and `>`** to
  `&lt;`/`&gt;` (`pygeoapi/util.py:270-272`). GET returned the map attribution as
  `&copy; &lt;a href=...&gt;` (executed). A GET→PUT round trip therefore corrupts any value containing
  `<` or `>` (§2.2).
- **Datetimes.** `extents.temporal` datetimes are returned in ISO format (`2000-10-30T18:24:39+00:00`)
  (executed). They are parsed back to `datetime` on write by `temporal_extents_str2datetime`
  (`pygeoapi/api/admin.py:608-621`).
- **Placeholders in GET.** GET returns them raw. `server.url` came back as
  `${PYGEOAPI_SERVER_URL:-http://localhost:5000}` and the href as `...token=${FAKE_SECRET}`, so GET does
  not leak resolved secrets (executed).

### 1.4 Semantics of each write

- **Common pattern.** Every write:
  1. starts from `deepcopy(admin.config)`, the **in-memory startup config**. This applies to PATCH
     config, POST, PUT resource, PATCH resource and DELETE (`pygeoapi/api/admin.py:257`, `:341`,
     `:446`, `:487`, `:551`). `PUT /admin/config` uses the request body instead;
  2. runs `admin.validate()` (`pygeoapi/api/admin.py:93-107`), which calls `validate_config`;
  3. on success runs `admin.write(config)`, which calls `write_config` and then `write_oas`
     (`pygeoapi/api/admin.py:109-117`).

  So "validate first (400 on failure), then regenerate OpenAPI" is **confirmed**. OpenAPI-document
  validation is commented out (`pygeoapi/api/admin.py:104-106`, `:154`).
- **PUT `/admin/config`.** Wholesale replacement with the body after JSON-schema validation
  (`pygeoapi/api/admin.py:218-240`).
  - The top-level schema requires only `server`, `logging` and `metadata`
    (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:752-755`). **`resources` is not
    required**, and no level sets `additionalProperties: false`.
- **PUT resource.** Replaces the resource object wholesale (`pygeoapi/api/admin.py:524`).
- **PATCH (config and resource).** A custom recursive merge, `Admin.merge` (`pygeoapi/api/admin.py:70-91`).
  **It is not RFC 7396 JSON Merge Patch and not RFC 6902 JSON Patch.**
  - dict + dict → recursive key merge; new keys are added (`pygeoapi/api/admin.py:80-87`).
  - list + list → `[merge(a, b) for a, b in zip(old, new)]` (`pygeoapi/api/admin.py:88-89`). **The
    result is truncated to the shorter list.** You cannot append; a shorter list truncates; `[]`
    empties (read; execution in §2.5).
  - Anything else → the new value replaces the old (`pygeoapi/api/admin.py:90-91`). `null` does
    **not** delete a key; it sets `None`, which then usually fails validation.
- **POST.** `config['resources'].update(data)` (`pygeoapi/api/admin.py:380`). Only the first key is
  used for the duplicate check (`pygeoapi/api/admin.py:369-371`). Extra keys in the body would also be
  added by `update()` (read).
- **DELETE.** `config['resources'].pop(id)` (`pygeoapi/api/admin.py:451`).

### 1.5 Validation strength

`validate_config` is plain JSON-schema validation of `json.loads(to_json(config))` against
`pygeoapi-config-0.x.yml` (`pygeoapi/config.py:74-85`).

- **It never instantiates providers.** A resource whose provider plugin does not exist
  (`name: NoSuchPlugin`) passes validation (executed, §2.6).
- **The process-resource branch has a typo, `requred:`**
  (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:693`), so `type`/`processor` are not
  enforced there (read).
- **Placeholders in typed fields fail validation.** `server.admin` is boolean
  (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:34-35`), so a raw placeholder such as the
  Docker default `admin: ${PYGEOAPI_SERVER_ADMIN:-false}` (`docker/default.config.yml:48`) is a string
  and fails. GET→PUT of such a config returns 400 (read; execution in §2.5).

### 1.6 The write path in detail

1. **`write_config`** (`pygeoapi/api/admin.py:119-142`):
   1. It deep-copies and re-validates.
   2. **Env-var preservation.** It reads the *current file* twice, raw (`yaml.safe_load`) and resolved
      (`yaml_load`) (`pygeoapi/api/admin.py:132-133`). It computes
      `patch = make_patch(conf, raw_conf)`, the RFC 6902 ops that turn resolved into raw
      (`pygeoapi/api/admin.py:134`), and applies it to the **new** config
      (`pygeoapi/api/admin.py:137`). Consequences:
      - Placeholders that already exist in the file are re-inserted at the **same JSON-pointer
        paths**, whatever the new config holds there. A `replace` op overwrites unconditionally, so a
        user's change to a placeholder-backed value is **silently discarded** (read; execution in §2.5).
      - If a path no longer exists in the new config, `patch.apply` raises
        `jsonpointer.JsonPointerException`, **uncaught → HTTP 500, nothing written**. That happens
        when a resource with a placeholder was deleted or renamed, or the file has a placeholder in a
        resource the in-memory config does not know about. The second case was executed in §2.4.
      - The patch comes from `to_json` output, which escapes `<`/`>`, so a restored placeholder
        value containing `<` or `>` would be written escaped (read).
   3. **Serialization.** `yaml_dump(config, self.PYGEOAPI_CONFIG)` (`pygeoapi/api/admin.py:141`) uses
      PyYAML's default `yaml.dump` (the `Dumper` class, not `SafeDumper`) with `sort_keys=False, indent=4,
      default_flow_style=False, allow_unicode=True` (`pygeoapi/util.py:224-226`).
      - It registers a `PurePath`→str representer (`pygeoapi/util.py:202-205`).
      - It registers a datetime representer that writes `%Y-%m-%dT%H:%M:%SZ` in UTC
        (`pygeoapi/util.py:207-217`).
   4. **Atomicity: none.** The target is opened with `open(destfile, 'wb')` inside the lock and written
      in place, with no temp file and no rename (`pygeoapi/util.py:223`). A crash mid-write leaves a
      truncated file. The lock only guards against concurrent `yaml_dump` calls that honour the same
      `.lock` file. In-place writing does work on single-file Docker bind mounts, where a rename
      would fail.
2. **Loader.** Reads use `yaml_load` (`pygeoapi/util.py:130-172`), an `EnvVarLoader(yaml.SafeLoader)`
   with an implicit resolver for `${VAR}` / `${VAR:-default}`
   (`pygeoapi/util.py:142-143`, `pygeoapi/util.py:167-172`).
   - Missing var and no default → `EnvironmentError` (`pygeoapi/util.py:158`).
   - Resolved strings are type-coerced by `get_typed_value` (`pygeoapi/util.py:106-127`, called at
     `pygeoapi/util.py:165`): `"5000"` → int, `"true"` → bool, `"1.5"` → float.
   - The raw read for GET and patch computation is plain `yaml.safe_load` (`pygeoapi/config.py:57-58`).
   - Implicit resolvers apply only to plain scalars, so **a quoted placeholder (`'${X}'`) is never
     interpolated** (read, PyYAML semantics; execution in §2.5).
3. **`write_oas`** (`pygeoapi/api/admin.py:144-159`):
   - It calls `get_oas(config)` with the default `fail_on_invalid_collection=True`
     (`pygeoapi/openapi.py:985-1000`). That **instantiates every collection's provider**
     (`pygeoapi/api/itemtypes.py:1094`, reads `p.fields` at `pygeoapi/api/itemtypes.py:1105`), so a
     write costs one plugin load per collection and may do network I/O.
   - The document is written with the same `yaml_dump` to `Admin.PYGEOAPI_OPENAPI`
     (`pygeoapi/api/admin.py:158`).
   - Because `write_config` runs **first**, a provider that fails to load gives **500 after the config
     file was already written, with the OpenAPI file left stale** (executed, §2.6).
4. **Where the files go.** `Admin.PYGEOAPI_CONFIG` and `Admin.PYGEOAPI_OPENAPI` are class attributes
   taken from `os.environ` **when `pygeoapi.api.admin` is imported** (`pygeoapi/api/admin.py:55-56`).
   The raw/resolved reads use `os.environ['PYGEOAPI_CONFIG']` at call time (`pygeoapi/config.py:53-56`).
   The unit tests have to monkeypatch the class attributes for this reason
   (`tests/api/test_admin.py:71-75`).

### 1.7 In-memory state vs the file: what is live after a write?

- **Reads vs writes.** GET handlers re-read the file on every request (`pygeoapi/api/admin.py:176`,
  `:317`, `:413`). Writes start from `admin.config`, the dict loaded at import (§1.4).
- **Nothing refreshes the in-memory state.** `admin.config`, `api_.config` and `api_.openapi` are
  never updated after a write. The only assignments are in `API.__init__` (`pygeoapi/api/__init__.py:524-525`).
- **Consequences (executed, Flask):**
  - After `POST lakes2` (201), `GET /collections/lakes2` → **404**, and `/collections` still lists only
    the startup resources. `GET /admin/config/resources` **does** list `lakes2`, because it reads the
    file.
  - The served `/openapi` does not mention `lakes2`; `openapi.yml` on disk does.
  - A second write in the same process is based on the startup config, so the first write is lost
    (§2.4).
- **Multiple workers.** Each gunicorn worker imports the app separately and holds its own copy (read).
  Without a restart, a new resource is never live in any worker.
- **The only supported way to make writes live** is to restart the workers:
  - gunicorn `--reload --reload-extra-file $PYGEOAPI_CONFIG` (`docs/source/admin-api.rst:35-48`,
    `docker/entrypoint.sh:151`);
  - or restart the process yourself.
  - The upstream live test sleeps 5 s after each write "to let gunicorn restart with the refreshed
    configuration" (`tests/api/test_admin_live.py:78-80`).
  - The unit tests rebuild the `Admin` object between writes (`tests/api/test_admin.py:66-77`, used at
    `tests/api/test_admin.py:97`).
- **Shared-config side effects that end up in written files (executed):**
  - `API.__init__` adds `server.templates = {'path': TEMPLATESDIR}` when missing
    (`pygeoapi/api/__init__.py:541-542`) and `pretty_print: false` when missing
    (`pygeoapi/api/__init__.py:544-545`). This mutates the same dict the Admin object uses (the same
    `CONFIG` passed at `pygeoapi/flask_app.py:96` and `pygeoapi/flask_app.py:652`).
  - Every admin write therefore bakes an **absolute, installation-specific** templates path into the
    config file.
  - When `server.api_rules` exists, `get_api_rules` adds `api_version` to it
    (`pygeoapi/util.py:181-182`) (read; execution in §2.5).

---

## 2. Round-trip fidelity experiments (executed, Flask test client)

**Setup.** Driver: scratch `run_scenario.py`, one fresh process per scenario.

- `PYGEOAPI_CONFIG` and `PYGEOAPI_OPENAPI` point at scratch copies. Data paths point at copies of
  `tests/data/ne_110m_lakes.geojson` and `tests/data/obs.csv`.
- OpenAPI is pre-generated with `generate_openapi_document` before import, as the entrypoint does.
- `FAKE_SECRET=sk-FAKE-SECRET-12345` is set. `PYGEOAPI_SERVER_URL` and `DATA_DIR` are unset, so their
  defaults apply.
- The base config contains:
  - a header comment and inline comments;
  - single- and double-quoted scalars;
  - flow-style `bbox: [...]` lists;
  - `${PYGEOAPI_SERVER_URL:-http://localhost:5000}`;
  - `data: ${DATA_DIR:-/…/data}/ne_110m_lakes.geojson`;
  - `href: https://example.org/lakes?token=${FAKE_SECRET}` and `options.api_key: ${FAKE_SECRET}`;
  - an unknown top-level key `x-custom-top-level` and an unknown resource key `x-note`;
  - an anchor `links: &shared_links` with alias `links: *shared_links`;
  - YAML timestamps.

### 2.1 What survives a write (any write: POST shown)

| Property | Result |
|---|---|
| (a) YAML comments | **Lost**: header, inline and trailing comments all removed |
| (b) key order | **Preserved** (`sort_keys=False`, `pygeoapi/util.py:224`) |
| (c) quoting style | **Not preserved**. `'application/json; charset=UTF-8'` and `"utf-8"` became plain. Flow lists `[-180, -90, 180, 90]` became block lists. List indentation changed. Long scalars are re-wrapped. Timestamps are rewritten (`2000-10-30 18:24:39+00:00` → `2000-10-30T18:24:39Z`) |
| (d) placeholders | **Existing ones restored** at their original paths: `${PYGEOAPI_SERVER_URL:-…}`, `${DATA_DIR:-…}/…`, `${FAKE_SECRET}`. **The resolved secret was not written to the config file** (`secret literal on disk in config? False`). New placeholders in a POST body are written literally. The restore mechanism has two sharp edges, see §2.4 and §2.5 |
| (e) unknown keys | **Preserved** (`x-custom-top-level`, `x-note`); the schema has no `additionalProperties: false` |
| (f) anchors/aliases | Structure preserved; **names lost**: `&shared_links`/`*shared_links` became `&id001`/`*id001` |
| Injected keys | `server.templates.path: /…/site-packages/pygeoapi/templates` was **added** (§1.7) |

### 2.2 POST diff (abridged)

```diff
-# HEADER COMMENT: admin API round-trip experiment
-        host: 0.0.0.0  # inline comment on host
+        host: 0.0.0.0
-    mimetype: 'application/json; charset=UTF-8'   # single-quoted
-    encoding: "utf-8"   # double-quoted
+    mimetype: application/json; charset=UTF-8
+    encoding: utf-8
-        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap contributors</a>'
+        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap
+            contributors</a>'
+    templates:
+        path: /…/venv/lib/python3.12/site-packages/pygeoapi/templates
-        links: &shared_links
-            - type: text/html
+        links: &id001
+        -   type: text/html
             href: https://example.org/lakes?token=${FAKE_SECRET}      (unchanged: placeholder kept)
-                bbox: [-180, -90, 180, 90]   # flow-style list
+                bbox:
+                - -180
-        links: *shared_links   # alias
+        links: *id001
-                begin: 2000-10-30 18:24:39+00:00
+                begin: 2000-10-30T18:24:39Z
+    lakes2:                                                        (the POSTed resource, appended last)
+        ...
+            href: https://example.org/?t=${FAKE_SECRET}            (literal from request body)
+            temporal:
+                begin: 2020-01-01T00:00:00Z
+                end: null
```

**GET→PUT hazard.** GET HTML-escapes `<`/`>` (§1.3). PUTting a GET body back unchanged writes
`&lt;a href=…&gt;` into the attribution (read; execution pending in §2.5).

### 2.3 Secrets in the OpenAPI document

`openapi.yml` held the literal `sk-FAKE-SECRET-12345` both after pre-generation and after the admin
write (`in openapi? True`, executed). The path:

- `get_oas_30` adds the admin paths whenever `server.admin` is true (`pygeoapi/openapi.py:568-572`).
- `get_admin` embeds the whole config as the `PUT /admin/config` example (`pygeoapi/openapi.py:819`).
  It also embeds `metadata` (`:840`) and the first resource (`:757-759`, `:883`, `:930`).
- The config passed in is the **resolved** one: `yaml_load` in `generate_openapi_document`
  (`pygeoapi/openapi.py:1043-1049`) and the in-memory config in `write_oas`.

(Serving by `GET /openapi` is confirmed in §2.5.)

`get_admin` also **mutates** `cfg['resources'][first]` by adding a `patch_example` key
(`pygeoapi/openapi.py:785-787`), because `res_eg` references the same dict (`pygeoapi/openapi.py:757-759`).
`write_oas` deep-copies first (`pygeoapi/api/admin.py:152`), so the config file is not affected (read).

### 2.4 Stale in-memory config: lost updates and poisoned workers (executed)

- **Lost update.** Sequence in one process, placeholder-free resources:
  1. `POST first` → 201, file has `first`.
  2. `POST second` → 201, file has `second` but **`first` is gone**.
  3. `PATCH plain` → 204, **both gone**.
  4. `DELETE second` (on disk, not in memory) → 400 `ResourceNotFound`.
- **Poisoned worker.** `POST lakes2` with an href containing `${FAKE_SECRET}` → 201. Then
  `POST lakes3` → **500**, `jsonpointer.JsonPointerException: member 'lakes2' not found`. The patch
  computed from the file points into `lakes2`, which the in-memory config lacks. `lakes3` was not
  written. Every later write in that worker fails the same way until restart.
- **Duplicate detection** uses the in-memory config. POSTing an id that exists only on disk is not
  rejected as a duplicate.

### 2.5 PATCH / PUT / DELETE / PUT-config diffs

(Pending execution. See the next revision of this section.)

### 2.6 Validation vs provider loading (executed)

- A resource with `providers[0].name: NoSuchPlugin`:
  - passes schema validation;
  - `write_config` writes it;
  - `write_oas` raises `InvalidPluginError` → **500 HTML**;
  - `config.yml` now contains `broken`, and `openapi.yml` is unchanged (stale).
- Regenerating like the entrypoint (`pygeoapi openapi generate … --fail-on-invalid-collection`) then
  exits **1**, so a container restart would abort (`docker/entrypoint.sh:87-89`).
  `--no-fail-on-invalid-collection` exits 0.
- A failed `generate` did not create/truncate its output file. `click.File('w')` is lazy
  (`pygeoapi/openapi.py:1100`).
- A GeoJSON provider pointing at a **non-existent file** did *not* fail OpenAPI generation. The POST
  returned 201 and the resource entered `openapi.yml`. OpenAPI success is not a proof that the provider
  works.

### 2.7 Starlette and Django

(Pending execution.)

---

## 3. Server lifecycle and reload

- **Config and OpenAPI are read once, at import:**
  - Flask: `CONFIG = get_config()` and `OPENAPI = load_openapi_document()` (`pygeoapi/flask_app.py:55-56`).
  - Starlette: `pygeoapi/starlette_app.py:67-72`. Starlette additionally requires the
    `PYGEOAPI_OPENAPI` env var up front (`pygeoapi/starlette_app.py:69-70`).
  - `get_config` raises `RuntimeError` if `PYGEOAPI_CONFIG` is unset (`pygeoapi/config.py:53-54`).
  - The docs agree: config changes need a restart "(configurations are loaded once at server startup
    for performance)" (`docs/source/running.rst:29`).
- **OpenAPI must be pre-generated.** `load_openapi_document` raises if the file does not exist
  ("Please generate before starting pygeoapi", `pygeoapi/openapi.py:1072-1076`).
  - It is YAML-parsed only for `.yaml`/`.yml` names. Any other extension is kept as a raw string
    (`pygeoapi/openapi.py:1079-1082`).
  - Nobody generates it at server startup. Generation happens only through `pygeoapi openapi generate`
    (`pygeoapi/openapi.py:1102-1117`), which the Docker entrypoint runs (`docker/entrypoint.sh:87`),
    and through Admin API writes (`pygeoapi/api/admin.py:153-158`).
  - The CLI dumps with `yaml.safe_dump` (`pygeoapi/openapi.py:1052`); Admin uses `yaml_dump`.
- **Picking up a resource that someone else wrote to the file:**
  - **Restart** the process or container. In Docker this also regenerates OpenAPI (§4).
  - **gunicorn `--reload --reload-extra-file <config>`.** Each worker runs a reloader
    (`ext:gunicorn/workers/base.py:122-132`, gunicorn 23.0.0). The polling reloader stats the files
    every 1 s (`ext:gunicorn/reloader.py:17-21`, `ext:gunicorn/reloader.py:38-53`). On change the
    worker sets `alive = False` and exits (`ext:gunicorn/workers/base.py:123-129`); the arbiter spawns
    a replacement, which re-imports the app and re-reads config and OpenAPI. Caveats:
    - **OpenAPI is not regenerated**, so a file edited by a third party yields `/collections` with the
      new resource but a stale `/openapi` unless the writer also regenerated it. Admin writes do
      regenerate it, but only after the config is written (`pygeoapi/api/admin.py:116-117`), so a
      worker that reloads in between can load the old OpenAPI (race, read).
    - `--reload` is documented by gunicorn as a development feature (read).
  - **SIGHUP to the gunicorn master** reloads config and gracefully replaces workers
    (`ext:gunicorn/arbiter.py:244`, `ext:gunicorn/arbiter.py:433`). Without `--preload` the new
    workers re-import pygeoapi, so config and OpenAPI are re-read (read; not executed).
  - **Dev servers.** `pygeoapi serve` runs Flask with `debug=True` hard-coded, whatever `--debug` says
    (`pygeoapi/flask_app.py:671`). Starlette runs uvicorn with `reload=True` (`pygeoapi/starlette_app.py:842-848`).
    These reloaders watch Python sources, not the YAML config (read; not executed).

---

## 4. The official Docker image `geopython/pygeoapi:0.24.0` (all read; daemon not running)

**Build**

| Item | Value |
|---|---|
| Base image | `ubuntu:noble` (`Dockerfile:37`) |
| Python | System `python3` of Ubuntu noble. The version is not pinned anywhere in the repo; noble ships 3.12, **unverified** (check `docker run --rm --entrypoint /venv/bin/python3 geopython/pygeoapi:0.24.0 -V`) |
| venv | `python3 -m venv --system-site-packages /venv` (`Dockerfile:133`). pip installs go into `/venv`; apt Python packages are visible through system site-packages |
| pip | `python3-pip` and `python3-venv` are apt-installed (`Dockerfile:100-102`). `/venv/bin/python3 -m pip` is used in the build (`Dockerfile:134-137`). **`PATH` is not changed** (`Dockerfile:87-105`), so a bare `pip` in a derived image is the system pip. Ubuntu's system Python is PEP 668 externally managed, so that fails (unverified). Use `/venv/bin/python3 -m pip install …`, which is not affected by PEP 668 |
| System packages | Build deps `software-properties-common curl unzip` (`Dockerfile:90-93`), purged afterwards (`Dockerfile:125`). Runtime: `locales tzdata python3-dateutil python3-gevent python3-greenlet python3-pip python3-tz python3-venv python3-yaml` (`Dockerfile:94-104`) |
| Provider packages via apt (`ADD_DEB_PACKAGES`, `Dockerfile:63-84`) | `libsqlite3-mod-spatialite python3-dask python3-elasticsearch python3-fiona python3-gdal python3-jsonpatch python3-netcdf4 python3-pandas python3-psycopg2 python3-pydantic python3-pymongo python3-pyproj python3-rasterio python3-scipy python3-shapely python3-tinydb python3-xarray python3-zarr python3-mapscript python3-pytest python3-pyld` |
| GDAL/PROJ | Via `python3-gdal` from the **ubuntugis-unstable PPA** (`Dockerfile:112`). **Version not found in repo.** `requirements-provider.txt:7` (`GDAL<=3.11.3`) is **not** used by the image. `PROJ_LIB=/usr/share/proj` (`Dockerfile:105`) |
| pip-installed | `requirements-docker.txt` (`Dockerfile:134`): `elasticsearch-dsl<8 geoalchemy2 pygeofilter[backend-sqlalchemy] pygeoif pygeometa sodapy s3fs<=2023.6.0 Flask>=2.2.0 Flask-Cors urllib3>2 botocore>1.42` (`requirements-docker.txt:1-11`). Then `requirements-admin.txt` (`Dockerfile:135`), `"gunicorn<24"` (`Dockerfile:136`), and `pip install -e .` (`Dockerfile:137`, editable install of `/pygeoapi`, which pulls `requirements.txt`) |
| Not installed | `requirements-provider.txt`, `requirements-starlette.txt` (no starlette/uvicorn, so `WSGI_APP=pygeoapi.starlette_app:APP` would need a derived image), `requirements-django.txt`, `requirements-manager.txt`, `requirements-pubsub.txt` (read) |
| Source & data | `ADD . /pygeoapi` (`Dockerfile:130`): the whole repo, including `tests/data` used by the default config |
| OGC schemas | Downloaded to `/schemas.opengis.net` at build (`Dockerfile:117-120`); the default config points at it (`docker/default.config.yml:56`) |
| Default config | `docker/default.config.yml` copied to `/pygeoapi/local.config.yml` (`Dockerfile:141`) |
| Permissions | `chmod -R g=u /pygeoapi` (`Dockerfile:145`): group gets the owner's rights, so an arbitrary UID in GID 0 can write |
| User | **root**: no `USER` directive (executed: `grep` found no `USER`/`EXPOSE`/`HEALTHCHECK`/`VOLUME` in `Dockerfile` or `docker/`) |
| WORKDIR | `/pygeoapi` (`Dockerfile:107`); the entrypoint also `cd`s to `$PYGEOAPI_HOME` (`docker/entrypoint.sh:82`) |
| Ports | No `EXPOSE`. gunicorn binds `${CONTAINER_HOST}:${CONTAINER_PORT}`, default `0.0.0.0:80` (`docker/entrypoint.sh:56-57`, `:107`) |
| HEALTHCHECK | None |
| ENTRYPOINT | `/entrypoint.sh` (`Dockerfile:147`), copied from `docker/entrypoint.sh` (`Dockerfile:142`) |

**Environment variables honoured by `docker/entrypoint.sh`** (complete list from the script)

| Var | Default | Where |
|---|---|---|
| `PYGEOAPI_HOME` | forced to `/pygeoapi`; not overridable | `docker/entrypoint.sh:41` |
| `PYGEOAPI_CONFIG` | `/pygeoapi/local.config.yml` | `docker/entrypoint.sh:43-45` |
| `PYGEOAPI_OPENAPI` | `/pygeoapi/local.openapi.yml` | `docker/entrypoint.sh:46-48` |
| `PYGEOAPI_ASYNCAPI` | `/pygeoapi/local.asyncapi.yml` | `docker/entrypoint.sh:49-51` |
| `SCRIPT_NAME` | `/`, turned into `""` before gunicorn | `docker/entrypoint.sh:54`, `:100` |
| `CONTAINER_NAME` | `pygeoapi` (gunicorn `--name`) | `docker/entrypoint.sh:55` |
| `CONTAINER_HOST` | `0.0.0.0` | `docker/entrypoint.sh:56` |
| `CONTAINER_PORT` | `80` | `docker/entrypoint.sh:57` |
| `WSGI_APP` | `pygeoapi.flask_app:APP` | `docker/entrypoint.sh:58` |
| `WSGI_WORKERS` | `4` | `docker/entrypoint.sh:59` |
| `WSGI_WORKER_TIMEOUT` | `6000` | `docker/entrypoint.sh:60` |
| `WSGI_WORKER_CLASS` | `gevent` | `docker/entrypoint.sh:61` |
| `PYGEOAPI_OPENAPI_GENERATE_FAIL_ON_INVALID_COLLECTION` | `true`. Exactly `false` selects `--no-fail-on-invalid-collection` | `docker/entrypoint.sh:64-70` |

Only the default config's placeholders use `PYGEOAPI_SERVER_URL` and `PYGEOAPI_SERVER_ADMIN`
(`docker/default.config.yml:41`, `docker/default.config.yml:48`). The script never reads them.

**Startup sequence (every command, including `test`)**

1. `cd /pygeoapi` (`docker/entrypoint.sh:82`).
2. `/venv/bin/pygeoapi openapi generate $PYGEOAPI_CONFIG --output-file $PYGEOAPI_OPENAPI
   --fail-on-invalid-collection` (`docker/entrypoint.sh:87`). A non-zero exit calls `error` →
   `exit -1`, **so the container stops** (`docker/entrypoint.sh:76-79`, `:89`). Generation loads
   every collection's provider (§1.6.3), so the provider under test is exercised at boot. The default
   config's OGR/WFS collections (`docker/default.config.yml:212-216`) are therefore likely to need
   network at boot (read).
3. `asyncapi generate`; failure is only logged (`docker/entrypoint.sh:94-96`).
4. **There is no separate config validation step.** The config is validated only implicitly: a YAML
   parse or env-var resolution error fails step 2 (read).

**Commands** (`entry_cmd=${1:-run}`, `docker/entrypoint.sh:73`)

- `run` (default): `exec /venv/bin/gunicorn --workers … --worker-class=… --timeout … --name=… --bind … ${WSGI_APP}`
  (`docker/entrypoint.sh:103-109`, `:136-139`). Extra `docker run` arguments are **not** forwarded:
  `${@}` inside `start_gunicorn` holds the function's own arguments (`docker/entrypoint.sh:108`).
  This is shell semantics, read.
- `run-with-hot-reload`: when running as root, first `chmod 0444` every `*.py` under `/pygeoapi`
  (`docker/entrypoint.sh:144-148`), then `start_gunicorn --reload --reload-extra-file ${PYGEOAPI_CONFIG}`
  (`docker/entrypoint.sh:151`). OpenAPI is still generated only once, at step 2.
- `test`: runs `pytest` on `tests/test_*.py` (`docker/entrypoint.sh:114-133`). In 0.24.0 that glob
  matches only `tests/test_models.py` (executed: `ls`), because the suite moved into subdirectories.
  The skip list names top-level files that no longer exist (`docker/entrypoint.sh:119-125`).
- Anything else → `error "unknown command arg…"` (`docker/entrypoint.sh:154-156`).

**Sandbox design notes**

- A derived image works:

  ```dockerfile
  FROM geopython/pygeoapi:0.24.0
  RUN /venv/bin/python3 -m pip install --no-cache-dir <runtime-pkg> <provider-pkg>
  ```

- The entrypoint needs root (for the hot-reload `chmod`) and writable `/pygeoapi` (for openapi/asyncapi
  output and admin `.lock` files). The image works as non-root only through the `g=u` trick.
- For probes, prefer running `/venv/bin/pygeoapi openapi generate …` ourselves and capturing its exit
  code and stderr, then starting gunicorn explicitly. Otherwise a failing provider just makes the
  container exit 255.
- Starlette is not in the image.

---

## 5. Docs vs code conflicts (code wins)

| Doc | Code | Note |
|---|---|---|
| `CONTAINER_HOST` default is `localhost` (`docs/source/docker.rst:107`) | `0.0.0.0` (`docker/entrypoint.sh:56`) | Doc wrong |
| `WSGI_WORKER_TIMEOUT` example sets `WSGI_WORKERS=10` (`docs/source/docker.rst:128-132`) | `WSGI_WORKER_TIMEOUT` (`docker/entrypoint.sh:60`) | Copy-paste error in doc |
| `PYGEOAPI_SERVER_URL` / `PYGEOAPI_SERVER_ADMIN` described as image env vars (`docs/source/docker.rst:83`, `:97`) | Only placeholders in the default config (`docker/default.config.yml:41`, `:48`) | They do nothing with a custom config unless it uses the same placeholders |
| Undocumented: `WSGI_APP`, `WSGI_WORKER_CLASS`, `CONTAINER_NAME`, `PYGEOAPI_ASYNCAPI` | `docker/entrypoint.sh:49-61` | |
| "Hot reload" in `docs/source/admin-api.rst:35-48` and `docs/source/docker.rst:30-34` | Workers restart but OpenAPI is not regenerated (`docker/entrypoint.sh:87`, `:151`) | |
| Admin API docs list no routes, no semantics and no Starlette/Django caveats (`docs/source/admin-api.rst:1-48`) | POST is mis-wired in Starlette/Django (`pygeoapi/starlette_app.py:648`, `pygeoapi/django_/views.py:546`); Django imports the non-existent `pygeoapi.admin` (`pygeoapi/django_/views.py:579`) | |
| "Hot-reloading" section (`docs/source/running.rst:168-188`) describes `pip install -e .` for code changes | Config changes still need a restart (`docs/source/running.rst:29`) | Consistent, but easy to misread as config hot reload |
| `test` command "run all unit tests" (`docs/source/docker.rst:36-40`) | Runs only `tests/test_models.py` (`docker/entrypoint.sh:115`) | |

---

## 6. Security notes

- **No built-in access control** (`docs/source/security.rst:11-12`). The docs point to external
  projects: fastgeoapi, pygeoapi-auth-deployment, pygeoapi-auth (`docs/source/security.rst:14-18`).
  Admin access control is explicitly out of scope (`docs/source/admin-api.rst:29-30`).
- **`/openapi` leaks resolved config when admin is enabled.** This includes any secret resolved from
  `${VAR}` (§2.3) (executed for the file; serving pending). Never enable `server.admin` on a
  public-facing instance whose config holds secrets. Provider Builder should flag it.
- **Default config ships `cors: true`** (`docker/default.config.yml:46`). With admin on, any origin can
  script the unauthenticated write endpoints from a browser (read).
- **Admin writes instantiate every provider** (§1.6.3). An unauthenticated caller can force plugin
  imports and outbound connections (read).
- **Config file permissions.** The Admin API needs write access to the config file and its directory
  (`.lock` files). The Docker image runs as root (§4).

---

## 7. Open questions for the user

1. Publish target: is "write the YAML ourselves + regenerate OpenAPI + SIGHUP/restart" acceptable
   instead of the Admin API, given §2.4?
2. Should Provider Builder refuse, or warn, when a target has `server.admin: true`, given the
   `/openapi` secret leak?
3. Must our writer preserve comments and anchors? That implies ruamel.yaml round-trip mode plus our
   own placeholder handling.
4. Sandbox: may we bypass `/entrypoint.sh`? The image's GDAL version must be checked once the Docker
   daemon is available.
