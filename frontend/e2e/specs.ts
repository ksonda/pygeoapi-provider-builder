// Specs the E2E tests save through the API: the dev stack's mock API (mockapi:8000).

const MOCK = 'http://mockapi:8000'

export function gauges(id: string) {
  return {
    spec_version: 1,
    template: 'timeseries-edr',
    id,
    title: `E2E gauges ${id}`,
    locations: {
      from: 'http',
      http: {
        source: { url: `${MOCK}/coops/mdapi/prod/webapi/stations.json`, query: { type: 'waterlevels' } },
        records: '$.stations',
        item_id: { from: 'id' },
        geometry: { type: 'point', x: 'lng', y: 'lat' },
        properties: [{ name: 'name', type: 'string' }],
        title_field: 'name',
      },
    },
    parameters: [{ id: 'water_level', label: 'Water level', unit: { symbol: 'm' } }],
    observations: {
      from: 'http',
      http: {
        url: `${MOCK}/coops/api/prod/datagetter`,
        query: {
          product: '{parameter}',
          station: '{location_id}',
          begin_date: '{start:%Y%m%d %H:%M}',
          end_date: '{end:%Y%m%d %H:%M}',
          datum: 'MLLW',
          units: 'metric',
          time_zone: 'gmt',
          format: 'json',
        },
        records: '$.data',
        error_path: '$.error',
        time: { from: 't', format: '%Y-%m-%d %H:%M' },
        value: { from: 'v' },
      },
      default_window: 'P1D',
      max_window: 'P31D',
    },
    position: { snap_radius_km: 5 },
  }
}
