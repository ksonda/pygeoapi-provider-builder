# Architecture decisions

Short ADRs. Each records the context, the decision, its consequences and the
evidence. "Proposed" means it still needs your confirmation or a spike in the
phase named; "Accepted" means work proceeds on it. Source citations are
`path:line` in pygeoapi 0.24.0 (`vendor/pygeoapi`) unless another repo is named.

| # | Decision | Status |
|---|---|---|
| [0001](#adr-0001-pin-pygeoapi-0240-and-vendor-its-source) | Pin pygeoapi 0.24.0 and vendor its source | Accepted (pending §1 confirmation) |
| [0002](#adr-0002-python-toolchain-uv--cpython-312) | Python toolchain: uv + CPython 3.12 | Accepted |
| [0003](#adr-0003-lossless-editing-with-surgical-text-edits) | Lossless editing with surgical text edits | Accepted |
| [0004](#adr-0004-form-rendering-small-in-house-renderer-over-compiled-descriptors) | Form rendering: small in-house renderer | Accepted (Phase 1) |
| [0005](#adr-0005-a-template-built-providers-data-points-at-its-spec) | A template-built provider's `data` points at its spec | Accepted (Phase 2) |
| [0006](#adr-0006-jsonpath-rfc-9535-via-python-jsonpath) | JSONPath: RFC 9535 via `python-jsonpath` | Accepted |
| [0007](#adr-0007-sql-specs-parse-check-plus-database-side-limits) | SQL specs: parse check plus database-side limits | Accepted |
| [0008](#adr-0008-sandbox-per-run-containers-with-an-egress-gateway) | Sandbox: per-run containers with an egress gateway | Accepted (built in Phase 2) |
| [0009](#adr-0009-edr-engine-classes-define-their-query-methods-in-the-class-body) | EDR engine classes define query methods in the class body | Accepted |
| [0010](#adr-0010-drift-check-ast-alias-tracking-plus-an-allowlist) | Drift check: AST alias tracking plus an allowlist | Accepted |
| [0011](#adr-0011-typed-client-generated-from-the-backends-openapi) | Typed client generated from the backend's OpenAPI | Accepted |
| [0012](#adr-0012-csrf-and-dns-rebinding-protection-even-on-127001) | CSRF and DNS-rebinding protection even on 127.0.0.1 | Accepted |
| [0013](#adr-0013-track-provider-versions-in-a-publish-log-not-in-the-config) | Track provider versions in a publish log, not in the config | Accepted |
| [0014](#adr-0014-a-cited-runtime-rules-layer-on-top-of-pygeoapis-schema) | A cited runtime-rules layer on top of pygeoapi's schema | Proposed (strictness: Q3) |
| [0015](#adr-0015-the-admin-api-is-a-guarded-optional-target) | The Admin API is a guarded, optional target | Proposed (Q8) |
| [0016](#adr-0016-spec-files-are-loaded-with-pygeoapis-own-yaml_load) | Spec files are loaded with pygeoapi's own `yaml_load` | Accepted |
| [0017](#adr-0017-the-sandbox-image-holds-dependencies-our-code-is-mounted) | The sandbox image holds dependencies; our code is mounted | Accepted |
| [0018](#adr-0018-the-spec-engine-vets-every-address-inside-the-connection) | The spec engine vets every address inside the connection | Accepted |

---

## ADR-0001: Pin pygeoapi 0.24.0 and vendor its source

**Context.** pygeoapi's config and plugin interfaces change between minor
releases (for example, how EDR providers register query types). The app
generates config and code that must work on the version our deployments run.

**Decision.** Pin `pygeoapi==0.24.0` everywhere: the backend venv, the runtime
and harness packages, generated `pyproject.toml` files, and the sandbox and
test images (`geopython/pygeoapi:0.24.0`). Clone the repo at tag `0.24.0` into
the gitignored `vendor/pygeoapi` for its docs and test fixtures, and cite it as
`path:line` in docs and descriptors. `tools/check_citations.py` verifies those
citations.

**Consequences.** Upgrading pygeoapi is a deliberate task: bump the pin,
re-clone, rerun the citation check, the drift check and the golden tests, and
update `docs/pygeoapi-notes.md`. The backend checks at startup that its own
pygeoapi version matches the sandbox image's.

Since Phase 1, `tools/vendor.txt` pins every repository the docs cite to the
commit that was read. `make vendor` checks them out, and `make check` fails
when one is missing, at another commit or modified.

**Evidence.** PyPI lists 0.24.0 (2026-07-28) as the latest release, with
`requires_python >=3.12`. The installed wheel is byte-identical to the tag's
`pygeoapi/` tree (`diff -rq`, 2026-09-23). Docker Hub has
`geopython/pygeoapi:0.24.0` for linux/amd64 and linux/arm64.

## ADR-0002: Python toolchain: uv + CPython 3.12

**Context.** pygeoapi 0.24 needs Python ≥ 3.12. This Mac has Homebrew Python
3.11 and 3.10 only.

**Decision.** Use `uv` (installed with `brew install uv`) to provision CPython
3.12 and manage a single uv workspace for `backend/`, `runtime/` and
`harness/`, with one lock file. CPython 3.12 matches the minimum pygeoapi
supports. It should also match the official image, which is `FROM ubuntu:noble`
with the system `python3` in a `/venv` (`Dockerfile:37`, `Dockerfile:133`); noble
ships 3.12. Phase 1 confirms the exact version with `docker run` and adjusts if
it differs.

**Consequences.** Contributors need uv, not a system Python. The README records
the setup steps.

**Confirmed in Phase 1.** The image runs Python 3.12.3 (`docker run`,
2026-10-07). The workspace uses uv-managed CPython 3.12 (`.python-version`;
3.12.14 locally) with one `uv.lock`. Its members so far are `backend/` and
`harness/`; `runtime/` arrives in Phase 2.

## ADR-0003: Lossless editing with surgical text edits

**Context.** Editing must be lossless: keys, comments and ordering that the UI
doesn't understand stay exactly as they were, and saving an unchanged file must
produce identical bytes (definition of done). PROMPT.md §6 suggests `ruamel.yaml`
in round-trip mode.

**Evidence.** Round-tripping pygeoapi's own configs through `ruamel.yaml`
0.19.1 (`preserve_quotes=True`, `width=4096`, best of 12 indent settings per
file) is **not** byte-identical. We tested 17 files: the repo's example config,
the 15 test configs and the Docker default config. Only 2 survived unchanged.
The changes we saw:
- flow sequences get respaced: `bbox: [-180,-90,180,90]` becomes `[-180, -90, 180, 90]`
- an explicit `null` is dropped: `end: null  # …` becomes `end:       # …`
- trailing spaces after keys are removed
- mixed indentation in one file is normalised to a single style (the OGR test
  config changed 132 lines, the PostgreSQL test config 50)

**Decision.** Never re-serialize a whole document. Parse with `ruamel.yaml`
(round-trip mode) only to *locate* nodes: line and column of every key, value
and item. Apply each change as a minimal text patch to the original bytes:
- replace a scalar in place, keeping its trailing comment
- insert or delete a key's line span
- replace the smallest enclosing block, re-serialized in the indentation style
  detected from its siblings
- append a new resource at the end of `resources:`

A save with no changes writes nothing. After patching, the result must parse to
exactly the intended data, or the write is refused. The rest of ADR-0003's
contract lives in PLAN.md §4: atomic writes, backups and ETags.

**Consequences.** More code than calling `dump()`: an estimated 400 lines, plus
a round-trip corpus and property-based tests (random edits must touch only the
lines they target). The corpus is pygeoapi's test configs, the Geoconnex
example configs and hand-written fixtures with anchors, block scalars and odd
indentation. Blocks we re-serialize may still normalise inside themselves;
that minimal diff is documented and shown in the review step.

## ADR-0004: Form rendering: small in-house renderer over compiled descriptors

**Context.** Forms are rendered from compiled descriptor schemas. The options
are react-jsonschema-form (rjsf 6.10, Apache-2.0, React ≥ 18) with custom
widgets, or a small in-house renderer.

| | rjsf + custom widgets | In-house renderer |
|---|---|---|
| Generic JSON Schema (arrays, nested objects) | Built in | We write only what descriptors use |
| Variants (`data` as fields *or* DSN) | `oneOf` selector, needs a custom field to look right | Radio group, first-class |
| Layout across nesting levels (`data.host` beside `table`) | Needs a flattened schema plus custom templates | Flat dotted keys, any order |
| `choices_from` probe results, secret, CRS, bbox and path widgets | Custom widgets | Custom widgets (same work) |
| Server-side errors on fields | `extraErrors` | Direct: every finding carries a JSON pointer |
| WCAG focus and error linking | Good baseline (`aria-describedby`, `focusOnFirstError`), themes vary | Ours to build and test |
| Dependency weight | Core, utils and validator packages plus a theme | ajv only |

**Proposed decision.** Build a small in-house renderer for the descriptor
vocabulary (PLAN.md §5). It uses the compiled JSON Schema with ajv for inline
validation (the backend validates against the same schema) and a UI schema for
layout. The fixed wizard steps (Describe, Extents, Links) are hand-built
components. We control the descriptor vocabulary, so rjsf's main strength,
arbitrary JSON Schema, buys little. The parts that cost the most (variants,
probe-driven pickers, secrets, focus management) are custom either way.

**Confirm in Phase 1** with a one-day spike: render the PostgreSQL descriptor
(two variants, a secret, a field picker, advanced options) both ways. Switch to
rjsf if the in-house version isn't clearly simpler, or if it fails the axe
audit.

**Outcome (Phase 1): accepted.** The renderer is
`frontend/src/forms/DescriptorForm.tsx` (338 lines) plus `validate.ts` (85
lines: Ajv for draft 2020-12, with the backend's format checks). It renders all
four Phase 1 descriptors. That includes PostgreSQL's two variants, its secret
with an editable environment-variable name, the probe-driven pickers, an
ordered property list and advanced options. The E2E suite runs axe (WCAG 2.1 A
and AA) on the connect step for CSV (desktop and phone) and for PostgreSQL with
an error showing, and on the review step. No serious or critical issue remains.
Only the in-house half of the spike was built. With it passing the audit at
this size, rjsf's remaining advantage is generic JSON Schema, which the
descriptor vocabulary doesn't use. Revisit if descriptors ever need arbitrary
nested schemas.

## ADR-0005: A template-built provider's `data` points at its spec

**Context.** A spec-driven collection needs its spec at runtime. The spec could
be a file that `data` points at, or it could be inlined under `options` or
`data`. pygeoapi's schema allows either: `data` is a string or an object, and
extra keys are allowed (`pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml:540`).

**Decision.** `data` holds a reference to the spec:
- a filesystem path, e.g. `/specs/state-wells/1.2.0/spec.yaml`, or
- `pkg:<module>/<resource>`, which the engine resolves with `importlib.resources`
  from an installed provider package.

The engine also accepts an inline mapping under `data`, as an escape hatch for
Admin-API-only deployments. Mode A never emits one by default.

**Why a reference:**
- Specs are versioned artifacts, reviewed in Git.
- One spec can back several collections.
- The pygeoapi config stays short.
- `data` already means "where the data comes from" for file providers.
- A `pkg:` reference pins the version to the installed package.

**Consequences.**
- The spec must be deployed with the config, either as a volume or as a package.
- The publish step checks, in the sandbox, that the reference resolves on the
  target image.
- The file target can't upload spec files to remote servers, so for those, use a
  package (`pkg:`) or the Git PR target.

**Built in Phase 2.** `load_spec` takes a path, `pkg:<module>/<resource>` or an
inline mapping, and caches each per process: files by path, mtime and size, and
inline mappings by content, because pygeoapi builds a provider per request. Saved
versions live at `workspace/providers/<id>/<version>/spec.yaml`. They never
change, so a config pins a version by its path, and the dev stack mounts the
workspace at `/specs`.

## ADR-0006: JSONPath: RFC 9535 via `python-jsonpath`

**Context.** Specs select records and values with paths (`records: $.data.items`).
The engine must never evaluate code (PROMPT.md §7).

**Decision.** Use RFC 9535 JSONPath syntax and the `python-jsonpath` library
(MIT, 2.2.1, 2026-07, no required dependencies). The frontend's JSON-tree path
picker emits the same normalized paths. `jsonpath-ng` uses a non-standard
filter dialect, and JMESPath is a different language that stewards would have
to learn.

**Evidence.** `jsonpath.findall('$.data.items[?@.loc].well_id', doc)` works,
and a Python expression in a filter raises `JSONPathNameError`. Scratch test,
2026-09-23.

## ADR-0007: SQL specs: parse check plus database-side limits

**Context.** The SQL → Features template runs a steward's `SELECT`. §7 requires
that it parse as a single SELECT and run read-only, with a timeout and with
bound parameters only.

**Evidence** (sqlglot 30.19, PostgreSQL dialect):
- `SELECT 1; DROP TABLE wells` is rejected (2 statements).
- `DELETE …` and `COPY …` are rejected.
- `WITH d AS (DELETE FROM wells RETURNING *) SELECT * FROM d` parses as a
  top-level **SELECT**. A check on the statement type alone would pass it.
- `SELECT pg_read_file('/etc/passwd')` and `SELECT pg_sleep(100)` are plain
  SELECTs.

**Decision.** Defence in depth:
1. sqlglot must parse the query as exactly one statement whose root is a query
   (SELECT or a set operation), and a walk of the **whole tree** must find no
   DML, DDL or command nodes.
2. A configurable function denylist (on by default: file, large-object,
   `dblink` and sleep functions).
3. The engine wraps the query as a subquery and applies pygeoapi's filters only
   as SQLAlchemy bound parameters.
4. It runs in a read-only transaction with a statement timeout: `SET TRANSACTION
   READ ONLY` plus `SET LOCAL statement_timeout` on PostgreSQL,
   `START TRANSACTION READ ONLY` plus `MAX_EXECUTION_TIME` on MySQL, and a
   `mode=ro` URI plus a progress-handler timeout on SQLite.
5. The docs and UI tell stewards to connect with a least-privilege, read-only
   database role. The parse check can't stop a privileged role from calling a
   dangerous function we didn't list.

## ADR-0008: Sandbox: per-run containers with an egress gateway

**Context.** §7 of PROMPT.md requires that custom code run only in disposable,
non-root containers, with resource limits and egress only to declared hosts.
Relevant facts about the official image (`docs/pygeoapi-notes.md` §9.2):
- It runs as root.
- Its entrypoint runs `openapi generate --fail-on-invalid-collection` at every
  start (`docker/entrypoint.sh:87`).
- Extra packages must go in through `/venv/bin/python3 -m pip`.

Database drivers speak raw TCP, so an HTTP proxy can't enforce egress for them.

**Decision.**
- **Phase 1 (built):** probes already run in containers, but from the pinned
  image itself, with the harness mounted read-only on `PYTHONPATH`
  (`backend/provider_builder/runner.py`). They use the container settings
  below, except for the network: `--network none`, or the dev stack's network
  when configured (to reach PostGIS), and no gateway yet. Secrets reach the
  container by name (`-e NAME`), never on the command line. With `runner:
  auto`, a subprocess worker takes over when Docker is unavailable. That is
  acceptable only while nothing but core providers runs.
- **Phase 2 onward**, each run is a fresh container from
  `provider-builder/sandbox:0.24.0`:
  - `FROM geopython/pygeoapi:0.24.0` plus the harness and runtime packages
  - `USER 10001:0`, with our worker as `ENTRYPOINT`, bypassing the image's
    entrypoint
  - `--read-only`, tmpfs `/tmp`, `--cap-drop ALL`, `no-new-privileges`, and
    pids, memory and CPU limits
  - a backend-enforced timeout
  - read-only mounts of only the provider's workspace and the data roots,
    mounted at their server paths
- **Network:** a per-run `--internal` network with an egress gateway that
  forwards TCP only for the declared `host:port` pairs. The sandbox reaches them
  through `--add-host`. The gateway refuses link-local and metadata addresses,
  and allows private ranges only when allowlisted. This covers HTTPS (TLS passes
  through end to end) and database protocols alike.

**Consequences.**
- It needs Docker Desktop running on this Mac (Q4).
- Probes match the deployment image, if deployments use the official image (Q1).
  The base image is a setting.
- The backend's Docker access is root-equivalent: tolerated for local
  single-user v1, and revisited in Phase 6 (rootless Docker or a separate
  runner service).
- The sandbox tests in PLAN.md §8.2 prove the limits.

**Built in Phase 2** (`backend/provider_builder/runner.py`, `docker/gateway/gateway.py`):
- A job with no egress gets `--network none`.
- Otherwise each run gets `pb-net-<id>`, an `--internal` network, plus one
  gateway container per declared host, from the sandbox image, non-root.
- The gateway binds only to its internal address before it joins the upstream
  network (`sandbox.egress_network`, the dev stack's network in dev). It
  resolves the host itself and refuses link-local, metadata, multicast and
  reserved addresses. It refuses private addresses unless `sandbox.private_hosts`
  names the host.
- The sandbox gets `--add-host`, so undeclared names don't resolve at all.
- Egress is derived from what the job will connect to: a descriptor's `egress`
  rule (`{kind: sql}` for PostgreSQL) or the URLs of a spec.
- `tests/test_sandbox.py` runs a hostile provider shipped as a job file, and
  every escape it tries is stopped.

## ADR-0009: EDR engine classes define their query methods in the class body

**Context.** In 0.24.0, `BaseEDRProvider.__init_subclass__` builds `query_types`
from `cls.__dict__`, the methods defined *directly* in each class body. A
subclass that defines none raises `ProviderInvalidDataError` at class-creation
time (`pygeoapi/provider/base_edr.py:58-68`). An inherited method doesn't
count.

**Decision.** Every concrete EDR class the project generates or ships (the
time-series engine, scaffolds, ejected code) defines its query methods in its
own class body, even when they only delegate to a shared helper. We never ship
a thin subclass of an engine class that adds no query methods.

**Narrowing per spec.** The API rejects a query type that isn't in
`p.get_query_types()` (`pygeoapi/api/environmental_data_retrieval.py:292`), and
`get_query_types()` returns `self.query_types`. So the engine sets
`self.query_types` in `__init__` to the spec's `query_types`. An instance
attribute shadows the class attribute, so no subclass is needed.

We rejected a custom `__init_subclass__` over the MRO, which the research showed
works (`docs/research/provider-contract.md` §7.4), because it depends on 0.24
internals.

**Also:**
- Every EDR field carries `x-ogc-unit`, because `get_parameters()` raises
  without it (`pygeoapi/provider/base_edr.py:139`).
- `instances` isn't offered in v1, because of the instance-endpoint bugs.
- The harness checks that `get_query_types()` returns exactly the declared
  query types.

## ADR-0010: Drift check: AST alias tracking plus an allowlist

**Context.** §3 asks for a script that finds the keys each provider module
reads and compares them with its descriptor. Modules read keys through aliases
such as `connection_data`, `self.data_def` and `conn_dict`, not just
`provider_def[...]`.

**Evidence.** A prototype (flow-insensitive alias tracking over the AST) finds:
- the base keys in `pygeoapi/provider/base.py`
- nested OGR keys such as `data.source_capabilities.paging` (`pygeoapi/provider/ogr.py:218`)
- ESRI's top-level `username`, `password`, `token_service` and `referer` (`pygeoapi/provider/esri.py:65-68`)

It misses keys that are passed through `**kwargs` into a connection builder,
such as PostgreSQL's `data.*`, and keys read only by a base class, such as
`xarray-edr` inheriting from `xarray`.

**Decision.** The drift check:
- walks the provider class's MRO across modules and applies alias tracking
- compares the result with the descriptor
- accepts keys the descriptor marks `drift: passthrough`, each with a citation
  showing where the key is forwarded (for example, SQLAlchemy URL
  construction)

It reports missing and invented keys. It runs in CI and whenever pygeoapi is
bumped, and it covers Mode B scaffolds as well.

**Implemented in Phase 1** as `tools/drift_check.py` (`make drift`, part of
`make check`), for the core descriptors.
- `ALIAS_HINTS` names variables that hold `data` across function boundaries.
  PostgreSQL's connection keys arrive as the `connection_data` parameter, so
  plain alias tracking can't see them.
- Keys the API layer reads itself (`type`, `name`, `crs`, `format`, …) count
  as read.
- A descriptor's `drift_ignore` lists keys the code reads that it leaves out on
  purpose, each with a reason and a citation, which a test checks:
  - BaseProvider reads `file_types` and `x_field`/`y_field`/`z_field` for every
    provider, but only STAC, Parquet and xarray providers use them.
  - PostgreSQL also reads `data.database`, an undocumented alias of `dbname`,
    and `data.query`, which it never uses.

## ADR-0011: Typed client generated from the backend's OpenAPI

**Decision.** FastAPI publishes the backend's OpenAPI document. `openapi-typescript`
(7.13) generates types and `openapi-fetch` (0.17) is the runtime client, both
widely used and dependency-light. CI fails if the committed schema and the
generated types are stale: `make api-types` regenerates both into a temporary
directory and compares them (`make api-types-update` refreshes them).

## ADR-0012: CSRF and DNS-rebinding protection even on 127.0.0.1

**Context.** A single-user tool bound to 127.0.0.1 is still reachable from any
web page the user visits. A malicious page can POST to
`http://127.0.0.1:<port>/api/publish`, or use DNS rebinding to read responses.

**Decision.**
- Reject requests whose `Host` isn't in the configured allowlist (default
  `127.0.0.1:<port>`, `localhost:<port>`).
- On every state-changing request, require a matching `Origin` and a custom
  header (`X-Provider-Builder: 1`). A cross-site request then needs a CORS
  preflight, which we never grant.
- When deployed behind a reverse proxy, the proxy handles authentication and
  these checks stay on.

**Implemented in Phase 1** (`backend/provider_builder/security.py`). An
`Origin` equal to the request's own scheme and `Host` passes, since `Host` has
already been checked. The backend can therefore serve the built UI on any
allowlisted host and port, such as Playwright's 8766. Requests marked
`Sec-Fetch-Site: cross-site` are refused as well.

## ADR-0013: Track provider versions in a publish log, not in the config

**Context.** Mode A must show which collections use which version of a created
provider. Adding a tracking key to pygeoapi's config would invent a config key
(§12).

**Decision.** Every publish appends an entry to a JSON Lines publish log (the
resource id, provider id and version, target, config hash and time). Usage is
worked out from the log plus the provider's `name` and `data` in the current
config, for example a versioned spec path or package reference. Nothing is
added to the pygeoapi config.

## ADR-0014: A cited runtime-rules layer on top of pygeoapi's schema

**Context.** `validate_config` is a schema-only check. It raises one
`best_match` error that is often misleading. The schema's process branch
misspells `required`, so untyped resources pass. Several schema-valid configs
make the server return 500 or 400: a link without `title`, a temporal extent
without `end`, a tile provider as default without `format`, a missing
`server.languages` (`docs/pygeoapi-notes.md` §1, §6).

**Decision.** Validation runs four layers:
1. descriptor schema
2. pygeoapi's schema via `iter_errors`, with the `anyOf` branch chosen by the
   resource `type` and `uri`/`date-time` format checks added
3. runtime rules
4. sandbox checks: the probe, and `openapi generate` with the fail flag on

Each runtime rule has an id, a severity, a pygeoapi citation, a fix hint and a
test.

**Open.** Should runtime-rule violations block publishing (recommended, since
each one reproduces a 500 or 400) or only warn? That's Q3 in PLAN.md.

## ADR-0015: The Admin API is a guarded, optional target

**Context.** The research found several problems (`docs/pygeoapi-notes.md` §10):
- Admin API writes start from the config loaded at import, which is never
  refreshed. A second write silently undoes the first, and new resources aren't
  served until workers restart.
- It rewrites the file with PyYAML, which drops comments.
- With `server.admin: true`, the generated OpenAPI embeds the resolved config,
  secrets included (`pygeoapi/openapi.py:819`).
- It has no authentication.

**Decision.**
- Keep the Admin API as an optional target with guardrails: one write per
  publish, and only when the target restarts workers after a write (`reload:
  watch` or `command`).
- Verify through both `GET /admin/config/resources/{id}` and the live endpoint.
- Refuse to publish secret-bearing providers to an admin-enabled target without
  an explicit acknowledgement of the leak.
- The file and Git PR targets are the recommended ones.

**Open:** keep it in Phase 3, or defer it to v2 (Q8).

## ADR-0016: Spec files are loaded with pygeoapi's own `yaml_load`

**Context.** §6 of PROMPT.md requires the runtime to resolve `${VAR}` in spec
files the same way pygeoapi does. pygeoapi's behaviour has sharp edges:
- only plain scalars are substituted, via the `varname` regex (`pygeoapi/util.py:143`)
- a default applies only when the variable is unset
- the whole resulting string is re-typed by `get_typed_value`, so `123456` becomes an int (`pygeoapi/util.py:165`)
- a missing variable raises `EnvironmentError`, i.e. `OSError` (`pygeoapi/util.py:158`)

**Decision.** The runtime package calls `pygeoapi.util.yaml_load` itself, rather
than re-implementing it. That gives bug-for-bug parity for free, and pygeoapi is
already a dependency. The spec editor emits placeholders only in plain-scalar
positions. The UI warns when a variable's value will be re-typed.

## ADR-0017: The sandbox image holds dependencies; our code is mounted

**Context.** PLAN.md §8.2 planned `provider-builder/sandbox:0.24.0` as the pinned
image plus the harness and runtime packages, installed with pip. Baking our own
code in means rebuilding the image for every change, and a stale image runs old
checks against new code.

**Decision.** `docker/sandbox/Dockerfile` adds only the spec engine's third-party
dependencies to `geopython/pygeoapi:0.24.0`: httpx, httpcore, h11, anyio and
python-jsonpath, at the versions `uv.lock` tests. Its tag is the hash of the
Dockerfile and requirements (`make images`), and it is also tagged
`provider-builder/sandbox:0.24.0` for compose. The harness and runtime sources
are mounted read-only on `PYTHONPATH` at run time. The dev stack's pygeoapi runs
the same image the way the stock one runs (root, `/entrypoint.sh`), with the
runtime mounted.

**Consequences.**
- Code changes take effect at once. Only a dependency change needs `make images`
  (CI builds it, which needs PyPI).
- Production installs `pygeoapi-spec-providers` as a package, which brings the
  same dependencies. certifi and idna stay as the image ships them, as they
  would there.

## ADR-0018: The spec engine vets every address inside the connection

**Context.** PROMPT.md §7: HTTP sources follow the probes' host allowlist, never
reaching link-local or metadata addresses. In the sandbox the gateway enforces
that, but a production pygeoapi has no gateway, so the engine must enforce it
too. A check before the request is open to DNS rebinding: the name can resolve
differently when httpx connects.

**Decision.** `pygeoapi_spec_providers.egress` puts a network backend in httpx's
connection pool. It resolves the name, vets every address, refuses if any is
link-local or metadata, and connects to a vetted address. TLS still verifies
the certificate against the hostname.

The rest of the policy is checked before each request:
- only the spec's declared hosts, which also bounds redirects
- `https`, unless the host is allowlisted
- private ranges only for hosts in `PYGEOAPI_SPEC_PRIVATE_HOSTS`; the sandbox sets
  `*`, because there the gateway decides

**Consequences.** It relies on httpx's private `_pool` attribute; the runtime pins
`httpx>=0.28,<0.29`, and `runtime/tests/test_egress.py` fails if the swap stops
taking effect. A simulated rebind and a mixed DNS answer are both refused.

