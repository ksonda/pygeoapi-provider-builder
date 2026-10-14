# Vendored schemas

`coveragejson.json` is the bundled JSON Schema (draft 2020-12) of the OGC
CoverageJSON Community Standard, copied unchanged from
`standard/schema/build/2020-12/coveragejson.json` in
<https://github.com/opengeospatial/CoverageJSON> at commit
`692065649ae64b9481b0744f6858a136893198ec` (pinned in `tools/vendor.txt`).

Copyright the Open Geospatial Consortium; licensed under the Apache License,
Version 2.0 (`vendor/CoverageJSON/LICENSE`). `harness/tests/test_coveragejson.py`
checks that the copy still matches the pinned source.

pygeoapi never validates the CoverageJSON its EDR providers return
(`docs/pygeoapi-notes.md` §5.2), so the contract harness brings this schema
(PLAN.md risk R7).
