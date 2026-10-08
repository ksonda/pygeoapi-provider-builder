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

**Phase 2: Mode B core.** Both modes work end to end.

- **Configure** (Phase 1): a wizard for CSV, GeoJSON, GeoPackage/SQLite,
  PostgreSQL/PostGIS and providers made in Create. It covers connecting, a probe
  in the sandbox, field mapping, metadata, review with every validation layer
  and OpenAPI generation, and an export of the merged config.
- **Create** (Phase 2): the HTTP JSON API template. The wizard fetches a sample in
  the sandbox and suggests a mapping. You pick the records, id, geometry and
  properties, preview them, set paging and filters, then run the contract
  harness, from the provider itself to a live pygeoapi in the sandbox. Each saved
  version goes to `workspace/providers/<id>/` (a Git repository) and shows up in
  Configure's catalog.
- **Sandbox:** every probe, sample and check runs in a locked-down container.
  Network access goes only to declared hosts, through an egress gateway.

Writing configs in place (Phase 3) and the SQL and time-series templates
(Phase 4) come next; see [PLAN.md §13](PLAN.md#13-milestones).

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
```

Install everything else. This installs the Python and Node dependencies,
checks out the source repositories the docs cite (`make vendor`), installs
Chromium for the browser tests, pulls `geopython/pygeoapi:0.24.0` and builds the
sandbox image from it (`make images`, which needs PyPI):

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
- **PostGIS:** host `postgis`, database `test`, user `postgres`, password
  `postgres`, schema `osm`, table `hotosm_bdi_waterways`, id `osm_id`, geometry
  `foo_geom`. The host is `postgis` because that's how the sandbox and the dev
  stack's pygeoapi reach the database.

To see the dev stack serve your collection:
1. On the Publish step, download `pygeoapi.config.yml`. Save it over
   `docker/pygeoapi/pygeoapi.config.yml`.
2. If the step lists environment variables, add them to
   `docker/pygeoapi/pygeoapi.env` (gitignored), for example
   `PYGEOAPI_HOTOSM_BDI_WATERWAYS_DB_PASSWORD=postgres`.
3. Recreate the container. It regenerates OpenAPI on start, and a plain
   restart wouldn't reread the env file:

   ```bash
   docker compose -f docker/compose.yaml up -d --no-deps --force-recreate pygeoapi
   ```

4. Use **Check the live server** on the Publish step, or open
   <http://localhost:5080/collections>.

`git checkout docker/pygeoapi/pygeoapi.config.yml` restores the dev stack's
config afterwards.

To create a provider, choose **Create a provider** on the home page:
- **The mock API:** URL `http://mockapi:8000/v2/wells`, with query parameters
  `offset=0` and `limit=25` so the wizard spots the paging. That's the name the
  sandbox and the dev stack's pygeoapi use.
- **A real API:** `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json`
  with `type=waterlevels`, NOAA CO-OPS's 302 water-level stations, no key needed.

After **Save**, **Publish a collection with this provider →** opens Configure
with the provider chosen. The dev stack's pygeoapi already has the spec engine
and sees the workspace at `/specs`, so the steps above serve it.

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
