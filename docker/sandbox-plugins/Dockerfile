# The sandbox image plus cgs-earth's pygeoapi-plugins, to test org plugins
# (PLAN.md §5.4: "needs a derived image with pygeoapi-plugins"). make
# images-plugins builds it; point sandbox.image at it in provider-builder.yaml.
# A production image needs only the RUN line, FROM geopython/pygeoapi:0.24.0
# (the catalog card's Dockerfile; backend/tests/test_catalog.py keeps them equal).
ARG BASE=provider-builder/sandbox:0.24.0
FROM ${BASE}
USER root
# Hold pip to the image's own versions (but shapely): its GDAL, rasterio and netCDF4
# need its numpy 1.26, which newer pyarrow wheels can't use.
RUN /venv/bin/python3 -m pip list --format=freeze | grep -viE '^(shapely)==' > /tmp/image.txt \
 && printf '%s\n' 'pyarrow<26' >> /tmp/image.txt \
 && /venv/bin/python3 -m pip install --no-cache-dir -c /tmp/image.txt pygeoapi-plugins==0.6.0 \
 && rm /tmp/image.txt
USER 10001:0
