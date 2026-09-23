# pygeoapi 0.24.0: config model, validation and tooling

Phase 0 research for the pygeoapi Provider Builder. **Ground truth:** pygeoapi 0.24.0 (tag 0.24.0, commit f765a64), vendored at `vendor/pygeoapi`. It is byte-identical to the package in `.venv`: `diff -rq` of `pygeoapi/` against site-packages printed nothing.

**Conventions**
- Citations are `path:line`, relative to the pygeoapi repo root.
- **(read)** means I read it in the source. **(executed)** means I ran it against pygeoapi 0.24.0 in the project venv (Python 3.12.14, jsonschema 4.26.0, PyYAML 6.0.3, Flask 3.1.3).
- Scratch configs, scripts and outputs are in `/private/tmp/claude-501/-Users-konda-Documents-pygeoapi-provider-ui/6838b640-da34-42bf-b227-b524ac19219b/scratchpad/config-model/`.
- "Schema" means `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml`.

---

## 0. The rules a config-generating GUI must enforce (summary)

These rules go beyond the JSON Schema. Each one comes from a runtime failure I reproduced. Details and citations are in the sections below.

1. **Resource ids.** Only emit slug-like string ids. pygeoapi does no normalisation and no escaping. Spaces or `?#` produce broken self-links. An id ending in a route word (`…/items`) can't be reached. An empty id can't be reached. Unquoted YAML `123:` or `yes:` become int/bool keys and return 404 (§2).
2. **Every resource needs `type`.** Because of a schema typo, a resource with no `type` passes `validate_config`, but it makes `/collections` and `/conformance` return 500 (§3.1).
3. **Every link needs `title`**, even though the schema and docs don't require it. Without one, the collection silently disappears from `/collections`, and `/collections/{id}` returns 500 (§3.3).
4. **`extents.temporal`.** If present, it needs both `begin` and `end` keys, each an **unquoted** RFC 3339 timestamp or `null`. A quoted string or a date-only value returns 400 on every `datetime=` query. A missing `end` returns 500 (§3.4).
5. **`server.languages`** (or `language`) is required at runtime, although the schema doesn't require it. Without it the server doesn't start and `openapi generate` fails (§11).
6. **Env-var placeholders** must be written as **plain (unquoted) block-style YAML scalars**, `key: ${VAR}`. A quoted `"${VAR}"`, a block scalar (`|`/`>`) or a flow collection `[${A}]` is **not** substituted, or doesn't parse (§6).
7. **Tile and map providers need `format: {name, mimetype}`** in practice. Without it, `/collections/{id}` fails (§9).
8. **Multiple providers.** At most one provider per `type` is actually used, and it's the **first** of that type. `default: true` only changes the collection's primary type, not which provider of a type is used (§9).
9. **`validate_config` is only a schema check.** It doesn't load providers or check `format`, and its error message for resource-level mistakes is often misleading. The GUI should run its own `iter_errors` pass (§7).
10. **`pygeoapi openapi generate` works as a smoke test only with the default flag on**, and only for *visible* collections. It also needs `metadata.identification.terms_of_service` and `metadata.license.url`, which the schema doesn't require. With `--no-fail-on-invalid-collection`, one broken collection silently removes the item paths of **all** feature/record collections (§8).
11. **`title_field` must name a string-valued field.** A numeric one makes the item HTML page return 500. The items HTML page also needs `server.limits.default_items` (§15).
12. **Keep one data provider as the default.** Put it first, or mark it `default: true`. Never make a `tile` or `map` provider the default (§9).

---

## 1. Top-level sections

**Claim:** the sections are `server`, `logging`, `metadata` and `resources`. **Verdict: correct, plus an optional fifth.**

- The schema requires `server`, `logging`, `metadata` and `resources` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:752; `- server` pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:753, `- resources` pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:756). (read)
- `pubsub` is an optional fifth top-level section (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:170; `docs/source/configuration.rst:17`). (read)
- There is no `additionalProperties` anywhere in the schema, so unknown top-level keys are accepted. A `foo: 1` at the top level passed. (executed)
- `$schema` is draft 2020-12 (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:1). `jsonschema.validators.validator_for(schema)` resolves to `Draft202012Validator`. (executed)
- The docs link to the schema at the old path `pygeoapi/schemas/config/...` (`docs/source/configuration.rst:22`). The real path is `pygeoapi/resources/schemas/config/` (`pygeoapi/config.py:68`). (read)

## 2. Resource ids

**Claim:** each key under `resources` is a resource id, used as-is in `/collections/{id}`. **Verdict: correct. There is no normalisation and no validation of any kind.**

- The schema matches any key: `patternProperties: "^.*$"` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:372, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:373). (read)
- An empty id, `my lakes`, `a/b`, `ümlaut`, `a?b#c`, `../x`, `a\nb` and an int key all pass `validate_config`. (executed)
- The id goes out verbatim as the collection id, `'id': dataset` (`pygeoapi/api/collection.py:70`). Lookup is exact, case-sensitive dict membership (`pygeoapi/api/__init__.py:935`). (read) `/collections/upper` returned 404 for id `UPPER`. (executed)
- Routes take the id as a *path* converter, so ids may contain `/`: Flask `<path:collection_id>` (`pygeoapi/flask_app.py:234`), Starlette `{collection_id:path}` (`pygeoapi/starlette_app.py:760`). (read)
- Slashes in ids are a documented feature, "hierarchical collections" (`docs/source/configuration.rst:501`, `tests/pygeoapi-test-config.yml:220` `naturalearth/lakes`). (read)
- Links are built by f-string with no percent-encoding, for example the items link `f'{api.get_collections_url()}/{dataset}/items?f={F_JSON}'` (`pygeoapi/api/collection.py:251`). (read)
- OpenAPI `operationId` is built from `k.capitalize()` (`pygeoapi/api/itemtypes.py:1122`). (read)

Executed with the Flask test client, one config holding all of these ids (`ids.yml`, `intkey.yml`, `boolkey.yml`):

| id in YAML | `GET /collections/<id>` | Notes |
|---|---|---|
| `my lakes` | 200 via `my%20lakes` | Generated href is `http://localhost:5000/collections/my lakes/items?f=json` (raw space) |
| `a?b#c` | 200 via `a%3Fb%23c` | Generated href `…/collections/a?b#c/items?f=json` is broken |
| `ümlaut` | 200 via `%C3%BCmlaut` | Generated href has a raw `ü` (IRI, not URI) |
| `a/b` | 200 (`/collections/a/b`, `/collections/a/b/items`) | Hierarchical, works |
| `lakes/items` | **404** at `/collections/lakes/items` and `/collections/lakes/items/items` | The route `…/<path:id>/items` wins, so the id is unreachable |
| `items` | 200 | Fine as a plain id |
| `../x` | 200 in the test client | Real clients and proxies normalise `..`. Unsafe |
| `''` (empty) | unreachable | `/collections/` lists all collections, and `/collections//items` redirects (308) to `/collections/items` |
| `UPPER` | 200; `/collections/upper` 404 | Case-sensitive |
| `123:` (unquoted, so an int key) | **404** | `/collections` lists `"id": 123` (a JSON number). Validation passes because `to_json` stringifies keys |
| `yes:` (unquoted, so bool `True`) | **404** for `/collections/yes` and `/collections/True` | YAML 1.1 bool key |

**GUI rule (proposed).**
- Ids should match `^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)*$`.
- Allow `/` only as an explicit "hierarchical" opt-in.
- Reject a last path segment equal to a sub-route: `items`, `schema`, `queryables`, `coverage`, `tiles`, `map`, `styles`, `position`, `area`, `cube`, `radius`, `trajectory`, `corridor`, `locations` or `instances` (the `/collections/<path:collection_id>` routes start at `pygeoapi/flask_app.py:234`).
- Always emit ids as YAML strings. ruamel quotes them automatically if the key is a `str`. When importing YAML, flag any non-`str` key.

## 3. Resources

### 3.1 `type` and required keys

**Claim:** `type` ∈ {`collection`, `stac-collection`, `process`}, and a collection requires `title`, `description`, `keywords`, `extents` and `providers`. **Verdict: correct for collection and stac-collection, but the schema can be bypassed.**

