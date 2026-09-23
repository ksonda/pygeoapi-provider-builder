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

**Phase 0: research and plan.** There is no application code yet. Read, in order:

1. [PLAN.md](PLAN.md): architecture, schemas, API, sandbox, milestones and risks
2. [docs/pygeoapi-notes.md](docs/pygeoapi-notes.md): what pygeoapi 0.24.0
   actually does, with a `file:line` citation for each fact
3. [docs/decisions.md](docs/decisions.md): architecture decision records
4. [docs/research/](docs/research/): detailed per-topic findings (provider key
   inventories, Admin API experiments, prior art)

[PROMPT.md](PROMPT.md) is the original build brief.

## Development setup (macOS)

Recorded on 2026-09-23 on macOS 15.3 (Apple silicon). The machine had Homebrew
Python 3.10 and 3.11 only, but pygeoapi 0.24 needs Python ≥ 3.12.

```bash
brew install uv
```

```bash
uv venv --python 3.12 .venv
```

```bash
uv pip install --python .venv/bin/python "pygeoapi==0.24.0"
```

The pinned pygeoapi source, for its docs, test fixtures and citations
(`vendor/` is gitignored):

```bash
git clone --depth 1 --branch 0.24.0 https://github.com/geopython/pygeoapi.git vendor/pygeoapi
```

Check that the installed package matches the tag. This prints nothing when they
are identical:

```bash
diff -rq --exclude=__pycache__ .venv/lib/python3.12/site-packages/pygeoapi vendor/pygeoapi/pygeoapi
```

Prior-art repositories used by the research notes (optional):

```bash
git clone --depth 1 https://github.com/cgs-earth/pygeoapi-plugins.git vendor/pygeoapi-plugins
```

```bash
git clone --depth 1 https://github.com/cgs-earth/pygeoapi-geoconnex-examples.git vendor/pygeoapi-geoconnex-examples
```

```bash
git clone --depth 1 https://github.com/byteroad/pygeoapi_config.git vendor/pygeoapi_config
```

### Needed from Phase 1 on

- **Node.js** for the frontend (not installed on the original machine):

  ```bash
  brew install node
  ```

- **Docker Desktop, running.** Needed for the docker compose stack (Phase 1)
  and the sandbox runner (Phase 2). The original machine had the Docker CLI
  20.10.23 and Compose v2.15.1 installed, but the daemon was stopped. Updating
  Docker Desktop is recommended.

## Checks

Verify every `path:line` citation in `docs/` against the vendored sources:

```bash
.venv/bin/python tools/check_citations.py
```

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
