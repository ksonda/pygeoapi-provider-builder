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

**Phase 1: a walking skeleton of Mode A.** The configure wizard works end to
end for CSV, GeoJSON, GeoPackage/SQLite and PostgreSQL/PostGIS:
- connect, with lookups of files, tables and columns
- a probe that loads the provider in the pinned pygeoapi image
- field mapping from what the probe found
- metadata and extents
- review, with every validation layer and OpenAPI generation
- export of the merged config

The dev stack's pygeoapi serves what you export. Mode B (Phase 2), writing
configs in place (Phase 3) and the rest of the catalog (Phase 5) come next; see
[PLAN.md §13](PLAN.md#13-milestones).

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
Chromium for the browser tests and pulls `geopython/pygeoapi:0.24.0`:

```bash
make setup
```

Start the dev stack, pygeoapi and PostGIS:

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
[backend/pyproject.toml](backend/pyproject.toml) and
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
- Secrets are never written to config, specs, code, logs or Git. They're
  written as `${ENV_VAR}` placeholders, and the app lists the variables your
  deployment must define.