- `resources.<id>` is an `anyOf` of two branches (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:374):
  - **Branch 0**, "base resource object": `type` enum `collection` or `stac-collection` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:382, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:383). It requires `type`, `title`, `description`, `keywords`, `extents` and `providers` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:663; `- providers` pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:669). (read)
  - **Branch 1**, "process object" (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:671): `type` enum `process` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:677), `processor.name` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:678), `allow_internal_requests` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:689).
- **Schema bug:** the process branch's required list is spelled `requred:` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:693), so branch 1 has **no required keys**. (read) As a result, these all **pass** `validate_config` (executed):
  - a resource with no `type`
  - a resource that is `{}`
  - `{type: process}` with no `processor`
- The server does not tolerate a missing `type`. `filter_dict_by_key_value` does `v[key] == value` (`pygeoapi/util.py:571`) over every resource. `conformance` reads `value['type']` and then `value['providers']` (`pygeoapi/api/__init__.py:880`, `pygeoapi/api/__init__.py:884`). (read)
- With the docs' own "hidden resource" example, `foo: {title: …, visibility: hidden}` with no `type` (`docs/source/configuration.rst:413`), these return **500** (executed):
  - `/collections`
  - `/collections/obs`, a valid sibling collection
  - `/collections/foo`
  - `/conformance`
- `stac-collection` shares branch 0, so it also requires `providers` and `extents`. A stac-collection without `providers` failed validation. (executed)
- `providers: []` passes the schema, because there is no `minItems` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:513). (executed) At runtime (executed):
  - the collection vanishes from `/collections`
  - `/collections/{id}` returns 500 (`get_provider_default` does `providers[0]`, `pygeoapi/provider/__init__.py:86`)
  - `/items` returns 400 "Invalid provider type"
- `keywords: []` passes. Omitting `keywords` fails. (executed)

### 3.2 Keys of a collection or stac-collection resource

| key | schema | where the code reads it | notes |
|---|---|---|---|
| `type` | enum collection/stac-collection (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:378) | `v[key] == value` in `filter_dict_by_key_value` (`pygeoapi/util.py:571`) | See §3.1 |
| `visibility` | enum `default`, `hidden`, default `default` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:384, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:389) | `pygeoapi/api/__init__.py:947`, `pygeoapi/openapi.py:737` | `hidden` leaves the collection out of `/collections` and OpenAPI, but `/collections/{id}` and `/items` still work (executed) |
| `title`, `description` | `i18n_string` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:391, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:392) | `pygeoapi/api/collection.py:87` | §11 |
| `keywords` | `i18n_array` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:397) | `pygeoapi/api/collection.py:89` | §11 |
| `links[]` | object; required `type`, `rel`, `href` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:439, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:442) | `pygeoapi/api/collection.py:146` | `title` is effectively required (§3.3) |
| `extents` | required `spatial` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:508) | `pygeoapi/api/collection.py:92` | §3.4 |
| `limits` | same shape as `server.limits` via YAML merge `<<: *x-limits` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:511) | `pygeoapi/api/itemtypes.py:295` | §3.5 |
| `linked-data.context` | array of objects (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:400, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:404) | `pygeoapi/linked_data.py:194` | §10 |
| `formatters[]` | `{name (req), geom, attachment}` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:644, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:653, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:657) | `pygeoapi/util.py:779` | Resource-level, **not** provider-level. §12 |
| `providers[]` | §4 | `pygeoapi/api/collection.py:74` | §4, §9 |
| `templates` | **not in schema** | `pygeoapi/api/__init__.py:625` | Dataset-level HTML/JSON-LD template override. §10 |

The schema has no `additionalProperties` at resource level, so anything else is accepted silently (for example `foo: 1`, or a singular `template: x.j2`). (executed)

### 3.3 `links`

- **Schema:** items have `type`, `rel`, `title`, `href`, `hreflang` and `length` (integer). Only `type`, `rel` and `href` are required (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:427, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:436, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:439). (read)
- The docs say "pygeoapi does not enforce anything here, as long as the link has a `type`, `rel`, and `href` parameter" (`docs/source/configuration.rst:349`). (read)
- **The code disagrees.** `gen_collection` reads `link['title']` unconditionally (`pygeoapi/api/collection.py:150`), and so does the JSON-LD dataset builder (`pygeoapi/linked_data.py:164`). (read)
- With a link that has no `title` (executed):
  - `/collections` returns 200 **without** the collection. The exception is swallowed with a log warning (`pygeoapi/api/__init__.py:955`, `pygeoapi/api/__init__.py:956`).
  - `/collections/obs` returns **500** "Error generating collection" (`pygeoapi/api/__init__.py:962`).
  - `/items` still works.
- The code passes link `title`, `href` and `hreflang` through `l10n.translate` (`pygeoapi/api/collection.py:150`, `pygeoapi/api/collection.py:151`). The schema types them as plain `string`, so an i18n dict in a link `title` or `href` **fails** validation. (executed) This is a schema/code mismatch. Emit plain strings.
- `rel: enclosure` without `length`: pygeoapi sends a HEAD request **every time the collection is described**, and it drops the link if no `Content-Length` comes back (`pygeoapi/api/collection.py:158`, `pygeoapi/api/collection.py:160`; `docs/source/configuration.rst:395`). (read) The GUI should always write `length` for enclosures.

### 3.4 `extents`

- **`spatial.bbox`:**
  - An array of 4 to 6 numbers, `minItems: 4` / `maxItems: 6` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:451, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:456, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:457). `bbox` is the only required key of `spatial` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:462).
  - Neither the schema nor the code checks order or range. `[10,10,0,0]` and `[-500,-500,500,500]` both pass validation. (executed)
  - 3 or 7 values fail. (executed)
- **Nested bboxes.** The code accepts a *list* of bboxes: `if not isinstance(bbox[0], list): bbox = [bbox]` (`pygeoapi/api/collection.py:98`). The schema rejects `[[…]]` with "is not of type 'number'". (executed) This is a code/schema conflict, so emit a single bbox.
- **`spatial.crs`:**
  - The schema says `type: string`, default CRS84 (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:458, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:461). The default is **documentation only**: the code copies `crs` only if present (`pygeoapi/api/collection.py:107`). (read)
  - JSON-LD `spatial` is emitted only when `crs` ends with `CRS84` (`pygeoapi/linked_data.py:137`, `pygeoapi/linked_data.py:147`). If `crs` is omitted, the dataset JSON-LD gets `"spatial": null`. (read) **The GUI should always write `crs` explicitly.**
- **`temporal`:**
  - Keys: `begin` and `end` (`[string, null]`, `format: date-time`) (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:468, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:470, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:472), `trs` (default Gregorian, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:476), `resolution` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:480) and `default` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:483).
  - The collection document maps them to `interval`, `trs`, `grid.resolution` and `default` (`pygeoapi/api/collection.py:118`, `pygeoapi/api/collection.py:120`, `pygeoapi/api/collection.py:124`). (read)
- **Runtime requirements the schema doesn't check.** `validate_datetime` runs on every items, coverage, EDR and map request that has `datetime=` (`pygeoapi/api/itemtypes.py:321`). It indexes both `te['begin']` and `te['end']` and calls `.tzinfo` on them (`pygeoapi/api/__init__.py:1191`, `pygeoapi/api/__init__.py:1193`). (read) Executed:
  - `begin` as a quoted string, or a date-only value `2000-10-30`: validation passes, the collection renders, but `?datetime=` returns **400** `Configured times should be RFC3339` (`pygeoapi/api/__init__.py:1196`).
  - `temporal: {begin: <ts>}` with no `end` key: `?datetime=` returns **500** (KeyError).
  - An unquoted `2000-10-30T18:24:39Z` (PyYAML loads it as a `datetime`) with `end: null` works. An out-of-range query returns 400 "datetime parameter out of range".
  - The schema never sees the difference: `to_json` turns datetimes into ISO strings before validation (`pygeoapi/util.py:418`), and `format` isn't checked (§7). `begin: garbage` passes. (executed)
  - **GUI rule:** always write both keys, as unquoted `YYYY-MM-DDTHH:MM:SSZ` or `null`. With ruamel, emit a `datetime`, not a `str`.
