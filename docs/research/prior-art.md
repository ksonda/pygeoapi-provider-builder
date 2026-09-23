# Prior-art review (Phase 0)

> Status: **draft 1** (2026-09-23). Sections 1 to 3 are complete. Section 4 is preliminary and will be refined.
> Target: pygeoapi **0.24.0** (`vendor/pygeoapi`, tag 0.24.0, commit `f765a64`).
> Everything cloned or fetched was treated as data. Citations use `repo:path:line` (line numbers from `grep -n`) or full URLs.

## Sources examined

| Source | Where | Revision |
|---|---|---|
| byteroad/pygeoapi_config (QGIS plugin, MIT) | `vendor/pygeoapi_config` | `eb75cea48a22887ddff9cdbeedd82dae431cded0`, 2026-08-30 (merge of PR #24, release v.0.24) |
| USGS pygeoapi-plugin-cookiecutter (CC0) | `vendor/pygeoapi-plugin-cookiecutter`, from https://code.usgs.gov/wma/nhgf/pygeoapi-plugin-cookiecutter (public GitLab, project id 6223) | `e14b872b4e87e07c8c5a6d406ee8807632063f3d`, 2022-06-01 (last commit; last project activity 2023-11-25) |
| opengeospatial/ets-ogcapi-features10 | `vendor/ets-ogcapi-features10` | `a314c1e6a9278b14ab9a2ed865cfe36d202f0125`, 2025-02-06 (`pom.xml` = 1.10-SNAPSHOT) |
| opengeospatial/ets-ogcapi-edr10 | `vendor/ets-ogcapi-edr10` | `88656db8e3f89a0a9e8da07953644a4831d15012`, 2026-09-21 (`pom.xml` = 1.4-SNAPSHOT) |
| Docker Hub `ogccite/ets-ogcapi-features10`, `ogccite/ets-ogcapi-edr10` | `https://hub.docker.com/v2/repositories/ogccite/<name>/tags` | queried 2026-09-23 |
| pygeoapi 0.24.0 | `vendor/pygeoapi` | `f765a64fa65350dc93d9df0a1b38755bef6c31b0`, 2026-07-28 |

Scratch reproduction of the round-trip experiment in §1.5 (not part of the repo):
`/private/tmp/claude-501/-Users-konda-Documents-pygeoapi-provider-ui/6838b640-da34-42bf-b227-b524ac19219b/scratchpad/prior-art/pc_roundtrip/roundtrip.py`.

---

## TL;DR: lessons that change our design

1. **Validate against pygeoapi's own JSON Schema at runtime**, and add hand-written rules only as a second layer. pygeoapi_config writes validation rules by hand in dataclasses. It checks the JSON Schema only in CI, and against pygeoapi `master` rather than the release it targets. The hand-written rules mostly check for empty strings.
2. **Never round-trip the config through a lossy typed model.** In our experiment, pygeoapi_config silently dropped top-level `pubsub`, resource-level `limits`, and the PostgreSQL provider's `editable` flag. It also replaced `${DEFAULT_ITEMS}` with the default `10` and removed every comment. We should edit a comment-preserving YAML tree (for example ruamel.yaml round-trip mode) and patch only the nodes the user touched.
3. **The before/after diff shown before saving is the best idea to copy.** pygeoapi_config shows added, removed and changed paths and asks the user to confirm before it writes a file or pushes to a server. It displays the diff as a raw JSON dump in a modal, so we should copy the idea but present it better.
4. **Report errors per field, in plain words, and let users save a draft.** pygeoapi_config shows errors as lists of dotted paths such as `resources.lakes.providers` in modal text boxes, with no link to the offending widget. All of its checks block saving, and there is no draft or warning tier.
5. **The pygeoapi Admin API is a weak backend.** It has no authentication and returns only `400 "Schema validation error"` with no detail. It rewrites YAML with PyYAML, which drops comments, and it restores `${VAR}` placeholders with a jsonpatch hack. Our backend should validate and write files itself; the Admin API should be optional.
6. **The USGS cookiecutter is not a provider scaffold.** It is a stale (2022) fork of the Hypermodern-Python template with an empty `process/__init__.py`, pins of `pygeoapi ^0.11.0` and Python `<3.10,>=3.9`, and broken release automation. We can borrow its checklist (packaging, nox sessions, docs, changelog, CI) but none of its code.
7. **OGC CITE runs headless through TEAM Engine's REST API.** Use `GET /teamengine/rest/suites/{ets-code}/run?iut=…` with HTTP Basic auth `ogctest:ogctest` and pick the output format with the `Accept` header. The images are **amd64-only**, the latest dating from January 2025. The container must reach pygeoapi through the URL set in `server.url`, because pygeoapi builds every link from that value. The EDR suite also needs an `apiDefinition` parameter, and that name is case-sensitive.

---

## 1. byteroad/pygeoapi_config (QGIS plugin)

### 1.1 Snapshot

- A QGIS plugin ("pygeoapi configurator") by DGT and ByteRoad. It is flagged `experimental=True`, and its plugin version `0.24` tracks pygeoapi releases (`pygeoapi_config:metadata.txt:9`, `pygeoapi_config:metadata.txt:32`). The minimum QGIS version is 3.0 (`pygeoapi_config:metadata.txt:7`).
- Maturity: the repo was created 2025-05-16. It has 0 stars, 2 forks and 4 open issues/PRs, including "Fix tests and CI" (#19) and "Address Qt6 compatibility issues" (#21). Releases run from v.0.1-alpha (2025-07-31) to v.0.24 (2026-08-30). Source: GitHub API `https://api.github.com/repos/byteroad/pygeoapi_config` and `/releases`, fetched 2026-09-23.
- Runtime dependencies are PyQt5 and QGIS, plus `PyYAML==6.0.2` (`pygeoapi_config:requirements.txt:1`). It does not depend on pygeoapi itself.
- Docs: https://pygeoapi-config.byteroad.net (built from `pygeoapi_config:docs/index.md`).

### 1.2 UI structure

- A single QDialog has three tabs: **Server**, **Metadata** and **Resources** (`pygeoapi_config:pygeoapi_config_dialog_base.ui:45`, `:878`, `:1466`). Radio buttons at the bottom switch between **Local File** and **Server Connection** (`pygeoapi_config:pygeoapi_config_dialog_base.ui:2524-2542`).
- **Server tab** groups: bind\*, map\*, logging\*, limits, templates. An asterisk marks a group as mandatory in the UI labels.
- **Metadata tab** groups: identification\*, license\*, provider\*, contact\*.
- **Resources tab**: a filterable list of collections ("Select collection") plus a preview pane that draws the bbox on an OSM QgsMapCanvas (`pygeoapi_config:ui_widgets/UiSetter.py:569-593`). Opening a collection shows the detail groups title\*, description\*, keywords\*, links, providers\*, spatial extents\* and temporal extents. Fields that support translation (title, description, keywords) are edited as a list of `(locale, value)` rows.
- **Providers** are edited in a separate modal window that is **generated from the provider dataclass**. Each provider class returns `ui_elements_grid()`, a list of `(field path, type, default, widget kind, placeholder)` tuples. An asterisk suffix marks a mandatory field and `"disabled"` greys out a field that is modelled but not implemented (`pygeoapi_config:models/top_level/providers/ProviderPostgresql.py:62`, `:100`). Widgets are chosen by type: `int` gets a `QLineEdit` with `QIntValidator`, `list` gets a string-list widget, and a CRS field gets a "validate URL" callback (`pygeoapi_config:ui_widgets/providers/utils.py:59-94`).
- Providers the plugin does not support are listed in a second **"Read-only"** list as raw JSON strings (`pygeoapi_config:ui_widgets/UiSetter.py:491`, `pygeoapi_config:docs/index.md:74`).

### 1.3 Which providers it can configure (verified)

The claim holds: only **PostgreSQL** (feature), **WMSFacade** (map) and **MVT-proxy** (tile) are editable.

- `ProviderTemplate.init_provider_from_type` maps FEATURE, MAP and TILE to exactly these three classes (`pygeoapi_config:models/top_level/providers/ProviderTemplate.py:66-78`).
- Deserialisation refuses to cast any other provider name (`pygeoapi_config:models/utils.py:139`).
- The docs table lists the same three (`pygeoapi_config:docs/index.md:70-72`).
- Resource types: only `collection` and `stac-collection` resources are modelled. Anything else, including `process`, is kept as an opaque dict (`pygeoapi_config:models/ConfigData.py:78`, `:107`).
- Even for the three supported providers, some fields are deliberately not editable. For PostgreSQL these are `options`, `time_field` and `properties`, which are "not implemented by choice (greyed out in the UI)" (`pygeoapi_config:models/top_level/providers/ProviderPostgresql.py:38`). Issue #7 lists the intended mandatory and optional fields per provider (https://github.com/byteroad/pygeoapi_config/issues/7).

### 1.4 How it validates before saving

**Where the rules live:** hand-written Python dataclasses, not pygeoapi's JSON Schema.

- `ConfigData` has `ServerConfig`, `LoggingConfig`, `MetadataConfig` and `ResourceConfigTemplate`, and resources hold provider dataclasses (`pygeoapi_config:models/ConfigData.py:19-31`). Each class has a `get_invalid_properties()` (`pygeoapi_config:models/top_level/ServerConfig.py:94`, `pygeoapi_config:models/top_level/MetadataConfig.py:98`, `pygeoapi_config:models/top_level/ResourceConfigTemplate.py:141`, `pygeoapi_config:models/top_level/providers/ProviderPostgresql.py:179`, `pygeoapi_config:models/top_level/providers/ProviderWmsFacade.py:112`, `pygeoapi_config:models/top_level/providers/ProviderMvtProxy.py:134`).
- The checks are almost all "is this string non-blank?" (`is_valid_string`, `pygeoapi_config:models/top_level/utils.py:13`), "is this list non-empty?", and "does the bbox have 4 or 6 numbers?" (`pygeoapi_config:models/top_level/utils.py:32-48`). There are no URL, CRS-URI, enum-completeness or cross-field checks. A URL check exists but is commented out (`pygeoapi_config:models/top_level/MetadataConfig.py:117-119`).
- Type checking on load is a small home-grown checker over `typing` hints (`_is_instance_of_type`, `pygeoapi_config:models/utils.py:235`). It walks only the dataclass fields (`pygeoapi_config:models/utils.py:23`, `:32`). A value of the wrong type is recorded as `wrong_types` and the **default is kept** (`pygeoapi_config:models/utils.py:113`). Missing mandatory fields are recorded as "defaults used" (`pygeoapi_config:models/utils.py:123`).
- **The JSON Schema is used only in tests.** `test_json_schema_on_open_save` shells out to `check-jsonschema` against the schema on pygeoapi's **master** branch (`pygeoapi_config:tests/test_yaml_save.py:50-55`). The plugin does not validate against the schema at runtime, and does not pin the schema to the pygeoapi version it targets.

**Checks at runtime, and how errors are shown:**

| When | What is checked | Presentation | Blocking? |
|---|---|---|---|
| While typing | `QDoubleValidator` on bbox fields, `QIntValidator` on int fields (`pygeoapi_config:ui_widgets/UiSetter.py:558-567`, `pygeoapi_config:ui_widgets/providers/utils.py:83`) | The widget refuses the keystroke | Yes, silently |
| On a button click | CRS URL: an HTTP GET, failing on non-200 or on an `ExceptionReport` in the body (`pygeoapi_config:ui_widgets/utils.py:47-72`) | Info or warning message box | No |
| On open (file or server) | Wrong types and missing fields from deserialisation | One modal text box listing dotted paths: "All missing or replaced properties (check logs for more details): [...]" (`pygeoapi_config:pygeoapi_config_dialog.py:372-377`) | No: the file opens with defaults substituted |
| Provider window "Save" | Mandatory provider fields (`ConfigData.set_validate_new_provider_data`, `pygeoapi_config:models/ConfigData.py:189-210`) | `QMessageBox.warning`: "Invalid Provider values: [...]" (`pygeoapi_config:pygeoapi_config_dialog.py:604-609`) | Yes: the window stays open |
| Resource "save/preview" | alias, title, description, keywords, bbox parse, at least one provider, and temporal format `%Y-%m-%dT%H:%M:%SZ` (`pygeoapi_config:ui_widgets/DataSetterFromUi.py:432-484`) | `QMessageBox.warning`: "Invalid fields' values: [...]" (`pygeoapi_config:pygeoapi_config_dialog.py:688-695`) | Yes |
| Main "Save" | `validate_config_data()` across server, metadata and every modelled resource (`pygeoapi_config:models/ConfigData.py:212-226`) | Modal text box: "Properties are missing or have invalid values: [...]" (`pygeoapi_config:pygeoapi_config_dialog.py:387-402`) | Yes: it cannot save, but it will open invalid files |
| After validation passes | Diff between the original YAML and the new dict (`_diff_original_and_current_data`, `pygeoapi_config:pygeoapi_config_dialog.py:410-452`) | JSON dump of `{added, removed, changed}` with OK/Cancel (`pygeoapi_config:pygeoapi_config_dialog.py:446`) | It is a confirmation step |

Errors are only ever reported as a list of paths or labels. No widget is highlighted, nothing explains why a value is invalid or what it should be, and there is no severity other than "blocking". A path such as `resources.lakes.providers` points at a model location, which the user has to translate back into a widget.

### 1.5 YAML read/write (verified experimentally)

- **Library:** PyYAML. Files are read with `yaml.safe_load` (`pygeoapi_config:pygeoapi_config_dialog.py:333`) and written with `yaml.dump(..., Dumper=CustomDumper(SafeDumper), sort_keys=False, indent=4)` (`pygeoapi_config:pygeoapi_config_dialog.py:140-160`, `:300-308`). Custom representers emit bbox lists in flow style and datetimes as unquoted `…Z` timestamps.
- The output is **rebuilt from the dataclasses** (`asdict_enum_safe`, `pygeoapi_config:models/ConfigData.py:145`), not from the original text.

I ran the plugin's Qt-free model layer on three inputs (see the scratch script above). The pygeoapi 0.24.0 default `pygeoapi-config.yml` round-tripped with no diff. `tests/cite/cite.config.yml` lost `resources.*.crs` (a legacy key). A crafted file gave this:

| Input feature | Result after open, then save |
|---|---|
| `# comments` anywhere | **All lost** (PyYAML) |
| Key order | Re-ordered to dataclass field order. For example, `server.map` moves before `server.languages`. Unmodelled dict subtrees keep their order. |
| `${VAR}` in a `str` or `int\|str` field (`server.url`, `bind.port`, `data.host`, `data.password`) | Kept verbatim (the plugin never expands env vars) |
| `${VAR}` in an `int`-only field (`server.limits.default_items: ${DEFAULT_ITEMS}`) | **Replaced with the default `10`**. It is reported as a "wrong type" in the on-open warning, but saving is not blocked. |
| Top-level `pubsub:` (valid in the 0.24 schema) | **Dropped** |
| Resource-level `limits:` | **Dropped** ("limits: ignored for now", `pygeoapi_config:models/top_level/ResourceConfigTemplate.py:118`) |
| Unmodelled key on a supported provider (`editable: true` on PostgreSQL) | **Dropped** |
| Unsupported provider (GeoJSON) or resource type (`process`) | Kept as an opaque dict |

The only safety net is the diff dialog, which lists the dropped keys under `removed` before saving.

### 1.6 Talking to the pygeoapi Admin API

- The URL is built as `{http|https}://{host}:{port}/admin/config` from a small dialog (`pygeoapi_config:pygeoapi_config_dialog.py:95-103`). A path prefix, such as pygeoapi behind a reverse proxy at `/pygeoapi/`, cannot be entered.
- Pull is `GET /admin/config` (`pygeoapi_config:pygeoapi_config_dialog.py:269`). Push is `PUT /admin/config` with the **whole** config as JSON (`pygeoapi_config:pygeoapi_config_dialog.py:239`) and headers `accept: */*` and `Content-Type: application/json` (`pygeoapi_config:pygeoapi_config_dialog.py:79`). Success means HTTP 204 (`pygeoapi_config:tests/test_server_push.py:64`). The per-resource endpoints and `PATCH` are not used.
- **No authentication.** There is a `TODO: support authentication` (`pygeoapi_config:pygeoapi_config_dialog.py:236`, `:266`). The docs require an "unprotected" instance with admin enabled and hot reload, "for testing purposes only" (`pygeoapi_config:docs/index.md:37-41`).
- What pygeoapi 0.24.0 does on the other side, which is relevant to us:
  - `GET` returns the **raw** config with `${VAR}` not expanded (`pygeoapi:pygeoapi/api/admin.py:176`).
  - `PUT` validates against the JSON Schema (`pygeoapi:pygeoapi/config.py:74-83`). On failure it returns only `400 ValidationError "Schema validation error"`, with no path or message (`pygeoapi:pygeoapi/api/admin.py:231-238`).
  - It then **restores env-var placeholders** by computing a jsonpatch from the expanded config to the raw config and applying it to the new config (`pygeoapi:pygeoapi/api/admin.py:130-137`). This relies on positions not moving.
  - Finally it `yaml_dump`s the file, so comments are gone (`pygeoapi:pygeoapi/api/admin.py:141`).
  - Admin routes are `/admin/config` (GET, PUT, PATCH) and `/admin/config/resources[/{id}]` (`pygeoapi:pygeoapi/flask_app.py:580-646`). Authentication "is beyond the responsibility of pygeoapi" (`pygeoapi:docs/source/admin-api.rst:29`).

### 1.7 Target version and tests

- It targets the latest pygeoapi (plugin v0.24 was released on 2026-08-30). The CI, however, starts `geopython/pygeoapi:0.23.2` (`pygeoapi_config:.github/workflows/unit-tests.yml:28`), and the schema test uses **master** (`pygeoapi_config:tests/test_yaml_save.py:55`).
- Tests use pytest with pytest-qt, headless through `QT_QPA_PLATFORM=offscreen` (`pygeoapi_config:.github/workflows/unit-tests.yml:62-66`, `pygeoapi_config:requirements_dev.txt:3`):
  1. Round-trip every `tests/yaml_samples/*.yml` (open, save, then `check-jsonschema`).
  2. Run the save-time UI validation on each sample, which must pass.
  3. Open, save, re-open, and assert an empty diff after excluding keys already warned about (`pygeoapi_config:tests/test_yaml_save.py:32-148`).
  4. A live pull, push, pull round-trip against a docker pygeoapi (`tests/test_server_push.py`).
- Samples are named `org_repo_@commit_file.yml` (README). Four more real-world samples sit in `tests/` outside `yaml_samples/` and are **not** picked up by the glob. It is unclear whether they fail.
- Test-quality notes: the tests delete and recreate `saved_*.yml` inside the source tree at import time (`pygeoapi_config:tests/test_yaml_save.py:15-17`). The on-open warnings are excluded from the diff assertion, so dropped keys are "expected" rather than caught.

### 1.8 Lessons for our validation UX

1. **Keep a layered model, with better presentation:**
   - *Keystroke*: input masks and types.
   - *Field blur*: schema and rule check for that field.
   - *Section*: cross-field checks.
   - *Pre-save*: the full JSON Schema, plus pygeoapi-specific rules, plus an optional "live" probe such as a DB connection or URL reachability.
   - *Post-save*: optionally, load the config with pygeoapi itself and hit the collection.
2. **Anchor each error to its field.** Keep a JSON Pointer or path for every error and map it back to the form control. Show a summary panel whose entries jump to the field. The same path strings can drive both views.
3. **Use two severities.** *Errors* block publishing but still allow a draft save. *Warnings* never block: unknown key preserved, env var unresolved, CRS URL unreachable, deprecated key. pygeoapi_config's "open anything, save nothing invalid" rule frustrates users because it has no draft state.
4. **Explain the fix, not just the path.** For example: "bbox needs 4 or 6 numbers (minx, miny, maxx, maxy); you gave 3."
5. **Keep the pre-save diff**, and render it as a readable YAML diff: side-by-side, with changed and removed keys highlighted and dropped keys called out.
6. **Generate provider forms from metadata** (as `ui_elements_grid()` does), but take that metadata from a JSON Schema or descriptor instead of positional tuples. Mark "known but unsupported" fields as read-only with an "edit as raw YAML" escape hatch, rather than dropping or greying them out.
7. **Run network checks asynchronously and on demand** (CRS URI resolvable, DB reachable), and show them as warnings.

### 1.9 Pitfalls to avoid

- A lossy typed model as the source of truth. Unknown keys, comments, order and env vars in typed fields are all lost (§1.5).
- Hand-maintained rule duplication that drifts from pygeoapi's schema. The plugin validates against master in CI but hand-written rules at runtime, and neither is version-pinned.
- Positional coupling: providers are packed into ordered lists and parsed back with `len(values) != 16` guards (`pygeoapi_config:models/top_level/providers/ProviderPostgresql.py:113-139`). A list widget shows `" | "`-joined strings that are split again (`pygeoapi_config:pygeoapi_config_dialog.py:659`). This is brittle.
- Home-grown type casting with latent bugs. For example, `if ui_value is list:` compares a value with the `list` type and is never true (`pygeoapi_config:ui_widgets/providers/NewProviderWindow.py:98`). Circular imports make the model layer depend on import order, as we found when running it outside Qt.
- Replacing an invalid value with a default and continuing. Prefer to keep the user's value and flag it.
- Composing the admin URL from host and port only (no path prefix), not supporting authentication, and pushing the whole config with `PUT` rather than per-resource `PUT`/`PATCH`.
- Letting the UI toolkit leak into validation logic, which forces tests to need a Qt display. Keep validation pure: in our case, FastAPI and pydantic, with no UI imports.

---

## 2. USGS pygeoapi-plugin-cookiecutter

### 2.1 Snapshot

- Public at https://code.usgs.gov/wma/nhgf/pygeoapi-plugin-cookiecutter (GitLab API: project 6223, `visibility: public`, created 2021-07-27, 3 stars, 0 forks). It was cloned without authentication.
- The last commit is from 2022-06-01 ("More little debugging tweaks."). Of five MRs, four were merged between 2021-10 and 2022-04; one Renovate MR (2023-05) is still open. Issue #3 is open: "Change src/process to src/_pygeoapi_process to work as a plugin in our pygeoapi" (https://code.usgs.gov/wma/nhgf/pygeoapi-plugin-cookiecutter/-/work_items/3).
- It is a fork of cjolowicz's *Hypermodern Python* cookiecutter with conda added (`pygeoapi-plugin-cookiecutter:README.md:22-24`). The repo-level `.github/workflows/*.yml` still reference `cookiecutter-hypermodern-python` paths (`pygeoapi-plugin-cookiecutter:.github/workflows/tests.yml:20-30`, `pygeoapi-plugin-cookiecutter:.github/workflows/reset-instance.yml:10-19`). They are leftovers that cannot run on GitLab.
- License: CC0 / US public domain (`pygeoapi-plugin-cookiecutter:LICENSE.md:4-5`, `pygeoapi-plugin-cookiecutter:code.json:13`).

### 2.2 Generated project layout

```
{{project_name}}/                  # default project_name "nldi-pygeoapi-plugin" (cookiecutter.json:2)
├── pyproject.toml                 # Poetry (poetry-core backend)
├── environment.yml                # conda: python>=3.9,<3.10 only
├── meta.yaml                      # conda-build recipe
├── noxfile.py                     # nox sessions, conda venv backend
├── Dockerfile.nox                 # CI image FROM an internal USGS artifactory mirror
├── .gitlab-ci.yml                 # build-nox + test matrix, internal runners (tags wma/aws)
├── .github/workflows/{tests,release,labeler}.yml, dependabot, release-drafter
├── .travis.yml, .readthedocs.yml, .pre-commit-config.yaml, .flake8, .darglint, .bumpversion.cfg
├── README.md, HISTORY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, DISCLAIMER.md, LICENSE.md, codecov.yml
├── docs/ (Sphinx: index, usage (sphinx-click), reference (autodoc), license, …)
├── src/{{package_name}}/
│   ├── __init__.py                # __version__ only
│   ├── __main__.py                # empty click command
│   ├── process/__init__.py        # EMPTY
│   └── py.typed
└── tests/test_main.py             # CliRunner: exit code 0
```

### 2.3 What it generates for each plugin type

**Nothing pygeoapi-specific.**

- There is no provider type (feature, coverage, EDR, tile) and no processor class. `src/{{package_name}}/process/__init__.py` is an empty file.
- There is no `BaseProvider` or `BaseProcessor` subclass, no `PROCESS_METADATA`, and no example pygeoapi config snippet.
- There is no test that loads the plugin through `pygeoapi.plugin.load_plugin`, and no fixture data.
- The only code is a click CLI stub (`src/{{cookiecutter.package_name}}/__main__.py:1-12`) with a test that invokes it (`pygeoapi-plugin-cookiecutter:{{cookiecutter.project_name}}/tests/test_main.py:14-17`).
- The only pygeoapi coupling is the dependency pin.

### 2.4 Version pins (the "Python 3.9" claim is confirmed)

- `python = "<3.10,>=3.9"`, `pygeoapi = "^0.11.0"` (so >=0.11,<0.12), `click = "<8"` and `numpy = "^1.21.0"` (`{{cookiecutter.project_name}}/pyproject.toml:28-32`).
- The same pins appear in `nox` `python_versions = ["3.9"]` (`pygeoapi-plugin-cookiecutter:{{cookiecutter.project_name}}/noxfile.py:13`), in conda `python>=3.9,<3.10` (`pygeoapi-plugin-cookiecutter:{{cookiecutter.project_name}}/environment.yml:6`), and in the README: "The template supports Python 3.9." (`pygeoapi-plugin-cookiecutter:README.md:224`).
- These pins are now years out of date. pygeoapi 0.24.0 is 13 minor versions later.
- Internal inconsistencies:
  - The generated GitHub `tests.yml` also runs Python 3.8 sessions, which the pyproject pins exclude (`{{cookiecutter.project_name}}/.github/workflows/tests.yml:19-21`).
  - `release.yml` runs `python3 setup.py bdist_wheel` in a Poetry project that has no `setup.py` (`pygeoapi-plugin-cookiecutter:{{cookiecutter.project_name}}/.github/workflows/release.yml:52-54`).
  - Coverage is set to `fail_under = 100` (`pygeoapi-plugin-cookiecutter:{{cookiecutter.project_name}}/pyproject.toml:82`).

### 2.5 What to borrow for our scaffolds

Worth borrowing, as ideas rather than code:

- A **`src/` layout** with `py.typed`, and a single version source bumped by one tool (bump-my-version or hatch-vcs rather than the abandoned advbumpversion).
- A **nox or tox session list**: lint, type-check, tests, docs. Make these the generated project's CI entry points, so that local runs and CI run the same thing.
- **Community files** (CONTRIBUTING, CODE_OF_CONDUCT, LICENSE, CHANGELOG), a docs skeleton, dependabot or renovate, and release drafter.
- **`.cookiecutter.json` replay** (`{{cookiecutter.project_name}}/.cookiecutter.json:1`). The generated repo records the answers it was created with. This is a cheap way to re-generate or upgrade scaffolds later. Our descriptor file should have the same property, so the builder can re-render or upgrade a package.

What we need that it lacks. This is the minimum for our code scaffolds:

| Artifact | Contents |
|---|---|
| `pyproject.toml` | PEP 621 with hatchling or setuptools, `requires-python` matching pygeoapi 0.24's supported range, and `pygeoapi>=0.24,<0.25`. Optionally a `pygeoapi.plugins` entry point, but note that pygeoapi 0.24 loads plugins by dotted path. |
| `src/<pkg>/provider.py` | A `BaseProvider` (or `BaseEDRProvider`, …) subclass with `get_fields`, `query`, `get` stubs matching the chosen provider type |
| `src/<pkg>/descriptor.json` or `.yaml` | Provider type, fields, JSON Schema for the provider block, and template answers |
| `examples/pygeoapi-config.snippet.yml` | A ready-to-paste `resources:` block with `${ENV}` placeholders |
| `tests/contract/` | Generated contract tests: load through `pygeoapi.plugin.load_plugin`, `get_fields` types, `query` paging, bbox, datetime, `properties`, `sortby`, and `get` by id and not-found. Also an API-level smoke test through pygeoapi's API classes with the snippet config. |
| `tests/fixtures/` | Small recorded responses or a sample DB. Tests must not need the network. |
| `README.md`, `CHANGELOG.md` (Keep a Changelog), `LICENSE` | |
| CI | One workflow that runs the same `nox`/`tox` sessions on the Python versions pygeoapi 0.24 supports |

Do not copy: conda and poetry2conda, Dockerfiles pointing at internal registries, the GitLab CI tied to private runners, Travis, 100% coverage gates, or advbumpversion.

---

## 3. OGC CITE (TEAM Engine) for OGC API - Features and EDR

### 3.1 Versions and images (queried 2026-09-23)

| Image | Latest tag (= `latest`) | Pushed | Archs | Size | Previous |
|---|---|---|---|---|---|
| `ogccite/ets-ogcapi-features10` | `1.9-teamengine-6.0.0-RC2` | 2025-01-03 | **linux/amd64 only** | ~249 MB | `1.8-teamengine-5.7` (2024-11-29), `1.7.1-teamengine-5.4.1` (2024-04-19) |
| `ogccite/ets-ogcapi-edr10` | `1.3-teamengine-6.0.0-RC2` | 2025-01-07 | **linux/amd64 only** | ~266 MB | `1.2-teamengine-5.7` (2024-11-29), `1.1-teamengine-5.7` (2024-05-28) |

- The GitHub tags agree: Features `1.9` is the newest tag, and EDR `1.3` is the newest tag. The EDR "Releases" page only lists v0.0.x.
- `master` is at `1.10-SNAPSHOT` for Features and `1.4-SNAPSHOT` for EDR (`ets-ogcapi-features10:pom.xml:11`, `ets-ogcapi-edr10:pom.xml:11`). No newer image has been published since January 2025.
- The images are Tomcat 10.1 on JRE 17 with TEAM Engine. They start with `catalina.sh jpda run`, so the JPDA debugger is on (`ets-ogcapi-features10:src/docker/Dockerfile:1`, `:27-32`; `ets-ogcapi-edr10:src/docker/Dockerfile:18-30`).
- **arm64: not available.** On Apple Silicon (this machine is `arm64`), run with `--platform linux/amd64` under emulation, which is slower. Alternatively, build the image locally from the repo (`mvn clean install -Pdocker`, per `ets-ogcapi-edr10:README.adoc:46-58`), which should be possible on arm64 because the base image `tomcat:10.1-jre17` is multi-arch. I have not tested this.

### 3.2 Running headless

**Start:** `docker run -d -p 8081:8080 ogccite/ets-ogcapi-features10:1.9-teamengine-6.0.0-RC2` (`ets-ogcapi-features10:src/site/asciidoc/how-to-run-the-tests.adoc:47`). The web UI is at `http://localhost:8081/teamengine/`. The built-in user and password are **`ogctest` / `ogctest`** (`ets-ogcapi-features10:src/site/asciidoc/how-to-run-the-tests.adoc:53`).

**REST API** (TEAM Engine user guide, https://opengeospatial.github.io/teamengine/users.html):

- `GET /teamengine/rest/suites` lists the suites. `GET /teamengine/rest/suites/{etsCode}` returns a suite's documentation. `GET` or `POST /teamengine/rest/suites/{etsCode}/run?<args>` runs a suite.
- HTTP **Basic** auth is required.
- The output format depends on `Accept`:
  - `application/xml`: TestNG results XML
  - `application/rdf+xml`: **EARL** report
  - `application/zip`: zipped HTML report
- If an argument's URI contains `&` in its query, it must be percent-encoded as `%26`.

**ETS codes and arguments:**

| Suite | `etsCode` | Arguments |
|---|---|---|
| Features 1.0 | `ogcapi-features-1.0` (`ets-ogcapi-features10:pom.xml:50`) | `iut` = landing-page URL (required). `noofcollections` = collections to test, where ≤0 means all; the form default is 3 (`ets-ogcapi-features10:src/main/java/org/opengis/cite/ogcapifeatures10/TestRunArg.java:12-18`; `ets-ogcapi-features10:src/main/config/test-run-props.xml:5-6`). |
| EDR 1.0 | `ogcapi-edr10` (`ets-ogcapi-edr10:pom.xml:47`) | `iut` (required, `ets-ogcapi-edr10:src/main/java/org/opengis/cite/ogcapiedr10/SuiteFixtureListener.java:68-70`), `noofcollections` (`:85`), **`apiDefinition`** = OpenAPI document URL. Case matters: the code reads `params.get("apiDefinition")` and calls `URI.create(...)` on it (`ets-ogcapi-edr10:src/main/java/org/opengis/cite/ogcapiedr10/SuiteFixtureListener.java:98`), although the enum spells it `APIDEFINITION`, which renders as `apidefinition` (`ets-ogcapi-edr10:src/main/java/org/opengis/cite/ogcapiedr10/TestRunArg.java:20-25`). |

Example calls (untested here; they follow the documented API):

```bash
# Features: EARL output
curl -u ogctest:ogctest -H 'Accept: application/rdf+xml' \
  'http://localhost:8081/teamengine/rest/suites/ogcapi-features-1.0/run?iut=http://host.docker.internal:5000/&noofcollections=-1' \
  -o features-earl.rdf
# EDR: TestNG XML
curl -u ogctest:ogctest -H 'Accept: application/xml' \
  'http://localhost:8082/teamengine/rest/suites/ogcapi-edr10/run?iut=http://host.docker.internal:5000/&apiDefinition=http://host.docker.internal:5000/openapi&noofcollections=-1' \
  -o edr-testng.xml
```

The console alternative is the all-in-one jar: `java -jar ets-ogcapi-features10-<v>-aio.jar [-o outDir] [-h] test-run-props.xml`, which writes `testng-results.xml` (`ets-ogcapi-features10:src/site/asciidoc/how-to-run-the-tests.adoc:29-38`). The jar is not published to Docker Hub, so it has to be built with Maven.

**What gets tested** (conformance classes by TestNG test group):

- Features: Core (general, landing page, API definition, conformance, collections) and "Coordinate Reference Systems by Reference" (`ets-ogcapi-features10:src/main/resources/org/opengis/cite/ogcapifeatures10/testng.xml:14-34`).
- EDR: Preconditions, Core, Collections (with queries), JSON, GeoJSON and EDR-GeoJSON (`ets-ogcapi-edr10:src/main/resources/org/opengis/cite/ogcapiedr10/testng.xml:14-49`). Per `ets-ogcapi-edr10:teststatus.adoc`, the OAS30 abstract tests 28-33 are SKIPPED and several cube, z and trajectory tests are unassigned.

### 3.3 Gotchas

- **Reaching the host.** The docs note "HTTP calls to localhost are not working with Docker" (`ets-ogcapi-features10:src/site/asciidoc/how-to-run-the-tests.adoc:73`). Use `host.docker.internal` (Docker Desktop), `--add-host=host.docker.internal:host-gateway` (Linux), or put both containers on the same docker network and use the service name.
- **pygeoapi builds every link from `server.url`** (`pygeoapi:pygeoapi/util.py:186-189`, used as `self.base_url` at `pygeoapi:pygeoapi/api/__init__.py:528`). The `iut` host and `server.url` must therefore be the same container-reachable URL. Otherwise the ETS follows `http://localhost:5000/...` links inside its own container and fails. Our "run CITE" button must generate a temporary config with `server.url` rewritten.
- **Use an HTTP/1.1 server.** pygeoapi's own CITE notes use gunicorn "with HTTP 1.1 support" (`pygeoapi:tests/cite/README.md:26-35`).
- **Provide the OpenAPI document.** It is required for EDR (`apiDefinition`) and for Features Core "API definition". For Features, `pygeoapi openapi generate` has to have run (`pygeoapi:tests/cite/README.md:34`).
- `noofcollections` defaults to 3 in the UI form. Use `-1` or `0` to test all collections, which is slower.
- EDR prerequisites: a CRS84-capable collection, and `parameter_names` and `data_queries` in the collection metadata. See the teststatus list.
- For Features CRS tests, collections need `crs`/`storageCrs` metadata. pygeoapi's CITE config uses the TinyDB provider with sample data (`pygeoapi:tests/cite/cite.config.yml:90-136`).

### 3.4 How pygeoapi itself runs CITE

- `vendor/pygeoapi/tests/cite/` contains `cite.config.yml` with four resources: `icoads-sst` (xarray-edr), `canada-hydat-daily-mean-02hc003` (TinyDB), `lakes` (MVT-tippecanoe) and `hello-world` (process) (`pygeoapi:tests/cite/cite.config.yml:61-174`). It also has `cite.env` and a README that serves the config with gunicorn on port 5001 (`pygeoapi:tests/cite/README.md:24-35`).
- There is **no CITE job in pygeoapi's GitHub workflows**; a grep of `vendor/pygeoapi/.github/` finds nothing. Compliance is done against the public instance https://demo.pygeoapi.io/cite (`pygeoapi:docs/source/ogc-compliance.rst:10-13`) using OGC's hosted TEAM Engine. The procedure is documented on the wiki page https://github.com/geopython/pygeoapi/wiki/OGCCompliance, which the docs point to (`pygeoapi:docs/source/ogc-compliance.rst:18-19`). The wiki gives per-suite parameters (landing page, plus `/openapi` for EDR and Processes, plus Tiles parameters). It also reports that pygeoapi 0.22.0 is OGC-certified (https://portal.ogc.org/public_ogc/compliance/product.php?pid=1872). I have not verified this on the OGC portal.
- Typical runtime and known pygeoapi failures: see §3.5 (pending).

### 3.5 Runtime and known failures (pending)

_To be filled after focused searches._

---

## 4. Other tools and declarative providers

- **pygeoapi's built-in admin UI.** `/admin/config?f=html` renders the raw config as a **read-only** nested card tree (`pygeoapi:pygeoapi/templates/admin/index.html:19-59`). It has no editing, forms or validation feedback.
- **GitHub search (2026-09-23, `gh search repos`):** the queries "pygeoapi provider", "pygeoapi generic json api provider", "pygeoapi rest api provider", "pygeoapi sql query provider", "pygeoapi admin ui" and "pygeoapi config editor" found **no generic, spec-driven HTTP-JSON or SQL provider and no other config GUI**. So Mode B's spec engine has no existing implementation to reuse. The closest prior art is single-purpose providers:
  - `internetofwater/pgedr` (updated 2026-08-19): an OGC API - EDR provider backed by PostgreSQL. **Review it before designing the time series → EDR template.**
  - `waystones-nexus/pygeoapi-duckdb-geoparquet`: GeoParquet via DuckDB.
  - `Arkitektum/pygeoapi.provider.postgresql_ext`, `Arkitektum/pygeoapi.provider.mvt_postgresql_ext` and `Arkitektum/pygeoapi.provider.styles`.
  - `icisk/pygeoapi-provider-s3-geojson`.
  - Older ones: `Canadian-Geospatial-Platform/geocore-pygeoapi` (2021) and `chinedu117/opencdms-pygeoapi-provider` (2023).

  These are candidates for Mode A catalog descriptors, not for reuse as an engine.
