PYTHON ?= .venv/bin/python

.PHONY: check check-docs

## Run every check. Phase 0 has documentation checks only; later phases add
## lint, types, unit, contract, integration and E2E suites here (PLAN.md §12).
check: check-docs

## Citations: strict for curated docs, errors-only for the research appendices.
check-docs:
	$(PYTHON) tools/check_citations.py --strict docs/pygeoapi-notes.md docs/decisions.md PLAN.md
	$(PYTHON) tools/check_citations.py -q