- **Custom dimensions.** Any other key under `extents` (pattern `^(?!spatial$|temporal$).*`, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:487) must be an object with required `url` and `range` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:495, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:491), and optional `units` and `values` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:499, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:502). It is rendered as `definition`, `interval`, `unit` and `grid` (`pygeoapi/api/collection.py:133`, `pygeoapi/api/collection.py:136`). (read)
  - Because of this pattern, an unknown key under `extents`, such as `foo: {range: [0,1]}`, fails with "'url' is a required property". (executed)
  - Unknown keys under `spatial` and `temporal` are accepted. (executed)

### 3.5 `limits` (server and resource level)

- **Keys** (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:71):
  - `max_items`: integer, ≥1, default 10 (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:75)
  - `default_items`: integer, ≥1, default 10 (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:80)
  - `max_distance_x`, `max_distance_y`: number (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:85, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:88)
  - `max_distance_units`: UCUM string (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:91)
  - `on_exceed`: `error` or `throttle`, default `throttle` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:94, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:99, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:100)
- The resource level reuses the same definition through the YAML merge key (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:511). The deprecated `server.limit` integer still exists (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:67). (read)
- **Resolution:** `ChainMap(collection_limits, server_limits)`, so resource values override server values key by key. Code defaults are `default_items` 10, `max_items` 10 and `on_exceed` `throttle` (`pygeoapi/api/__init__.py:1305`, `pygeoapi/api/__init__.py:1307`, `pygeoapi/api/__init__.py:1308`, `pygeoapi/api/__init__.py:1309`). The items API passes `server.limits` and `resources.<id>.limits` (`pygeoapi/api/itemtypes.py:294`, `pygeoapi/api/itemtypes.py:295`). (read)
- The code default `max_items` is **10**, so with no `limits` configured a client can never get more than 10 items per page. The docs example uses 50/1000 (`docs/source/configuration.rst:554` and the server example above it). (read)
- Validation (executed): `on_exceed: bogus` fails, `max_items: 0` fails, and unknown keys under `limits` pass.

## 4. Providers: what the schema declares

**Claim:** provider `type` ∈ {feature, coverage, record, map, tile, edr, stac}, and `type`, `name`, `data` are required. **Verdict: correct** (enum pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:522; `- feature` pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:523 … `- stac` pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:529; required pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:640, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:643). (read)

The docs prose lists only `feature`, `coverage` and `tile` (`docs/source/configuration.rst:205`). The example comment lists five (`docs/source/configuration.rst:255`). (read)

Every property the schema declares under `providers.items` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:513 to the `required` list at pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:640):

| key | type / constraints | default (schema) | cite |
|---|---|---|---|
| `type` | string enum feature, coverage, record, map, tile, edr, stac. **Required** | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:519 |
| `default` | boolean | (first provider is default) | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:530 |
| `name` | string (short name or dotted import path). **Required** | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:535 |
| `data` | `anyOf` string or object. **Required**. An int or list fails (executed) | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:540 |
| `editable` | boolean | false | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:545 |
| `count` | boolean | true | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:549 |
| `table` | string | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:553 |
| `id_field` | string | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:556 |
| `geometry` | object `{x_field, y_field}`, both required | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:559, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:563, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:566 |
| `time_field` | string | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:572 |
| `title_field` | string. An i18n dict **fails** (executed), but the docs allow it (§11) | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:575 |
| `format` | object `{name, mimetype}`, both required | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:578, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:585 |
| `options` | object, `oneOf: [$ref PostgreSQL options]` | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:591, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595 |
| `properties` | array of strings, `minItems: 1`, `uniqueItems` | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:601, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:606, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:607 |
| `crs` | array of strings (`format: uri`), `uniqueItems` | `[CRS84]` | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:608, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:615 |
| `storage_crs` | string (`format: uri`) | CRS84 | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:619, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:621 |
| `storage_crs_coordinate_epoch` | number | — | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:627 |
| `include_extra_query_parameters` | boolean | false | pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:636 |

There is no `additionalProperties`, so every other key (`geom_field`, `uri_field`, `always_xy`, …) is accepted untyped. `uri_field: 5` and `always_xy: nope` pass. (executed)

**Surprise: `options`.**
- The only `oneOf` alternative is `#/definitions/provider/properties/PostgreSQL/properties/config/properties/options` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595). That definition types `connect_timeout`, `tcp_user_timeout`, `keepalives*`, `pool_size`, `max_overflow`, `pool_recycle` and `pool_timeout` as integer, and `pool_pre_ping` as boolean (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:725, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:749).
- These types are enforced for **every** provider, not just PostgreSQL. `options: {pool_size: "x"}` on a CSV provider fails. `options: {connect_timeout: "10"}` fails. (executed)
- Arbitrary other option keys pass. (executed)

**Claimed common optional keys: verification.**

| claimed key | in schema? | read by core code | verdict |
|---|---|---|---|
| `default` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:530) | `pygeoapi/provider/__init__.py:81` | ✓ |
| `editable` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:545) | `pygeoapi/provider/base.py:69`, `pygeoapi/api/itemtypes.py:788` | ✓ |
| `count` | yes, boolean (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:549) | `str2bool(...)` (`pygeoapi/provider/base.py:70`) | ✓. The code accepts `"false"`, the schema doesn't (executed) |
| `table` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:553) | provider-specific | ✓ |
| `id_field` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:556) | `pygeoapi/provider/base.py:72` | ✓ |
| `title_field` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:575) | `pygeoapi/provider/base.py:78` | ✓ (string only in schema) |
| `uri_field` | **no** | `pygeoapi/provider/base.py:73`; JSON-LD (§10) | ✗ not in schema; documented at `docs/source/configuration.rst:261` |
| `time_field` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:572) | `pygeoapi/provider/base.py:77` | ✓ |
| `x_field` / `y_field` / `z_field` (top level) | **no** (only `geometry.x_field`/`y_field`) | `pygeoapi/provider/base.py:74`, `pygeoapi/provider/base.py:75`, `pygeoapi/provider/base.py:76` | ✗ not in schema; used by coverage/EDR providers, e.g. `tests/pygeoapi-test-config.yml:213` |
| `properties` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:601) | `pygeoapi/provider/base.py:79` | ✓ |
| `format {name, mimetype}` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:578) | `pygeoapi/api/collection.py:79` | ✓ |
| `crs` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:608) | `pygeoapi/crs.py:111` | ✓ |
| `storage_crs` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:619) | `pygeoapi/provider/base.py:86`, `pygeoapi/crs.py:557` | ✓ |
| `storage_crs_coordinate_epoch` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:627) | `pygeoapi/api/collection.py:276` | ✓ |
| `always_xy` | **no** | `pygeoapi/crs.py:556` | ✗ not in schema; documented at `docs/source/configuration.rst:288` |
| `options` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:591) | `pygeoapi/provider/base.py:71` | ✓ (PostgreSQL types enforced, see above) |
| `include_extra_query_parameters` | yes (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:636) | `pygeoapi/provider/base.py:81` | ✓ |
| `validator` | **no** | **not found** anywhere in `pygeoapi/`, `docs/` or `tests/` (grep for `validator` hits only pydantic/Django internals) | ✗ does not exist in 0.24.0 |
| `formatters` | not at provider level | resource-level (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:644, `pygeoapi/util.py:779`) | ✗ misplaced in the claim: it's a **resource** key |

Keys the base class reads that were missing from the claim (none are in the schema):
- `file_types` (`pygeoapi/provider/base.py:80`)
- provider `languages` / `language` (`pygeoapi/l10n.py:473`; docs `docs/source/language.rst:232`)

`BaseProvider` raises `RuntimeError('name/type/data are required')` if any of the three is missing (`pygeoapi/provider/base.py:67`). (read)

**CSV geometry:** `geometry: {x_field, y_field}` is in the schema (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:559) and in the test config (`tests/pygeoapi-test-config.yml:158`). (read)

**`crs` type.** `crs: 4326`, as in the ESRI docs example, fails the schema ("4326 is not of type 'array'"). (executed) If it gets past validation, `get_supported_crs_list` iterates it (`pygeoapi/crs.py:111`), and the result is (executed):
- `/collections` drops the collection
- `/collections/{id}` returns 500
- `/items` returns 500

`format: uri` is not enforced, so `crs: [EPSG:4326]` and `storage_crs: EPSG:4326` pass the schema. (executed) Whether they work is up to pyproj at request time.

## 5. `additionalProperties` at every level

