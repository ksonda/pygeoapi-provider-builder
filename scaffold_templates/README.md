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
