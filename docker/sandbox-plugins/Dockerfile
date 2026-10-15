# The sandbox image plus cgs-earth's pygeoapi-plugins, to test org plugins
# (PLAN.md §5.4: "needs a derived image with pygeoapi-plugins"). make
# images-plugins builds it; point sandbox.image at it in provider-builder.yaml.
# A production image needs only the RUN line, FROM geopython/pygeoapi:0.24.0.
ARG BASE=provider-builder/sandbox:0.24.0
FROM ${BASE}
USER root
RUN /venv/bin/python3 -m pip install --no-cache-dir pygeoapi-plugins==0.6.0
USER 10001:0
