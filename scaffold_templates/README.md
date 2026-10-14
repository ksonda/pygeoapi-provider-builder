# Scaffold templates (J5, PLAN.md §7.5)

Jinja2 templates for code-built providers, rendered by
`backend/provider_builder/scaffold.py`:

- `provider/<type>.py.j2`: the provider module for each provider type. Method
  signatures and docstrings come from pygeoapi's installed code (the base
  classes, and the core providers whose signatures the API calls), passed in
  as `methods`.
- `package/`: the rest of the package (pyproject, README, CHANGELOG, tests, the
  descriptor, an example config, a test Dockerfile).

Values reach Python only through the `py` filter (`repr`), YAML and TOML only
through `json` (a JSON string is a valid YAML and TOML string), and docstrings
through `doc`, which escapes quotes and backslashes. Identifiers are checked
before rendering.

## Eject (PLAN.md §7.6, ADR-0021)

`eject/` holds the templates `backend/provider_builder/eject.py` renders when
a template-built provider becomes code:

- `http_features.py.j2`, `sql_features.py.j2`, `timeseries_edr.py.j2`: one
  module per spec template, with the spec's settings as constants and only
  the code paths the spec uses
- `support.py`, `http_client.py`, `database.py`, `jsonpaths.py`: plain modules
  copied as they are, only the ones a spec needs
- `pyproject.toml.j2`, `README.md.j2`, `CHANGELOG.md.j2`, `init.py.j2`,
  `test_contract.py.j2`: the rest of the package

The eject environment is stricter than the scaffold's. Its `finalize` refuses
any output that the generator didn't build as `Code`, so every spec value goes
through `literal()` (a `repr` that `ast.literal_eval` reads back) or another
checked builder.