**Claim:** the schema doesn't forbid extra provider keys. **Verdict: correct, and it forbids extra keys nowhere.**
- The schema has no `additionalProperties` keyword at all. (read: full 756-line file; a grep follows in §15)
- Extra keys were accepted at every level I tried: top level, `server`, `logging`, `metadata`, `metadata.identification`, resource, provider, `extents.spatial`, `extents.temporal`, link item, formatter item, `linked-data` and `limits`. (executed)
- The only "closed" place is the set of extra keys directly under `extents`, which must be custom-dimension objects (§3.4).

Consequences:
- A typo such as `titel:` or `id-field:` is silently ignored.
- The provider descriptors must do their own unknown-key detection, for example "warn: `id-field` is not a known key for CSV; did you mean `id_field`?".
- Provider-specific keys (`geom_field`, the fields inside `data`, most `options`) have no schema. That confirms the need for descriptors.

## 6. Environment variables in `yaml_load`

**Claim:** `yaml_load` replaces `${VAR}` and `${VAR:-default}`, and an undefined variable with no default raises. **Verdict: correct.** The mechanics:

- **Regex:** `r'.*?\$\{(?P<varname>\w+)(:-(?P<default>[^}]*))?\}'` (`pygeoapi/util.py:143`). (read)
- **Mechanism:** a PyYAML **implicit resolver** plus constructor on a `SafeLoader` subclass. `EnvVarLoader.add_implicit_resolver('!env', env_matcher, None)` (`pygeoapi/util.py:167`, `pygeoapi/util.py:170`). (read)
  - Substitution happens **after YAML parsing, per scalar node**. It is not a text-level pre-pass.
  - PyYAML consults implicit resolvers only for **plain** scalars. That's why quoted and block scalars aren't substituted (see the table).
- Each match appends the literal text before `${`, then the env value or the default. It raises `EnvironmentError`, an alias of `OSError`, with the message `Could not find the 'NAME' environment variable` (`pygeoapi/util.py:158`, `pygeoapi/util.py:159`). (read) The pygeoapi test suite asserts the same behaviour (`tests/other/test_config.py:73`). (read)
- **Typing:** the **whole resulting string**, literal parts included, goes through `get_typed_value` (`pygeoapi/util.py:165`):
  - if it contains `.`, it tries `value2 = float(value)` (`pygeoapi/util.py:117`)
  - a leading `0` with length > 1 keeps it a `str`, via `value.startswith('0')` (`pygeoapi/util.py:118`)
  - `true` / `false` (any case) become `bool`, via `value.lower() in ['true', 'false']` (`pygeoapi/util.py:120`)
  - otherwise it tries `value2 = int(value)` (`pygeoapi/util.py:123`), and on `except ValueError` falls back to `str` (`pygeoapi/util.py:124`)

Executed matrix (`envtest/t_env.py`):

