"""Constants and fixture data for the Docker-based suites."""

from __future__ import annotations

import shutil
from pathlib import Path

from provider_builder.images import sandbox_tag

REPO = Path(__file__).resolve().parents[1]
IMAGE = 'geopython/pygeoapi:0.24.0'
SANDBOX = sandbox_tag()  # make images
NETWORK = 'provider-builder-dev'
DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
#: The data the form cases read, from pygeoapi's test data (make fixtures copies the same).
FILES = {
    'obs.csv': 'obs.csv',
    'ne_110m_lakes.geojson': 'ne_110m_lakes.geojson',
    'poi_portugal.gpkg': 'poi_portugal.gpkg',
    'dutch_addresses_4326.gpkg': 'dutch_addresses_4326.gpkg',
    'CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2': (
        'CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2'
    ),
    'coads_sst.nc': 'coads_sst.nc',
    'canada-records.tinydb': 'open.canada.ca/sample-records.tinydb',
    'canada-hydat-daily-mean-02HC003.tinydb': 'canada-hydat-daily-mean-02HC003.tinydb',
}
FOLDERS = ['analysed_sst.zarr', 'tiles', 'dutch_addresses_shape_4326']
#: A tiny static STAC catalog for the Hateoas provider (pygeoapi ships none).
STAC_CATALOG = REPO / 'backend' / 'tests' / 'fixtures' / 'stac-catalog'


def copy_fixtures(target: Path) -> None:
    """The form cases' data in a folder the containers mount at /data.

    pygeoapi's GeoPackage support writes when it loads (SpatiaLite's AutoGPKGStart
    creates virtual tables), and containers run as the sandbox user 10001. Linux
    enforces the host's ownership on bind mounts; Docker Desktop doesn't.
    """
    target.mkdir(exist_ok=True)
    for name, source in FILES.items():
        shutil.copy(DATA / source, target / name)
        (target / name).chmod(0o666)
    for name in FOLDERS:
        shutil.copytree(DATA / name, target / name)
    shutil.copytree(STAC_CATALOG, target / 'stac-catalog')
    target.chmod(0o777)
