# pygeoapi Provider Builder

A web app for publishing data with [pygeoapi](https://pygeoapi.io) without
hand-writing YAML or Python:

- **Mode A: Configure.** Build a collection from any available provider
  (pygeoapi's built-ins, installed plugins, or providers made in Mode B), then
  validate, preview and publish it.
- **Mode B: Create.** Build a provider for a source pygeoapi can't read today,
  such as an agency's JSON API, a SQL query or a time-series database. Stewards
  use no-code templates; developers use a code workspace. Both paths pass the
  same contract tests and end up in Mode A's catalog.

Target: **pygeoapi 0.24.0** (pinned).

## Status

**Phase 5: catalog breadth.** Configure now covers every provider pygeoapi
0.24.0 ships, the cgs-earth org plugins, and the Geoconnex options.

- **Configure** (Phases 1 and 5): a wizard for all 33 of pygeoapi's providers
  (features, coverages, EDR, vector and map tiles, maps, records, STAC), the 9
  usable pygeoapi-plugins providers, and providers made in Create. It covers
  connecting (with lookups such as OGR layers and an ArcGIS layer's fields), a
  probe in the sandbox, field mapping, metadata, review with every validation
  layer and OpenAPI generation, and an export of the merged config.
  - **Test and inspect** asks each provider what pygeoapi's API would: a
    coverage window drawn from its CoverageJSON, the map image, the first
    vector tiles on a map, an EDR query, a STAC catalog.
  - **Known issues** found in pygeoapi and the plugins are on the catalog
    cards (such as xarray's CoverageJSON failing on missing values), and a
    provider whose libraries the image lacks shows the derived image it needs.
  - **Geoconnex options** in Describe: a JSON-LD template for items, from a
    library (a schema.org place, a Geoconnex hydrologic location, a gage on
    the river network), written beside the config with `templates.path`, and
    a preview of an item's RDF rendered by pygeoapi itself. `uri_field` must
    hold unique URIs.
- **Create without code** (Phases 2 and 4). Three templates, each a wizard that
  samples the source in the sandbox and suggests a mapping:
  - an HTTP JSON API → OGC API - Features
  - a SQL query (a join, a view, computed columns) over PostgreSQL/PostGIS or
    SQLite → Features, checked by a SQL guard and run read-only with a timeout
  - time series at monitoring locations → OGC API - EDR, as CoverageJSON. The
    NOAA CO-OPS API works as is.

  The contract harness checks each one, from the provider itself to a live
  pygeoapi in the sandbox. Each saved version goes to
  `workspace/providers/<id>/` (a Git repository) and shows up in Configure's
  catalog.
- **Create in Python** (Phase 4). Scaffolds for every provider type (features,
  records, coverages, EDR, tiles, maps) come from pygeoapi's own signatures. You
  edit them in the browser with ruff, then run the same contract checks and
  save versions as Git tags. Any template-built provider can be **ejected** to
  equivalent, readable Python that doesn't need the spec engine.
- **Deliver** (Phase 4). A version that passed its checks downloads as a pip
  package (a spec becomes `data: pkg:…`), builds into a test image `FROM
  geopython/pygeoapi:0.24.0`, or is pushed to a Git remote for review. Each
  provider shows which collections use which version.
- **Sandbox:** every probe, sample and check runs in a locked-down container.
  Network access goes only to declared hosts, through an egress gateway.
- **Publish and edit** (Phase 3): a *file* target edits the server's config in
  place. Only the lines that change are rewritten; comments, quoting and keys
  the form doesn't know stay byte for byte. Each publish shows the diff first,
  generates pygeoapi's OpenAPI as a gate, takes a backup, writes atomically,
  reloads pygeoapi and checks that it serves the collection. Any publish can be
  rolled back in one click. The **Collections** page lists what a server
  publishes, and lets you edit a collection, add a provider (J3), duplicate,
  delete or import YAML. An optional *Admin API* target is guarded (ADR-0015).

Hardening (Phase 6) comes next; see [PLAN.md §13](PLAN.md#13-milestones).

Background reading, in order:
1. [PLAN.md](PLAN.md): architecture, schemas, API, sandbox, milestones and risks
2. [docs/pygeoapi-notes.md](docs/pygeoapi-notes.md): what pygeoapi 0.24.0
   actually does, with a `file:line` citation for each fact
3. [docs/decisions.md](docs/decisions.md): architecture decision records
4. [docs/research/](docs/research/): detailed per-topic findings

[PROMPT.md](PROMPT.md) is the original build brief.

## Quickstart (macOS)

You need [Homebrew](https://brew.sh) and Docker Desktop, running. The original
machine had Docker CLI 20.10.23 and Compose 2.15.1, which work; updating Docker
Desktop is still recommended.

```bash
brew install uv node
git clone https://github.com/ksonda/pygeoapi-provider-builder.git
cd pygeoapi-provider-builder
```

Install everything else. This installs the Python and Node dependencies,
checks out the source repositories the docs cite (`make vendor`), installs
Chromium for the browser tests, pulls `geopython/pygeoapi:0.24.0` and builds the
sandbox image from it (`make images`, which needs PyPI). To test the
pygeoapi-plugins providers too, `make images-plugins` builds a sandbox with
them; set `sandbox.image` in `provider-builder.yaml` to
`provider-builder/sandbox-plugins:0.24.0`:

```bash
make setup
```

Start the dev stack: pygeoapi, PostGIS and a mock JSON API:

```bash
make stack-up
```

Build the UI once, then start the backend, which serves it:

```bash
npm --prefix frontend run build
```

```bash
make dev-backend
```

Open <http://127.0.0.1:8765> and choose **Create a collection**. For example:
- **CSV:** file `/data/obs.csv`, X column `long`, Y column `lat`, id `id`.
- **A coverage:** *Raster file*, `/data/CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2`,
  native format `GRIB`, media type `application/x-grib2`.
- **Vector tiles:** *Pre-rendered vector tiles*, Browse to the folder
  `/data/tiles/ne_110m_lakes`, zoom 0 to 5.
- **A WMS:** *Maps from a WMS*, URL `http://mockapi:8000/wms`, layers
  `wells,graticule`.
- **PostGIS:** host `postgis`, database `test`, user `postgres`, password
  `postgres`, schema `osm`, table `hotosm_bdi_waterways`, id `osm_id`, geometry
  `foo_geom`. The host is `postgis` because that's how the sandbox and the dev
  stack's pygeoapi reach the database.

The dev stack is a *file* target, so **Publish** serves your collection there
directly. It writes `docker/pygeoapi/pygeoapi.config.yml`, then recreates the
pygeoapi container, which regenerates OpenAPI and rereads its env file. Then it
checks `/collections/{id}` and the items. If Review lists environment
variables, add them to `docker/pygeoapi/pygeoapi.env` (gitignored) before you
publish, for example `PYGEOAPI_HOTOSM_BDI_WATERWAYS_DB_PASSWORD=postgres`.

**Collections** (top right) lists what the dev stack serves: edit, add a
provider, duplicate, delete, import YAML, and the publish log with rollback.
The live config is gitignored; `make stack-up` copies it from
`docker/pygeoapi/pygeoapi.config.seed.yml` when it's missing. To start over:

```bash
make stack-reset-config
```

Backups and the publish log go to `.provider-builder/` (gitignored).

To create a provider, choose **Create a provider** on the home page:
- **The mock API:** URL `http://mockapi:8000/v2/wells`, with query parameters
  `offset=0` and `limit=25` so the wizard spots the paging. That's the name the
  sandbox and the dev stack's pygeoapi use.
- **A real API:** `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json`
  with `type=waterlevels`, NOAA CO-OPS's 302 water-level stations, no key needed.

After **Save**, **Publish a collection with this provider →** opens Configure
with the provider chosen. The dev stack's pygeoapi already has the spec engine
and sees the workspace at `/specs`, so publishing serves it.

### Targets

`provider-builder.yaml` lists where collections are published:

| `kind` | What Publish does |
|---|---|
| `export` | Nothing on the server: download the merged config or copy the block. |
| `file` | Edits `base_config` in place. If `openapi` is set, it writes the OpenAPI document there first, then the config. Then it reloads with `reload`: `none` (instructions only), `command` (`reload_command`, an argv list run in the settings folder), `signal` (HUP the pid in `pid_file`) or `watch` (the server reloads itself, e.g. gunicorn `--reload --reload-extra-file`). Then it polls `server_url` for up to `verify_timeout_s`. Keeps the last `keep_backups` backups. |
| `admin` | Writes through pygeoapi's Admin API, one write per publish. Requires `reload: watch` or `command`, warns that the Admin API drops comments, and needs an acknowledgement for secret-bearing providers (ADR-0015). |

With the official Docker image, leave `openapi` unset and recreate or restart
the container: its entrypoint regenerates OpenAPI at start. With
`run-with-hot-reload`, nothing regenerates it, so set `openapi` to the file
`PYGEOAPI_OPENAPI` names.

For frontend work with hot reload, run `make dev-backend` and `make
dev-frontend` in two terminals, then open <http://127.0.0.1:5173>.

`.claude/launch.json` defines the same two servers for Claude Code's preview.
While the repository is under `~/Documents`, macOS privacy protection can stop
the desktop app from launching them. If it does, run them from a terminal.

### Ports

Everything binds to 127.0.0.1.

| Port | What |
|---|---|
| 8765 | The backend API, plus the built UI from `frontend/dist` |
| 5173 | Vite's dev server, which proxies `/api` to 8765 |
| 5080 | The dev stack's pygeoapi. Not 5000: the macOS AirPlay Receiver uses that port. |
| 55432 | The dev stack's PostGIS (database `test`, user and password `postgres`) |
| 8090 | The dev stack's mock JSON API (`mockapi:8000` inside the stack) |
| 8766 | The backend that the Playwright tests start |
| 5081 | The throwaway pygeoapi the publish E2E tests write to |

## Checks

Run every suite (PLAN.md §12). This needs Docker, starts the dev stack, and
treats a skipped Docker suite as an error:

```bash
make check
```

The checks that don't need Docker: lint, types, citations, the drift check,
unit tests, the frontend build and the API types.

```bash
make check-fast
```

`make help` lists every target. CI runs `make check` on each push and pull
request (`.github/workflows/ci.yml`).

## Dependencies

Each one is pinned. The Python ones are justified inline in
[backend/pyproject.toml](backend/pyproject.toml),
[runtime/pyproject.toml](runtime/pyproject.toml) (httpx and python-jsonpath, the
spec engine's only additions to pygeoapi's own) and
[pyproject.toml](pyproject.toml). For the frontend:

| Package | Why |
|---|---|
| react, react-dom | The UI |
| ajv | Inline form validation against the same compiled schema the backend checks (ADR-0004) |
| openapi-fetch; openapi-typescript (dev) | A typed API client generated from the backend's OpenAPI (ADR-0011) |
| @uiw/react-codemirror, @codemirror/lang-yaml | The YAML preview (CodeMirror 6, PLAN.md §11) |
| maplibre-gl | Extent drawing and the sample map, with no network style by default |
| vite, @vitejs/plugin-react | Dev server and build |
| typescript 5.9.3 | Not 6 or 7: openapi-typescript 7.13 needs TypeScript 5 |
| eslint, typescript-eslint, eslint-plugin-react-hooks | Lint |
| @playwright/test, @axe-core/playwright | End-to-end tests with WCAG checks |

## Security: read before deploying

- The builder binds to **127.0.0.1** and is a single-user tool. If you deploy
  it for others, put it behind a reverse proxy that handles authentication.
- **pygeoapi's Admin API has no authentication.** pygeoapi leaves access
  control to whoever deploys it. If you enable `server.admin: true`, the
  `/admin/*` routes **must** sit behind authentication, and only this app's
  backend should call them. Never expose them publicly.
- Custom provider code runs only in disposable sandbox containers, and it
  reaches production only as a reviewed package (Git pull request plus CI).
- The sandbox reaches only the hosts a job declares, through an egress gateway
  that refuses link-local and metadata addresses, and private ones unless
  `sandbox.private_hosts` in `provider-builder.yaml` names the host.
- A created provider's spec checks the same rules itself when it runs in
  pygeoapi. Allow a private host there with `PYGEOAPI_SPEC_PRIVATE_HOSTS`.
- Secrets are never written to config, specs, code, logs or Git. They're
  written as `${ENV_VAR}` placeholders, and the app lists the variables your
  deployment must define.
- A `file` target's `reload_command` runs with the builder's permissions.
  It comes only from `provider-builder.yaml`, never from the UI.
- With `server.admin: true`, pygeoapi's OpenAPI document embeds the resolved
  config, secrets included, and serves it at `/openapi`. Prefer the `file`
  target, which writes without enabling the Admin API.