| YAML | env | result |
|---|---|---|
| `port: ${T_PORT}` | `5000` | `5000` (**int**) |
| `v: ${T_V}` | `1.10` | `1.1` (**float**; a version string gets mangled) |
| `zip: ${T_Z}` | `02134` | `'02134'` (str, leading zero) |
| `b: ${T_B}` | `True` | `True` (bool); `yes` stays `'yes'` |
| `pw: ${T_PW}` | `123456` | `123456` (**int**; numeric passwords become ints) |
| `h: ${T_H}` | `127.0.0.1` | `'127.0.0.1'` (str) |
| `e: ${T_E}` | `''` (set, empty) | `''`. A default is used only when the variable is **unset**: `${T_P:-dflt}` with `T_P=''` gives `''` |
| `port: ${T_PORT:-5000}` | unset | `5000` (int). `${T_P:-}` gives `''`; `${T_T:-the default}` gives `'the default'` |
| `data: ${T_H}:${T_PORT}` | db, 5432 | `'db:5432'` (multiple variables OK) |
| `url: postgresql://${T_U}:${T_PW}@${T_H}/x` | u, p, h | `'postgresql://u:p@h/x'` |
| `x: 1${T_A}` | `2` | `12` (int, typed after concatenation) |
| `x: ${T_A}.${T_B}` | 1, 5 | `1.5` (float) |
| `${T_K}: 1` and `res_${T_K}: 1` | mykey | keys `mykey` and `res_mykey` (**keys are substituted**) |
| `x: ${T_MISSING}` | unset | `OSError: Could not find the 'T_MISSING' environment variable` (also when it's the 2nd of two vars) |
| `x: $T_A` | a | `'$T_A'` (**no braces, no substitution**) |
| `x: "${T_A}"` / `x: '${T_A}'` | a | `'${T_A}'` (**quoted: NOT substituted**) |
| `x: \|` / `x: >` block scalar | a | `'${T_A}\n'` (**not substituted**) |
| `x: [${T_A}, ${T_B}]` | a, b | **ParserError** (`{` is a flow indicator) |
| `x:\n - ${T_A}` (block sequence) | a | `['a']` |
| `x: !!str ${T_A}` | 5 | `'${T_A}'` (explicit tag disables it) |
| `${T-A}`, `${T.A}` | — | left literally (`\w+` only) |
| `${T_A:-${T_B}}` | T_B=b | `'${T_B}'` (no nesting) |
| env value `[1, 2]`, `a: b`, `null`, `~` | — | kept as strings (env values are **not** YAML-parsed) |
| env value `1_000`, ` 5 `, `-5`, `1.5e3` | — | `1000`, `5`, `-5`, `1500.0`; `1e5` and `0x1F` stay strings |

Where `yaml_load` is used (read):
- `get_config()` calls `yaml_load(fh)` (server startup): `pygeoapi/config.py:60`
- `pygeoapi config validate`: `pygeoapi/config.py:105`
- `pygeoapi openapi generate`, via `s = yaml_load(cf)`: `pygeoapi/openapi.py:1043`
- `pygeoapi openapi validate`: `pygeoapi/openapi.py:1130`
- Loading the schema itself: `pygeoapi/config.py:71`

So all these tools **need every referenced variable set, or a default**. `get_config(raw=True)` uses plain `yaml.safe_load` and keeps placeholders (`pygeoapi/config.py:58`). The Admin API uses it for writes (`pygeoapi/api/admin.py:132`). Example of a typed default in the shipped Docker config: `admin: ${PYGEOAPI_SERVER_ADMIN:-false}` (`docker/default.config.yml:48`). (read)

**GUI rules:**
1. Emit placeholders only as plain block-style scalars.
2. Never inside flow collections.
3. Never quoted.
4. Never in `|` or `>` blocks.
5. Remember the value is re-typed. `port: ${PORT}` becomes an int, which is right for `server.bind.port` (schema `type: integer`, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:19). But a numeric-looking password or version string also becomes int or float.
6. The runtime package's resolver (§6 of PROMPT.md) must copy these exact semantics: plain scalars only, `\w+` names, default only when unset, whole-string `get_typed_value`, `OSError` on missing.

## 7. `validate_config` and `pygeoapi config validate`

- **Signature:** `validate_config(instance_dict: dict) -> bool` (`pygeoapi/config.py:74`). It returns `True` (`pygeoapi/config.py:85`) or raises. (read)
- **Body:** `jsonschema_validate(json.loads(to_json(instance_dict)), load_schema())` (`pygeoapi/config.py:83`, where `jsonschema_validate` is `jsonschema.validate`, `pygeoapi/config.py:34`). (read)
  - It raises `jsonschema.exceptions.ValidationError`: only the **single best-match** error, chosen by `jsonschema.exceptions.best_match`. (executed)
  - jsonschema's `validate` first checks the schema against the 2020-12 metaschema, which passes. The validator class is `Draft202012Validator`, picked from `$schema` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:1). (executed)
- **No format checking.** No `format_checker` is passed, so `format: uri` and `format: date-time` are annotations only. `begin: garbage`, `crs: [EPSG:4326]` and `pubsub.broker.url: not a uri` all pass. (executed)
- **Env vars:** `validate_config` takes an already-loaded dict and does **not** resolve variables. A dict loaded with `yaml.safe_load` validates `${VAR}` strings as strings, so an integer field such as `server.bind.port: ${PORT}` fails ("is not of type 'integer'"). (executed: `server bind port string` → FAIL) The CLI loads with `yaml_load` (`pygeoapi/config.py:105`), so it resolves variables, and an unset variable aborts with an `OSError` traceback. (read)
- **Normalisation before validation:** `to_json` serialises `datetime`/`date` to ISO strings (`pygeoapi/util.py:418`) and escapes `<`/`>` as `&lt;`/`&gt;` (`pygeoapi/util.py:271`). Non-string keys are stringified by `json.dumps`, which is why int ids validate (§2). (read)
- **CLI:** `pygeoapi config validate -c FILE` (`pygeoapi/config.py:96`).
  - On success it prints `Validating FILE` then `Valid configuration`, and exits 0 (`pygeoapi/config.py:107`).
  - On failure it prints the **full Python traceback**, ending in the `ValidationError` text, and exits **1**.
  - Without `-c` it prints a `ClickException` (`pygeoapi/config.py:101`). (executed)
- `validate_config` is called only by the CLI and the Admin API (`pygeoapi/api/admin.py:102`, `pygeoapi/api/admin.py:128`). It is **not** called at server startup (§14). (read)

Executed error output. This is the tail of the CLI traceback; the Python `e.message` is the first line.

**(a) Missing required key** (`title` removed from a collection). The message is **misleading**:
```
jsonschema.exceptions.ValidationError: 'collection' is not one of ['process']

Failed validating 'enum' in schema[1]['properties']['type']:
    {'type': 'string', 'description': 'resource type', 'enum': ['process']}

On instance['type']:
    'collection'
```
- `best_match` picks the process branch of the resource `anyOf`. The same message appears for a missing `providers`, a missing `keywords` and `visibility: private`. (executed)
- The real cause is available in the `anyOf` error's `.context`: branch 0 reports `'title' is a required property` at `resources/obs`. (executed with `Draft202012Validator(schema).iter_errors(...)`)

**(b) Wrong provider type enum** (`type: features`):
```
jsonschema.exceptions.ValidationError: 'features' is not one of ['feature', 'coverage', 'record', 'map', 'tile', 'edr', 'stac']

Failed validating 'enum' in schema[0]['properties']['providers']['items']['properties']['type']:
    ...
On instance['providers'][0]['type']:
    'features'
```
`e.absolute_path` = `['resources','obs','providers',0,'type']`. The "On instance[...]" path in the text is *relative to the resource*. (executed)

**(c) Bad bbox:**

| bad value | message |
|---|---|
| `[-180,-90,180]` | `[-180, -90, 180] is too short` (validator `minItems`) |
| 7 values | `... is too long` |
| `'-180,-90,180,90'` | `'-180,-90,180,90' is not of type 'array'` |
| `[[...]]` | `[-180, -90, 180, 90] is not of type 'number'` at `.../bbox/0` |

(executed)

**GUI recommendation.** Don't show `str(ValidationError)`. Instead:
1. Run `Draft202012Validator(load_schema()).iter_errors(json.loads(to_json(cfg)))`.
2. For each `anyOf` error at `resources/<id>`, choose the branch by the resource's `type`: `process` → branch 1, anything else → branch 0.
3. Surface that branch's `context` errors, with `absolute_path` mapped to form fields.
4. Add a local format check for `uri` and `date-time`, since pygeoapi doesn't enforce them.

## 8. `pygeoapi openapi generate` and `pygeoapi openapi validate`

**Claim:** by default (`--fail-on-invalid-collection`), generate loads the providers, so it can double as a smoke test. **Verdict: mostly true, with three caveats.** It skips hidden collections. It fails on metadata keys the schema doesn't require. With `--no-fail…`, one bad collection drops the paths of *every* collection served by the same API module.

**Syntax** (`--help` executed):
```
pygeoapi openapi generate [OPTIONS] CONFIG_FILE
  -fic, --fail-on-invalid-collection / --no-fail-on-invalid-collection
  -f, --format [json|yaml]
  -of, --output-file FILENAME
```
- `CONFIG_FILE` is **positional**, `@click.argument('config_file', …)`, not `-c` (`pygeoapi/openapi.py:1095`).
- The flag defaults to **on** (`pygeoapi/openapi.py:1096`, `pygeoapi/openapi.py:1097`). **`--no-fail-on-invalid-collection` turns it off.** `-fic` is only the short form of the *on* flag.
- The default format is YAML (`pygeoapi/openapi.py:1098`). Without `-of`, the document goes to stdout (`pygeoapi/openapi.py:1100`).

**What it does** (read):
- `yaml_load` (so env vars must resolve), then `get_oas_30` (`pygeoapi/openapi.py:1043`, `pygeoapi/openapi.py:240`). It **never calls `validate_config`**.
- Each API module's `get_oas_30` runs inside one `try`. On error it re-raises if the flag is on; otherwise it logs `Resource not added to OpenAPI: <err>` and skips **that whole module's paths** (`pygeoapi/openapi.py:554`, `pygeoapi/openapi.py:562`, `pygeoapi/openapi.py:563`, `pygeoapi/openapi.py:566`).
- The items module calls `load_plugin` for every **visible** feature/record provider, to read `p.fields` (`pygeoapi/api/itemtypes.py:1094`). Per collection it catches only `ProviderTypeError` (`pygeoapi/api/itemtypes.py:1394`). Collections with `visibility: hidden` are excluded up front (`pygeoapi/openapi.py:737`).
- It indexes `metadata.identification.terms_of_service` and `metadata.license.url` directly (`pygeoapi/openapi.py:272`, `pygeoapi/openapi.py:276`), plus `metadata.provider.name` and `metadata.contact.name` (`pygeoapi/openapi.py:152`, `pygeoapi/openapi.py:161`).

**Executed** (scratch `g_*.yml` configs, copies of `tests/data/obs.csv` and `ne_110m_lakes.geojson`):

| config | default (flag on) | `--no-fail-on-invalid-collection` |
|---|---|---|
| Schema-valid minimal config **without** `terms_of_service` / `license.url` | **exit 1**, `KeyError: 'terms_of_service'` (the schema declares both as optional: `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:281`, `pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:299`) | exit 1, same error. This happens before the per-module `try` |
| Valid config (both keys added) | exit 0, `Generating oas_base.yml` / `Done`. Paths `/collections/obs`, `/items`, `/items/{featureId}`, `/queryables`, `/schema` | — |
| CSV `data` points at a missing file | **exit 1**, traceback ending `FileNotFoundError: [Errno 2] No such file or directory: 'data/nosuch.csv'` | exit 0. stderr `Resource not added to OpenAPI: [Errno 2] No such file or directory: 'data/nosuch.csv'`. Only `/collections/obs` is left |
| Unknown short name `NoSuchPlugin` | **exit 1**, `pygeoapi.plugin.InvalidPluginError: Plugin NoSuchPlugin not found`, preceded by a spurious `NoneType: None` from `LOGGER.exception` (`pygeoapi/plugin.py:120`) | exit 0, `Resource not added to OpenAPI: Plugin NoSuchPlugin not found` |
| Unimportable dotted path `nosuchpkg.provider.Foo` | **exit 1**, `ModuleNotFoundError: No module named 'nosuchpkg'` | exit 0, `Resource not added to OpenAPI: No module named 'nosuchpkg'` |
| Healthy `lakes` (GeoJSON) **plus** broken `obs` (missing CSV) | exit 1 | exit 0, but **`lakes` also loses `/items`, `/queryables` and `/schema`**. Only `/collections/lakes` and `/collections/obs` remain |
| Broken CSV collection with `visibility: hidden` | **exit 0**. The hidden collection isn't loaded, so it isn't checked | exit 0 |

- On failure, the `--output-file` target is **not truncated**: a pre-existing sentinel file survived every failing run. click opens `'w'` files lazily. (executed)
- The error output is a raw Python traceback. The last line holds the exception class and message.

**`pygeoapi openapi validate OPENAPI_FILE`** (positional, `pygeoapi/openapi.py:1122`):
- Loads the file with `yaml_load` and runs `jsonschema.validate` against `pygeoapi/resources/schemas/openapi/openapi-3.0.x.json` (`pygeoapi/openapi.py:1014`, `pygeoapi/openapi.py:1019`).
- On success it prints `Validating <_io.TextIOWrapper name='oas_base.yml' …>` (the file object's repr) and `Valid OpenAPI document`, and exits 0.
- On an invalid document it prints a traceback ending `jsonschema.exceptions.ValidationError: 'title' is a required property … On instance['info']`, and exits 1. (executed)
- The missing-argument message still says `--openapi/-o required` (`pygeoapi/openapi.py:1127`), but that branch is unreachable because click requires the argument. (read)

**Harness implications:**
1. Treat "generate OK" as necessary but not sufficient. It doesn't check hidden collections. It checks only construction plus `fields` for feature/record providers.
2. Always run generate with the flag **on**, against a temp file.
3. Pre-check `terms_of_service`, `license.url` and `server.languages` before running it.
4. Parse the last traceback line into a plain-language message.
5. Don't publish OpenAPI produced with `--no-fail…`: it silently drops healthy collections' paths.

## 9. Several providers in one collection

Selection helpers (read):
- `get_provider_by_type(providers, type)` returns the **first** provider whose `type` matches, and raises `ProviderTypeError('Invalid provider type requested')` if there's none (`pygeoapi/provider/__init__.py:51`, `pygeoapi/provider/__init__.py:64`, `pygeoapi/provider/__init__.py:66`). It ignores `default`.
- `get_provider_default(providers)` returns the first provider with a truthy `default`, else `providers[0]` (`pygeoapi/provider/__init__.py:71`, `pygeoapi/provider/__init__.py:81`, `pygeoapi/provider/__init__.py:86`).
- `filter_providers_by_type(providers, type)` builds `{type: provider}`, so the **last** provider of a type wins (`pygeoapi/provider/__init__.py:47`). It's used only for existence checks and in some OpenAPI and landing-page code.

Who uses what (read):
- **Collection document** (`gen_collection`): the *default* provider, from `get_provider_default`, sets the primary data type. That decides `itemType`, items vs coverage links, schema/queryables links, and `crs`/`storageCrs` (`pygeoapi/api/collection.py:74`, `pygeoapi/api/collection.py:246`, `pygeoapi/api/collection.py:274`). It then *loads* the first `coverage` (only when the default is coverage), `tile`, `map` and `edr` providers to add their links (`pygeoapi/api/collection.py:304`, `pygeoapi/api/collection.py:333`, `pygeoapi/api/collection.py:354`, `pygeoapi/api/collection.py:397`).
- **Items / item / transactions:** the first `feature` provider, else the first `record` (`pygeoapi/api/itemtypes.py:337`, `pygeoapi/api/itemtypes.py:343`).
- **Queryables:** first `feature`, else `coverage`, else `record` (`pygeoapi/api/itemtypes.py:127`).
- **Schema:** first `feature` → `coverage` → `record` → `edr` (`pygeoapi/api/__init__.py:1041`).
- **Items OpenAPI:** checks `feature` and then `record` with `filter_providers_by_type`, so if both exist, `record` wins in OpenAPI (`pygeoapi/api/itemtypes.py:1087`, `pygeoapi/api/itemtypes.py:1090`). At runtime `feature` wins. This is an inconsistency.
- **Tiles:** first `tile` (`pygeoapi/api/tiles.py:96`).
- **Maps:** first `map` (`pygeoapi/api/maps.py:90`).
- **Coverage:** first `coverage` (`pygeoapi/api/coverages.py:96`).
- **EDR:** first `edr` (`pygeoapi/api/environmental_data_retrieval.py:108`, `pygeoapi/api/environmental_data_retrieval.py:279`).
- **STAC:** first `stac` (`pygeoapi/api/stac.py:105`, `pygeoapi/api/stac.py:164`).

All of these use `get_provider_by_type` (read).

Executed:
- **Two `feature` providers**, CSV first and GeoJSON second, with or without `default: true` on the second: `/items` and `/queryables` serve the **CSV** (first) provider in both cases. The second feature provider is unreachable.
- **Broken `record` provider first, CSV `feature` second:** the collection advertises `itemType: record` with `ogc-catalog` links, because the default is the first provider, but `/items` serves the CSV.
- **`tile` provider first with no `format`:** `/collections` drops the collection and `/collections/obs` returns 500. `is_vector_tile` indexes `collection_data_format['name']` when the default is a tile (`pygeoapi/api/collection.py:82`, `pygeoapi/api/collection.py:83`). `/items` keeps working.

**Rules for J3 ("add tiles to a feature collection"):**
1. Allow at most one provider per `type`. Extra ones are dead config.
2. Any mix of distinct types may coexist. Core configs combine `feature` + `tile` (`tests/pygeoapi-test-config.yml:244`, `tests/pygeoapi-test-config.yml:252`).
3. Keep the data provider (feature, record, coverage or edr) as the default: either first in the list, or marked `default: true`. Never make a `tile` or `map` provider the default, because the collection is then described as tile-only or map-only.
4. Require `format: {name, mimetype}` on `tile` and `map` providers. Map links read `map_['format']['mimetype']` (`pygeoapi/api/collection.py:362`).
5. Remember that adding a tile, map, edr or coverage provider makes **every** `/collections` request instantiate it with `load_plugin` (`pygeoapi/api/collection.py:333`). A slow or broken one hides the collection (the exception is swallowed at `pygeoapi/api/__init__.py:955`).

## 10. Linked data and templates

- **`linked-data.context`:** an array of objects whose values are strings or objects (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:404). A bare string item or an object-valued `context` fails. (executed) At items/item JSON-LD time it is appended after pygeoapi's default vocabulary: `'@context': [defaultVocabulary, *context]` (`pygeoapi/linked_data.py:195`, `pygeoapi/linked_data.py:248`). (read)
- **`uri_field` and `@id`** (read):
  - **Single item:** `uri = properties[uri_field]` if `uri_field` is set, else `{collections}/{id}/items/{identifier}` (`pygeoapi/api/itemtypes.py:949`). It then sets `data['@id'] = identifier` with **no URL check** (`pygeoapi/linked_data.py:212`).
  - **Items list:** `geojson2jsonld(..., id_field=(p.uri_field or 'id'))` (`pygeoapi/api/itemtypes.py:723`). Each feature's `@id` is `feature[id_field]` or `properties[id_field]`, used **only if `is_url`**, else `{collection}/items/{feature.id}` (`pygeoapi/linked_data.py:226`, `pygeoapi/linked_data.py:228`, `pygeoapi/linked_data.py:229`).
  - Without `uri_field`, a feature whose `id` is itself a URL gets that URL as `@id`.
- **Executed:**
  - `uri_field: nosuchfield`: `/items/371?f=jsonld` returns **500**. `uri` is `None`, so `geojson2jsonld` takes the collection branch. `/items?f=jsonld` returns 200 with fallback `@id`s.
  - `uri_field: stn_id` (not a URL): the single item gets `@id: 35`, and the items list falls back to item URLs.
  - **Geoconnex check for the GUI:** every sampled value of `uri_field` must be present and be an absolute URL.
- **Dataset-level `templates`:** read by `API.get_dataset_templates()` as `resources.<id>.templates`, falling back to `server.templates` (`pygeoapi/api/__init__.py:625`, `pygeoapi/api/__init__.py:627`). (read)
  - Used for the collection HTML page, schema, items and item HTML, and the JSON-LD templates `collections/items/item.jsonld` and `collections/items/index.jsonld`, via `get_dataset_templates` (`pygeoapi/linked_data.py:196`, `pygeoapi/linked_data.py:257`, `pygeoapi/linked_data.py:264`). Docs: `docs/source/configuration.rst:696`.
  - **Only `path` is honoured.** `render_j2_template` builds the search path `[templates['path'], TEMPLATESDIR, '.']` (`pygeoapi/util.py:476`, `pygeoapi/util.py:482`, `pygeoapi/util.py:483`), so a custom directory may override **individual** templates (e.g. just `collections/items/item.jsonld`) and fall back to the packaged ones for the rest.
  - `static` is read only from `server.templates` (`pygeoapi/flask_app.py:67`, `pygeoapi/starlette_app.py:85`). A dataset-level `static` is ignored. (read)
  - A dataset-level `templates: /some/path` **string** passes the schema (not declared there; executed). At render time `tpl_config['path']` raises `TypeError`, which is caught, and the default templates are used silently (`pygeoapi/util.py:485`). (read)
  - **GUI:** always write `templates: {path: /abs/dir}`.
- **Singular `template:` (the resource-level key used by pygeoapi-geoconnex-examples):** **not read anywhere in core 0.24.0.** A grep for `['template']`, `get('template'`, `'template':` and `item_template` over `pygeoapi/**/*.py` found no config reads. It passes validation only because extra keys are allowed (executed). Any effect it has must come from a plugin or custom templates, not from core. The core mechanism is `templates.path` plus overridden `collections/items/item.jsonld` / `index.jsonld`.
- **`linked-data.item_template` or similar:** not found. `linked-data` has only `context` in the schema (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:404) and in code (`pygeoapi/linked_data.py:195`).

## 11. i18n

- `i18n_string` is `oneOf: [string, object with patternProperties ^[a-zA-Z]{2,3}([-_][a-zA-Z0-9]{2,3})?$ → string]`. `i18n_array` is the same with arrays of strings (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:697, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:702, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:705). (read)
- Keys that **don't** match the pattern are *not* validated (no `additionalProperties`). `title: {english: a}` passes. (executed)
- **Schema-level i18n keys** (read): `metadata.identification.title`, `description`, `keywords`, `terms_of_service`; `metadata.license.name` and `url`; `metadata.provider.name` and `url`; resource `title`, `description`, `keywords`.
- Everything else is a plain `string` in the schema, including `metadata.contact.*`. The repo's own `tests/pygeoapi-test-config-i18n.yml` uses an i18n `contact.url` (`tests/pygeoapi-test-config-i18n.yml:124`) and **fails** `validate_config`. (executed) Link `title`, `href` and `hreflang` and provider `title_field` are translated by code (`pygeoapi/api/collection.py:150`, `pygeoapi/api/itemtypes.py:680`) but typed `string` by the schema, so i18n dicts there fail validation (executed). The docs explicitly show an i18n `title_field` (`docs/source/language.rst:160`). This is a doc/code/schema conflict.

At runtime an i18n `title_field` **works**: `{en: datetime, fr: value}` rendered the item HTML page. (executed) So `validate_config` rejects a config that the server serves fine.
- **Resolution:** `l10n.translate(value, locale)` (`pygeoapi/l10n.py:214`, read):
  1. It tries the exact key first (`pygeoapi/l10n.py:252`).
  2. If not **all** keys are valid locales, it returns the dict unchanged (`pygeoapi/l10n.py:261`; warning in `docs/source/language.rst:169`).
  3. Otherwise it picks the best match (`pygeoapi/l10n.py:266`). When nothing matches, that's the first locale in the dict (`pygeoapi/l10n.py:207`).
- **Server languages:**
  - `get_locales` reads `server.languages`, falling back to `server.language` (a string is accepted) (`pygeoapi/l10n.py:437`). The first entry is the default (`pygeoapi/api/__init__.py:538`).
  - It raises `LocaleError('No languages have been configured')` if both are missing, and `Bad value in supported server language(s)` for an unknown tag (`pygeoapi/l10n.py:444`, `pygeoapi/l10n.py:450`).
  - The schema requires neither (server `required` is `bind`, `url`, `mimetype`, `encoding`, `map`: pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:164, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:169), but the docs say a `language` or `languages` property "must" exist (`docs/source/language.rst:66`).
  - Executed: without `languages`, importing the Flask app fails with `pygeoapi.l10n.LocaleError: No languages have been configured`. With `languages: [klingon]`, it fails with `Bad value in supported server language(s)`. A singular `language: fr-CA` works. Both configs pass `validate_config`.
- **Provider languages:** a provider's `languages`/`language` list is read by `get_plugin_locale` (`pygeoapi/l10n.py:453`, `pygeoapi/l10n.py:473`) for feature/record queries and items (`pygeoapi/api/itemtypes.py:535`). (read)

## 12. Plugin kinds: formatter, pubsub, "validator"

- The registry categories in `PLUGINS` (`pygeoapi/plugin.py:39`) are exactly: `provider`, `formatter`, `process`, `process_manager` and `pubsub` (`pygeoapi/plugin.py:75`, `pygeoapi/plugin.py:78`, `pygeoapi/plugin.py:83`, `pygeoapi/plugin.py:89`). (read) **There is no `validator` category, and the word does not occur in any 0.24.0 config code, docs or tests.** (executed grep)
- `load_plugin(type, def)` (`pygeoapi/plugin.py:97`):
  - A name with no dot that isn't a registered short name raises `InvalidPluginError('Plugin X not found')` (`pygeoapi/plugin.py:118`, `pygeoapi/plugin.py:119`).
  - A dotted name is split with `name.rsplit('.', 1)`, imported with `importlib.import_module`, and `class_(plugin_def)` is instantiated (`pygeoapi/plugin.py:123`, `pygeoapi/plugin.py:131`, `pygeoapi/plugin.py:133`).
  - An unimportable dotted path raises `ModuleNotFoundError` or `AttributeError`, **not** `InvalidPluginError`. (read; executed in §8)
- **Formatters:**
  - Resource-level `formatters: [{name, geom, attachment}]` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:644).
  - `get_dataset_formatters` always adds the core `CSV` formatter for feature/record collections, then loads each entry of `dataset.get('formatters', [])` by name (`pygeoapi/util.py:771`, `pygeoapi/util.py:779`). Only `CSV` is registered (`pygeoapi/plugin.py:76`).
  - The docs example uses a dotted `name: path.to.formatter` (`docs/source/configuration.rst:289`). (read)
- **pubsub:**
  - Top-level `pubsub: {name, broker: {url (req, format uri), channel, hidden}}` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:170, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:177, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:181, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:185, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:188).
  - Loaded once at API start with `load_plugin('pubsub', config['pubsub'])` (`pygeoapi/api/__init__.py:558`, `pygeoapi/api/__init__.py:560`).
  - Short names are `HTTP`, `Kafka` and `MQTT` (`pygeoapi/plugin.py:90`, `pygeoapi/plugin.py:92`). (read)
