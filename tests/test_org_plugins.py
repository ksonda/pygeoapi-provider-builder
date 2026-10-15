"""Org plugins: pygeoapi-plugins 0.6.0 on pygeoapi 0.24.0, in the derived image (PLAN.md §5.4).

The catalog's verdicts are pinned here: each test runs the builder's own probe
in ``provider-builder/sandbox-plugins:0.24.0`` (``make images-plugins``), so a
plugin release that fixes or breaks one shows up as a failure to look at. The
PostGIS-backed ones need the dev stack.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from dockerutil import NETWORK, REPO, copy_fixtures
from forms import CASES
from provider_builder.render import FormState
from provider_builder.runner import DockerRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target, Workspace

PLUGINS_IMAGE = os.environ.get('PB_PLUGINS_IMAGE', 'provider-builder/sandbox-plugins:0.24.0')
BASE = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'
SECRETS = {'PYGEOAPI_WATERWAYS_DB_PASSWORD': 'postgres'}


@pytest.fixture
def service(tmp_path: Path) -> Service:
    copy_fixtures(tmp_path / 'data')
    sitemaps = tmp_path / 'data' / 'sitemaps'
    sitemaps.mkdir()
    (sitemaps / '_sitemap.xml').write_text(
        '<urlset><url><loc>https://geoconnex.us/ref/hu02/07</loc></url></urlset>'
    )
    (tmp_path / 'config').mkdir()
    shutil.copy(BASE, tmp_path / 'config' / 'pygeoapi.config.yml')
    settings = Settings(
        root=tmp_path,
        data_roots=[DataRoot(name='d', host_path=tmp_path / 'data', server_path='/data')],
        targets=[
            Target(id='t', title='t', base_config=tmp_path / 'config' / 'pygeoapi.config.yml')
        ],
        catalogs=[REPO / 'catalog'],
        sandbox=SandboxSettings(
            runner='docker', image=PLUGINS_IMAGE, egress_network=NETWORK, private_hosts=['postgis']
        ),
        workspace=Workspace(host_path=tmp_path / 'providers', server_path='/specs'),
    )
    return Service(
        settings, runner=DockerRunner(settings.sandbox, settings.data_roots, settings.workspace)
    )


def probe(service: Service, name: str) -> dict:
    return service.probe(FormState.model_validate(CASES[name]()), 0, SECRETS)


def failure(result: dict) -> str:
    return str((result.get('error') or {}).get('message', ''))


@pytest.mark.docker
def test_the_derived_image_has_the_plugins(service: Service) -> None:
    service.check_sandbox()
    cards = {c['id']: c for c in service.cards()}
    assert cards['pygeoapi-plugins/GeoPandas']['availability']['status'] == 'ready'
    assert cards['Parquet']['availability']['status'] == 'ready'  # geopandas pulls pyarrow


@pytest.mark.docker
@pytest.mark.parametrize('name', ['rasterio', 'xarray', 'xarray-edr', 'ogr-gpkg'])
def test_the_derived_image_keeps_the_core_providers(service: Service, name: str) -> None:
    """pip only added packages: the image's rasterio, netCDF4 and GDAL still have their numpy."""
    result = probe(service, name)
    assert result['ok'], (result.get('explanation'), failure(result))


@pytest.mark.docker
def test_geopandas_points_work_and_other_geometries_are_malformed(service: Service) -> None:
    result = probe(service, 'org-geopandas')
    assert result['ok'], result.get('explanation')
    feature = result['result']['sample']['features'][0]
    assert feature['geometry']['type'] == 'Point'
    lakes = CASES['org-geopandas']()
    lakes['providers'][0]['values'] = {'data': '/data/ne_110m_lakes.geojson', 'id_field': 'id'}
    out = service.probe(FormState.model_validate(lakes), 0, {})
    assert out['ok'], out.get('explanation')
    # The verdict: a polygon's coordinates come back as a JSON string.
    assert isinstance(out['result']['sample']['features'][0]['geometry']['coordinates'], str)


@pytest.mark.docker
def test_filesystem_xml_lists_sitemaps(service: Service) -> None:
    result = probe(service, 'org-filesystem-xml')
    assert result['ok'], result.get('explanation')
    assert result['result']['stac']['links'] > 0


@pytest.mark.docker
def test_sparql_passes_items_through(service: Service) -> None:
    """/items reaches the wrapped CSV provider; only single items would query SPARQL."""
    result = probe(service, 'org-sparql')
    assert result['ok'], result.get('explanation')
    assert result['result']['sample']['features']


@pytest.mark.compose
def test_pseudo_postgresql_is_broken_on_0_24(service: Service) -> None:
    result = probe(service, 'org-pseudo-postgresql')
    assert not result['ok'] and '_get_crs_transform' in failure(result), (
        'fixed? update the verdict'
    )


@pytest.mark.compose
@pytest.mark.parametrize('name', ['org-mvt-postgresql', 'org-mvt-cache-fs'])
def test_mvt_plugins_serve_tiles(service: Service, name: str) -> None:
    result = probe(service, name)
    assert result['ok'], (result.get('explanation'), failure(result))
    assert any(result['result']['preview']['tiles'].values())
