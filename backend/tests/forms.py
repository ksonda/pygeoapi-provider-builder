"""Form-state builders shared by the unit, contract and integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
# A pristine copy of the dev stack's config: demos edit the real one (README, Quickstart).
BASE_CONFIG = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'


def csv_form(**overrides: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        'resource_id': 'obs',
        'resource': {
            'title': 'Observations',
            'description': 'Weather station observations',
            'keywords': ['weather', 'observations'],
            'extents': {
                'spatial': {'bbox': [-122, 43, -75, 49]},
                'temporal': {'begin': '2000-10-30T18:24:39Z', 'end': None},
            },
        },
        'providers': [
            {
                'descriptor': 'CSV',
                'values': {
                    'data': '/data/obs.csv',
                    'id_field': 'id',
                    'geometry.x_field': 'long',
                    'geometry.y_field': 'lat',
                    'time_field': 'datetime',
                },
            }
        ],
    }
    form.update(overrides)
    return form


def postgres_form() -> dict[str, Any]:
    return {
        'resource_id': 'waterways',
        'resource': {
            'title': 'Waterways of Burundi',
            'description': 'HOT OSM waterways',
            'keywords': ['water'],
            'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
        },
        'providers': [
            {
                'descriptor': 'PostgreSQL',
                'variant': 'fields',
                'values': {
                    'data.host': 'postgis',
                    'data.dbname': 'test',
                    'data.user': 'postgres',
                    'data.search_path': ['osm', 'public'],
                    'table': 'hotosm_bdi_waterways',
                    'id_field': 'osm_id',
                    'geom_field': 'foo_geom',
                },
                'secrets': {'data.password': {'env': 'PYGEOAPI_WATERWAYS_DB_PASSWORD'}},
            }
        ],
    }


def gpkg_form() -> dict:
    return {
        'resource_id': 'poi-portugal',
        'resource': {
            'title': 'Points of interest in Portugal',
            'description': 'OSM POIs',
            'keywords': ['poi'],
            'extents': {'spatial': {'bbox': [-9.5, 36.9, -6.2, 42.2]}},
        },
        'providers': [
            {
                'descriptor': 'SQLiteGPKG',
                'values': {
                    'data': '/data/poi_portugal.gpkg',
                    'table': 'poi_portugal',
                    'id_field': 'osm_id',
                    'title_field': 'name',
                },
            }
        ],
    }


def geojson_form() -> dict:
    return {
        'resource_id': 'lakes2',
        'resource': {
            'title': {'en': 'Large lakes', 'fr': 'Grands lacs'},
            'description': 'Natural Earth lakes',
            'keywords': ['lakes', 'yes', '2020'],
            'links': [
                {
                    'type': 'text/html',
                    'rel': 'canonical',
                    'title': 'Natural Earth',
                    'href': 'https://www.naturalearthdata.com/',
                }
            ],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
        },
        'providers': [
            {
                'descriptor': 'GeoJSON',
                'values': {
                    'data': '/data/ne_110m_lakes.geojson',
                    'id_field': 'id',
                    'properties': ['name', 'scalerank'],
                    'count': True,
                },
            }
        ],
    }


def postgres_dsn_form() -> dict:
    return {
        'resource_id': 'drains',
        'resource': {
            'title': 'Drains',
            'description': 'A materialized view',
            'keywords': ['water'],
            'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
        },
        'providers': [
            {
                'descriptor': 'PostgreSQL',
                'variant': 'dsn',
                'values': {
                    'options.search_path': ['osm'],
                    'table': 'hotosm_bdi_drains',
                    'id_field': 'osm_id',
                    'geom_field': 'foo_geom',
                    'options.connect_timeout': 10,
                },
                'secrets': {'data': {'env': 'PYGEOAPI_DRAINS_DB_DSN'}},
            }
        ],
    }


def _resource(rid: str, title: str, bbox: list[float], **more: Any) -> dict[str, Any]:
    return {
        'resource_id': rid,
        'resource': {
            'title': title,
            'description': f'{title} (pygeoapi test data)',
            'keywords': ['test'],
            'extents': {'spatial': {'bbox': bbox}, **more},
        },
    }


def provider(descriptor: str, values: dict[str, Any], **more: Any) -> dict[str, Any]:
    return {'providers': [{'descriptor': descriptor, 'values': values, **more}]}


DUTCH = [5.67, 52.04, 5.83, 52.13]  # Dutch addresses around Otterlo
WORLD = [-180.0, -90.0, 180.0, 90.0]
#: The mock API as the sandbox and the dev stack's pygeoapi reach it.
MOCK = 'http://mockapi:8000'


def ogr_gpkg_form() -> dict[str, Any]:
    return _resource('addresses', 'Dutch addresses', DUTCH) | provider(
        'OGR',
        {
            'data.source_type': 'GPKG',
            'data.source': '/data/dutch_addresses_4326.gpkg',
            'layer': 'OGRGeoJSON',
            'id_field': 'fid',
        },
        variant='file',
    )


def ogr_shapefile_form() -> dict[str, Any]:
    return _resource('addresses-shp', 'Dutch addresses (Shapefile)', DUTCH) | provider(
        'OGR',
        {
            'data.source_type': 'ESRI Shapefile',
            'data.source': '/data/dutch_addresses_shape_4326/inspireadressen.shp',
            'layer': 'inspireadressen',
            'id_field': 'fid',
            'data.source_options': {'ADJUST_GEOM_TYPE': 'FIRST_SHAPE'},
            'data.gdal_ogr_options': {'SHPT': 'POINT'},
        },
        variant='file',
    )


def esri_form() -> dict[str, Any]:
    return _resource('esri-wells', 'Wells from ArcGIS', [-117.2, 42.0, -111.1, 48.9]) | provider(
        'ESRI',
        {
            'data': f'{MOCK}/arcgis/rest/services/Wells/FeatureServer/0',
            'id_field': 'OBJECTID',
            'time_field': 'measured',
            'title_field': 'well_name',
        },
    )


def esri_login_form() -> dict[str, Any]:
    form = _resource('secure-wells', 'Wells behind an ArcGIS login', [-117.2, 42.0, -111.1, 48.9])
    return form | provider(
        'ESRI',
        {
            'data': f'{MOCK}/arcgis/rest/services/SecureWells/FeatureServer/0',
            'id_field': 'OBJECTID',
            'username': 'reader',
            'token_service': f'{MOCK}/arcgis/sharing/rest/generateToken',
            'referer': MOCK,
        },
        secrets={'password': {'env': 'PYGEOAPI_SECURE_WELLS_ESRI_PASSWORD'}},
    )


def rasterio_form() -> dict[str, Any]:
    return _resource('cmc-temperature', 'CMC 2 m air temperature', WORLD) | provider(
        'rasterio',
        {
            'data': '/data/CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2',
            'format.name': 'GRIB',
            'format.mimetype': 'application/x-grib2',
            'options': {'DATA_ENCODING': 'SIMPLE_PACKING'},  # COMPLEX_PACKING can't write subsets
        },
    )


def xarray_form() -> dict[str, Any]:
    temporal = {'temporal': {'begin': '2005-12-01T09:00:00Z', 'end': '2006-01-01T09:00:00Z'}}
    return _resource(
        'analysed-sst', 'Analysed sea surface temperature', [-28, 40, -27, 41], **temporal
    ) | provider(
        'xarray',
        {
            'data': '/data/analysed_sst.zarr',
            'format.name': 'zarr',
            'format.mimetype': 'application/zip',
            'options.squeeze': True,
        },
    )


def xarray_edr_form() -> dict[str, Any]:
    temporal = {'temporal': {'begin': '2000-01-16T06:00:00Z', 'end': '2000-12-16T01:20:06Z'}}
    return _resource('coads-sst', 'COADS sea surface temperature', WORLD, **temporal) | provider(
        'xarray-edr', {'data': '/data/coads_sst.nc'}
    )


def tippecanoe_form() -> dict[str, Any]:
    return _resource('lakes-tiles', 'Lakes as vector tiles', WORLD) | provider(
        'MVT-tippecanoe',
        {
            'data': '/data/tiles/ne_110m_lakes',
            'options.zoom.min': 0,
            'options.zoom.max': 5,
            'format.name': 'pbf',
            'format.mimetype': 'application/vnd.mapbox-vector-tile',
        },
        variant='folder',
    )


def mvt_proxy_form() -> dict[str, Any]:
    return _resource('lakes-proxy', 'Lakes from a tile server', WORLD) | provider(
        'MVT-proxy',
        {
            'data': MOCK + '/tiles/ne_110m_lakes/{z}/{x}/{y}.pbf',
            'options.zoom.min': 0,
            'options.zoom.max': 5,
            'options.schemes': ['WebMercatorQuad'],
            'format.name': 'pbf',
            'format.mimetype': 'application/vnd.mapbox-vector-tile',
        },
    )


def wms_form() -> dict[str, Any]:
    return _resource('wells-map', 'Wells as a map', [-118, 41, -110, 49.5]) | provider(
        'WMSFacade',
        {
            'data': f'{MOCK}/wms',
            'options.layer': 'wells,graticule',
            'format.name': 'png',
            'format.mimetype': 'image/png',
        },
    )


def tinydb_form() -> dict[str, Any]:
    return _resource(
        'canada-records', 'Canadian open data records', [-142, 41, -52, 84]
    ) | provider(
        'TinyDBCatalogue',
        {
            'data': '/data/canada-records.tinydb',
            'id_field': 'externalId',
            'time_field': 'created',
            'title_field': 'title',
        },
    )


# --- Tier 2: golden renders and schema checks; integration where a fixture is cheap ---


def tier2(
    descriptor: str, rid: str, values: dict[str, Any], bbox: list[float] = WORLD, **more: Any
) -> dict[str, Any]:
    return _resource(rid, f'{descriptor} test', bbox) | provider(descriptor, values, **more)


TIER2: dict[str, Any] = {
    'tinydb': lambda: tier2(
        'TinyDB',
        'hydat',
        {
            'data': '/data/canada-hydat-daily-mean-02HC003.tinydb',
            'id_field': 'IDENTIFIER',
            'time_field': 'DATE',
        },
        [-79.6, 43.6, -79.4, 43.8],
    ),
    'parquet': lambda: tier2(
        'Parquet',
        'parquet-random',
        {
            'data.source': '/data/random.parquet',
            'id_field': 'id',
            'x_field': '[minlon, maxlon]',
            'y_field': '[minlat, maxlat]',
            'data.batch_size': 10000,
        },
    ),
    'erddaptabledap': lambda: tier2(
        'ERDDAPTabledap',
        'osmc-points',
        {
            'data': 'https://osmc.noaa.gov/erddap/tabledap/OSMC_Points',
            'id_field': 'PLATFORM_CODE',
            'time_field': 'time',
            'options.max_age_hours': 12,
        },
    ),
    'socrata': lambda: tier2(
        'Socrata',
        'earthquakes',
        {
            'data': 'https://soda.demo.socrata.com/',
            'resource_id': 'emdb-u46w',
            'id_field': 'earthquake_id',
            'geom_field': 'location',
            'time_field': 'datetime',
        },
        secrets={'token': {'env': 'PYGEOAPI_EARTHQUAKES_SOCRATA_TOKEN'}},
    ),
    'sensorthings': lambda: tier2(
        'SensorThings',
        'datastreams',
        {
            'data': 'https://example.org/FROST-Server/v1.1/',
            'entity': 'Datastreams',
            'time_field': 'phenomenonTime',
            'intralink': True,
        },
    ),
    'sensorthingsedr': lambda: tier2(
        'SensorThingsEDR', 'sta-edr', {'data': 'https://example.org/FROST-Server/v1.1/'}
    ),
    'elasticsearch': lambda: tier2(
        'Elasticsearch',
        'places-es',
        {'id_field': 'geonameid', 'time_field': 'datetimefield'},
        variant='secret',
        secrets={'data': {'env': 'PYGEOAPI_PLACES_ES_INDEX_URL'}},
    ),
    'elasticsearchcatalogue': lambda: tier2(
        'ElasticsearchCatalogue',
        'metadata-es',
        {'data': 'http://search.example.org:9200/metadata', 'id_field': 'identifier'},
        variant='url',
    ),
    'opensearch': lambda: tier2(
        'OpenSearch',
        'places-os',
        {'data': 'http://search.example.org:9200/places', 'id_field': 'geonameid'},
        variant='url',
    ),
    'mongodb': lambda: tier2(
        'MongoDB',
        'places-mongo',
        {'collection': 'places'},
        variant='secret',
        secrets={'data': {'env': 'PYGEOAPI_PLACES_MONGO_MONGODB_URL'}},
    ),
    'mysql': lambda: tier2(
        'MySQL',
        'mysql-locations',
        {
            'data.host': 'mysql.example.org',
            'data.dbname': 'geo',
            'data.user': 'reader',
            'data.search_path': ['geo'],
            'table': 'location',
            'id_field': 'locationID',
            'geom_field': 'locationCoordinates',
        },
        secrets={'data.password': {'env': 'PYGEOAPI_MYSQL_LOCATIONS_DB_PASSWORD'}},
    ),
    'oracledb': lambda: tier2(
        'OracleDB',
        'lakes-oracle',
        {
            'data.host': 'oracle.example.org',
            'data.service_name': 'XEPDB1',
            'data.user': 'geo',
            'table': 'lakes',
            'id_field': 'id',
            'geom_field': 'geometry',
            'title_field': 'name',
            'sdo_operator': 'sdo_relate',
            'mandatory_properties': ['group_id'],
        },
        variant='service',
        secrets={'data.password': {'env': 'PYGEOAPI_LAKES_ORACLE_DB_PASSWORD'}},
    ),
    'cswfacade': lambda: tier2(
        'CSWFacade',
        'pycsw-demo',
        {
            'data': 'https://demo.pycsw.org/cite/csw',
            'id_field': 'identifier',
            'title_field': 'title',
        },
    ),
    'filesystem': lambda: tier2(
        'FileSystem', 'data-folder', {'data': '/data', 'file_types': ['.grib2', '.nc', '.gpkg']}
    ),
    'hateoas': lambda: tier2('Hateoas', 'stac-demo', {'data': '/data/stac-catalog'}),
    'azureblobstorage': lambda: tier2(
        'AzureBlobStorage', 'azure-gribs', {'data': 'gribs', 'file_types': ['.grib2']}
    ),
    'mvt-elastic': lambda: tier2(
        'MVT-elastic',
        'places-es-tiles',
        {
            'data': 'http://search.example.org:9200/places/_mvt/geometry/{z}/{x}/{y}?grid_precision=0',
            'options.zoom.min': 0,
            'options.zoom.max': 5,
            'format.name': 'pbf',
            'format.mimetype': 'application/vnd.mapbox-vector-tile',
        },
    ),
    'mvt-postgresql': lambda: tier2(
        'MVT-postgresql',
        'waterway-tiles',
        {
            'data.host': 'postgis',
            'data.dbname': 'test',
            'data.user': 'postgres',
            'data.search_path': ['osm', 'public'],
            'table': 'hotosm_bdi_waterways',
            'id_field': 'osm_id',
            'geom_field': 'foo_geom',
            'storage_crs': 'http://www.opengis.net/def/crs/EPSG/0/4326',
            'options.zoom.min': 0,
            'options.zoom.max': 15,
            'format.name': 'pbf',
            'format.mimetype': 'application/vnd.mapbox-vector-tile',
        },
        [29.0, -4.5, 30.9, -2.3],
        variant='fields',
        secrets={'data.password': {'env': 'PYGEOAPI_WATERWAYS_DB_PASSWORD'}},
    ),
    'wmtsfacade': lambda: tier2(
        'WMTSFacade',
        'wmts-grid',
        {
            'data': 'https://wmts.example.org/geoserver/gwc/service/wmts',
            'options.wmts_layer': 'camb:grid',
            'options.wmts_tile_matrix_set': 'WebMercatorQuad',
            'options.scheme': 'WebMercatorQuad',
            'options.zoom.min': 0,
            'options.zoom.max': 20,
            'format.name': 'png',
            'format.mimetype': 'image/png',
        },
    ),
    'mapscript': lambda: tier2(
        'MapScript',
        'addresses-map',
        {
            'data': '/data/dutch_addresses_shape_4326/inspireadressen.shp',
            'options.type': 'MS_LAYER_POINT',
            'options.layer': 'inspireadressen',
            'format.name': 'png',
            'format.mimetype': 'image/png',
        },
        DUTCH,
    ),
}

# --- Org plugins (pygeoapi-plugins 0.6.0): golden renders; verdicts are pinned in Docker ---

PG_FIELDS = {
    'data.host': 'postgis',
    'data.dbname': 'test',
    'data.user': 'postgres',
    'data.search_path': ['osm', 'public'],
    'table': 'hotosm_bdi_waterways',
    'id_field': 'osm_id',
    'geom_field': 'foo_geom',
}
PG_SECRET = {'data.password': {'env': 'PYGEOAPI_WATERWAYS_DB_PASSWORD'}}
MVT_PLUGIN = PG_FIELDS | {
    'storage_crs': 'http://www.opengis.net/def/crs/EPSG/0/4326',
    'options.zoom.min': 8,  # the plugin thins out small features at low zooms
    'options.zoom.max': 15,
    'format.name': 'pbf',
    'format.mimetype': 'application/vnd.mapbox-vector-tile',
}
BURUNDI = [29.0, -4.5, 30.9, -2.3]

ORG: dict[str, Any] = {
    'org-ckan': lambda: tier2(
        'pygeoapi-plugins/CKAN',
        'nm-reservoirs',
        {
            'data': 'https://catalog.newmexicowaterdata.org/api/3/action/datastore_search',
            'resource_id': '08369d21-520b-439e-97e3-5ecb50737887',
            'id_field': '_id',
            'x_field': 'LONDD',
            'y_field': 'LATDD',
        },
    ),
    'org-geopandas': lambda: tier2(
        'pygeoapi-plugins/GeoPandas',
        'obs-geopandas',
        {
            'data': '/data/obs.csv',
            'id_field': 'id',
            'geometry.x_field': 'long',
            'geometry.y_field': 'lat',
        },
        [-122, 43, -75, 49],
    ),
    'org-sparql': lambda: tier2(
        'pygeoapi-plugins/SPARQL',
        'obs-sparql',
        {
            'sparql_provider': 'CSV',
            'data': '/data/obs.csv',
            'id_field': 'id',
            'geometry.x_field': 'long',
            'geometry.y_field': 'lat',
            'sparql_query.endpoint': 'https://graph.geoconnex.us/repositories/iow',
            'sparql_query.bind.name': 'uri',
            'sparql_query.bind.variable': '?subject',
            'sparql_query.where': '[{predicate: "schema:name", object: "?name"}]',
            'sparql_query.prefixes': {'schema': '<https://schema.org/>'},
        },
        [-122, 43, -75, 49],
    ),
    'org-pseudo-postgresql': lambda: tier2(
        'pygeoapi-plugins/PsuedoPostgreSQL',
        'waterways-pseudo',
        PG_FIELDS,
        BURUNDI,
        variant='fields',
        secrets=PG_SECRET,
    ),  # fmt: skip
    'org-mvt-postgresql': lambda: tier2(
        'pygeoapi-plugins/MVT-PostgreSQL',
        'waterways-thinned',
        MVT_PLUGIN | {'disable_at_z': 10, 'min_pixel': 256},
        BURUNDI,
        variant='fields',
        secrets=PG_SECRET,
    ),
    'org-mvt-cache-fs': lambda: tier2(
        'pygeoapi-plugins/MVT-PostgresCacheFS',
        'waterways-cached',
        MVT_PLUGIN | {'cache_directory': '/tmp/pb-mvt-cache'},
        BURUNDI,
        variant='fields',
        secrets=PG_SECRET,
    ),
    'org-mvt-cache-postgres': lambda: tier2(
        'pygeoapi-plugins/MVT-PostgresCache',
        'waterways-db-cached',
        MVT_PLUGIN | {'mvt_table': 'pb_waterways_mvt'},
        BURUNDI,
        variant='fields',
        secrets=PG_SECRET,
    ),
    'org-mvt-cache-redis': lambda: tier2(
        'pygeoapi-plugins/MVTPostgresRedis',
        'waterways-redis',
        MVT_PLUGIN | {'redis_host': 'redis'},
        BURUNDI,
        variant='fields',
        secrets=PG_SECRET,
    ),
    'org-filesystem-xml': lambda: tier2(
        'pygeoapi-plugins/FileSystemXML',
        'sitemaps',
        {'data': '/data/sitemaps', 'file_types': ['.xml']},
    ),
}

CASES = {
    'csv': csv_form,
    'geojson': geojson_form,
    'sqlitegpkg': gpkg_form,
    'postgresql-fields': postgres_form,
    'postgresql-dsn': postgres_dsn_form,
    'ogr-gpkg': ogr_gpkg_form,
    'ogr-shapefile': ogr_shapefile_form,
    'esri': esri_form,
    'esri-login': esri_login_form,
    'rasterio': rasterio_form,
    'xarray': xarray_form,
    'xarray-edr': xarray_edr_form,
    'mvt-tippecanoe': tippecanoe_form,
    'mvt-proxy': mvt_proxy_form,
    'wmsfacade': wms_form,
    'tinydbcatalogue': tinydb_form,
    **TIER2,
    **ORG,
}
#: Cases whose data is a file in the test data (no network to load): Tier 1, and the
#: Tier-2 ones whose libraries the pinned image has.
FILE_CASES = ['csv', 'geojson', 'sqlitegpkg', 'ogr-gpkg', 'ogr-shapefile', 'rasterio', 'xarray',
              'xarray-edr', 'mvt-tippecanoe', 'tinydbcatalogue', 'tinydb', 'filesystem', 'hateoas',
              'mapscript']  # fmt: skip
#: Tier-1 cases that load from the mock API on the dev stack's network.
MOCK_CASES = ['esri', 'esri-login', 'mvt-proxy', 'wmsfacade']