- **process_manager:** `server.manager: {name, connection (string or object), output_dir}`, all required (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:127, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:134, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:139). (read)

## 13. `server` keys (read-only in the v1 UI)

**Schema-declared:**
- `bind.{host,port}`, required (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:12, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:16, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:19)
- `url` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:25), `icon`, `logo` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:28, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:31)
- `admin` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:34), `mimetype` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:38), `encoding` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:41), `gzip` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:44)
- `language` / `languages` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:47, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:50), `locale_dir` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:56)
- `cors` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:59), `pretty_print` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:63)
- `limit` (deprecated, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:67), `limits` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:71)
- `templates.{path,static}` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:101)
- `map.{url,attribution}`, both required (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:111, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:118)
- `ogc_schemas_location` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:124)
- `manager` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:127)
- `api_rules.{api_version, strict_slashes, url_prefix, version_header}` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:146, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:150, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:153, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:156, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:161)

Required: `bind`, `url`, `mimetype`, `encoding` and `map` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:164). (read)

**Runtime notes** (read):
- `server.url` plus the `api_rules.url_prefix` make up the base URL used in every link (`pygeoapi/api/__init__.py:528`).
- `encoding` → `CHARSET` (`pygeoapi/api/__init__.py:532`).
- `gzip` (`pygeoapi/api/__init__.py:533`).
- `templates` defaults to the package templates (`pygeoapi/api/__init__.py:541`). `templates.static` sets Flask's static folder (`pygeoapi/flask_app.py:67`).
- `pretty_print` (`pygeoapi/api/__init__.py:544`).
- `cors` needs `flask-cors` (`pygeoapi/flask_app.py:86`).
- `admin` (`pygeoapi/flask_app.py:61`; OpenAPI admin paths `pygeoapi/openapi.py:568`).
- The whole `server` section has no `additionalProperties`, like everything else.

