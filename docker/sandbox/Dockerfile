# The Provider Builder sandbox and dev-stack image (PLAN.md §8.2, ADR-0008):
# the pinned pygeoapi image plus the spec engine's dependencies. The harness and
# runtime sources are mounted read-only at run time (/opt/harness, /opt/runtime),
# so code changes don't need a rebuild; this file and requirements.txt do, and the
# image tag is their content hash (backend/provider_builder/images.py).
FROM geopython/pygeoapi:0.24.0
COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/python3 -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
# Sandbox defaults. The dev stack's pygeoapi overrides both (root, /entrypoint.sh).
USER 10001:0
ENTRYPOINT ["/venv/bin/python3", "-m", "pygeoapi_provider_harness.worker"]
