# Research notes (Phase 0)

The detailed evidence behind [`../pygeoapi-notes.md`](../pygeoapi-notes.md).
Research agents working in parallel against pygeoapi 0.24.0 (`vendor/pygeoapi`)
wrote these notes on 2026-09-23. Their experiments ran against the pinned
package, in the project venv or in throwaway venvs.

| Note | Covers |
|---|---|
| [config-model.md](config-model.md) | Config schema, resource ids, extents, links, i18n, env vars, `validate_config`, `openapi generate`, multiple providers, templates |
| [admin-api-and-runtime.md](admin-api-and-runtime.md) | Admin API (with round-trip experiments), server reload, the `geopython/pygeoapi:0.24.0` image |
| [provider-contract.md](provider-contract.md) | Plugin loading, base classes, errors, the exact kwargs the API passes, EDR registration, checks the harness can run |
| [providers-features-files.md](providers-features-files.md) | CSV, GeoJSON, SQLiteGPKG, OGR, Parquet, TinyDB |
| [providers-features-services.md](providers-features-services.md) | PostgreSQL, MySQL, Oracle, MongoDB, Elasticsearch, OpenSearch, ESRI, Socrata, SensorThings, ERDDAP |
| [providers-coverage-edr.md](providers-coverage-edr.md) | rasterio, xarray, xarray-edr, SensorThingsEDR, and the coverage and EDR API layers |
| [providers-tiles-maps-records-stac.md](providers-tiles-maps-records-stac.md) | MVT-\*, WMTS, WMS, MapScript, catalogue providers, STAC providers |
| [org-plugins-and-geoconnex.md](org-plugins-and-geoconnex.md) | cgs-earth `pygeoapi-plugins` 0.6.0, `pygeoapi-geoconnex-examples` |
| [prior-art.md](prior-art.md) | byteroad `pygeoapi_config`, the USGS cookiecutter, OGC CITE, other tools |

## Status and caveats

- **Citations.** Every `path:line` resolves to an existing line
  (`tools/check_citations.py`: 0 errors). About 6% are flagged by the checker's
  heuristic, which warns when the named key isn't within ±3 lines of the cited
  line. Most are citations of a line range's last line or of an error branch.
  Facts carried into `pygeoapi-notes.md` were re-verified, and that file passes
  `--strict`.
- **Pending experiments.** A usage limit interrupted the research twice. Items
  still marked *pending* in these notes:
  - the Admin API's PUT/PATCH/DELETE diffs and its Starlette/Django behaviour
  - executed checks for the tile, map, record and STAC providers (the code
    reading is complete)
  - some SQL, ESRI and plugin live checks
  - CITE runtimes

  `pygeoapi-notes.md` §12 says when each will be done.
- These notes are **reference material** for writing descriptors (Phases 1 and
  5). They are not maintained as the source of truth. When pygeoapi is bumped,
  regenerate the facts rather than editing these files.