## 14. Startup, validation and invalid configs

- The Flask, Starlette and Django apps read `PYGEOAPI_CONFIG` via `get_config()` and `PYGEOAPI_OPENAPI` via `load_openapi_document()`, **once, at import** (`pygeoapi/flask_app.py:55`, `pygeoapi/flask_app.py:56`, `pygeoapi/starlette_app.py:67`, `pygeoapi/starlette_app.py:72`, `pygeoapi/config.py:53`, `pygeoapi/openapi.py:1065`). (read)
- **No `validate_config` at startup.** The only callers are the CLI and the Admin API (`pygeoapi/config.py:106`, `pygeoapi/api/admin.py:102`). (read)
- The OpenAPI file must exist, or startup fails with `OpenAPI document … does not exist` (`pygeoapi/openapi.py:1073`). A `.yaml`/`.yml` file is parsed with `yaml_load`; anything else is served as a raw string without parsing (`pygeoapi/openapi.py:1079`). (read) A 3-byte `{}` JSON file is enough to boot. (executed)
- **Tolerance:** the server starts with schema-invalid or semantically broken configs and fails per request. The only exceptions are hard startup errors such as missing languages or a broken pubsub/manager. (executed; see §3.1, §3.3, §3.4, §9, §11)
- **Configs in the repo** (`validate_all.py`, executed), 19 files:
  - **17 pass:** `pygeoapi-config.yml`, `docker/default.config.yml`, `tests/cite/cite.config.yml` and every `tests/pygeoapi-test-config*.yml` except i18n. That includes `tests/pygeoapi-test-config.yml` and `tests/pygeoapi-test-config-failing-collection.yml`, which is schema-valid by design.
  - **2 fail:**
    - `tests/pygeoapi-test-config-i18n.yml`: `{'en': 'Contact URL', 'fr': 'URL de contact'} is not of type 'string'` at `metadata/contact/url` (`tests/pygeoapi-test-config-i18n.yml:124`)
    - `tests/pygeoapi-test-stac-api-config.yml`: `None is not of type 'object'` at `resources/nhgf-stac-static/extents`. It's a mis-indented `extents:` block (`tests/pygeoapi-test-stac-api-config.yml:113`).

## 15. Other semantics a config-generating GUI must know

These are all required by the API or HTML layer, not by the schema. Each case below validates cleanly.

- **Items HTML needs `server.limits.default_items`.** The packaged template does `config['server']['limits']['default_items']` (`pygeoapi/templates/collections/items/index.html:81`). With no `server.limits`, `/collections/{id}/items?f=html` returns **500** (`UndefinedError: 'dict object' has no attribute 'limits'`), while JSON works. (executed) The schema only gives `limits` defaults (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:80). The server section is read-only in v1, so this should be a **warning on load**.
- **`title_field` must point at a string-valued property.** With `title_field: stn_id` (integer values), the item HTML page returns 500 (`TypeError: object of type 'int' has no len()` from `truncate` in `pygeoapi/templates/collections/items/item.html:34`). The same field pointing at a string property works. (executed) **Probe rule:** only offer string-typed fields from `get_fields()` for `title_field`.
- **A wrong provider `type` that bypassed the schema** (`features`): `/collections/{id}` returns 200 with `itemType: null` and no data links, and `/items` returns 400 "Invalid provider type". (executed) The type enum must be enforced by the GUI.
- **stac-collection without `providers`** (bypassing the schema): `/conformance` and `/stac` return 500. **`process` without `processor`** (schema-valid, see §3.1): `/processes` returns 500. (executed)
- **Provider order matters:**
  - The first provider is the default unless one has `default: true` (`pygeoapi/provider/__init__.py:86`).
  - The first provider of each type is the only one used (§9).
  - A lossless editor must preserve list order, and moving a provider changes behaviour.
- **`table` and multi-table sources.** `table` is a plain string (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:553). One provider serves one table, and only the first provider of a type is used (§9). So "publish 3 tables" means **3 resources**, not 3 providers in one resource.
- **Everything is read once at startup** (§14). Every `/collections` request instantiates the tile, map, EDR and coverage providers (§9), and any exception there silently removes the collection from `/collections` (`pygeoapi/api/__init__.py:955`). The live health check should therefore request `/collections/{id}`, which does surface the 500, not just look for the id in `/collections`.
- **`yaml_dump`**, used by the Admin API, is PyYAML `yaml.dump(..., sort_keys=False, indent=4)` (`pygeoapi/util.py:224`). Comments are dropped. It should be treated as lossy (the Admin API is covered in another note).
- **Core keys typed by the schema but coerced by code:** `count` goes through `str2bool` (`pygeoapi/provider/base.py:70`), so the code accepts `"false"` while the schema rejects it. Emit real booleans.

## 16. Doc/code/schema conflicts

| # | Topic | Docs say | Schema says | Code does |
|---|---|---|---|---|
| 1 | Link `title` | only `type`, `rel`, `href` needed (`docs/source/configuration.rst:349`) | `required` lists only `type`, `rel`, `href` (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:439) | **required** (`pygeoapi/api/collection.py:150`) |
| 2 | Resource `type` | required (`docs/source/configuration.rst:198`); the `visibility: hidden` example omits it (`docs/source/configuration.rst:413`) | not required (typo `requred`, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:693) | required, `v[key]` (`pygeoapi/util.py:571`) |
| 3 | `server.languages` | "must" exist (`docs/source/language.rst:66`) | optional | required (`pygeoapi/l10n.py:444`) |
| 4 | Provider types | feature/coverage/tile (`docs/source/configuration.rst:205`) | 7 types (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:522) | 7 types |
| 5 | `title_field` i18n | allowed (`docs/source/language.rst:160`) | string (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:575) | translated (`pygeoapi/api/itemtypes.py:680`) |
| 6 | Multiple bboxes | — | single bbox (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:454) | list of bboxes accepted (`pygeoapi/api/collection.py:98`) |
| 7 | `uri_field`, `always_xy`, `x_field`/`y_field`/`z_field` | documented (`docs/source/configuration.rst:261`, `docs/source/configuration.rst:288`) | absent | read (`pygeoapi/provider/base.py:73`, `pygeoapi/crs.py:556`) |
| 8 | `options` types | "key value pairs" (`docs/source/configuration.rst:271`) | PostgreSQL option types for all providers (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:595) | provider-specific |
| 9 | `extents.spatial.crs` default | CRS84 | default CRS84 (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:461) | not defaulted; JSON-LD `spatial` becomes `null` without it (`pygeoapi/linked_data.py:147`) |
| 10 | Schema path | `pygeoapi/schemas/config/…` (`docs/source/configuration.rst:22`) | — | `pygeoapi/resources/schemas/config/…` (`pygeoapi/config.py:68`) |
| 11 | i18n `contact.url` | used by the repo's own i18n test config | string | not translated in JSON-LD (`pygeoapi/linked_data.py:100`) |
| 12 | `terms_of_service`, `license.url` | shown in examples | declared optional (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:281, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:299) | **required** by `openapi generate` (`pygeoapi/openapi.py:272`, `pygeoapi/openapi.py:276`) |
| 13 | `server.limits` | optional (`docs/source/configuration.rst:554`) | optional | `default_items` required by the items HTML template (`pygeoapi/templates/collections/items/index.html:81`) |
| 14 | `format` checks | — | `format: uri` / `date-time` declared (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:615, pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:470) | never enforced: `jsonschema_validate(...)` has no `format_checker` (`pygeoapi/config.py:83`) |
| 15 | Tile/map `format` | optional | optional (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:578) | required when the default is a tile (`pygeoapi/api/collection.py:83`) and always for a map (`pygeoapi/api/collection.py:362`) |

## 17. Open questions for the user

1. **Stricter-than-pygeoapi validation.** The GUI rules in §0 reject configs that `validate_config` accepts: no `type`, links without `title`, quoted temporal values, odd ids. Do you want those as **errors** that block publishing, or **warnings**? I recommend errors, because each one produced a 500 or 400.
2. **Two schema-valid configs the server rejects or mishandles**, and the reverse: an i18n `title_field` or link `title` works at runtime but fails the schema. Should the app offer an "allow i18n here anyway" override, or stick to what `validate_config` accepts? Publishing requires `validate_config` to pass (PROMPT §4 J1 step 7).
3. **Hierarchical ids** (`naturalearth/lakes`) are a documented pygeoapi feature. Allow them in v1 behind an opt-in, or slugs only?
4. **Upstream fixes.** Should we file issues or PRs upstream? Candidates: the `requred` typo (pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:693), link `title` handling, `validate_datetime`'s KeyError when `end` is missing, the provider `options` PostgreSQL `$ref` applying to every provider, and missing `additionalProperties`. Pinning 0.24.0 means we must work around them regardless.
5. **Env-var typing.** `get_typed_value` turns `PASSWORD=123456` into an int and `VERSION=1.10` into `1.1`. Should the runtime package copy this bug-for-bug, as PROMPT §6 implies, or diverge and document it?
