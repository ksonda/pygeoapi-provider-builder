// Test-step previews for providers that don't list items: a coverage window, a
// map image, vector or image tiles, and an EDR query. Everything shown comes
// from the sandbox probe, which asked the provider what pygeoapi's API asks it.
import { Map as MlMap, NavigationControl, type LayerSpecification, type StyleSpecification } from 'maplibre-gl'
import { useEffect, useRef } from 'react'

import { registerTiles, unregisterTiles } from '../components/maplibre'
import type { CoverageSummary, ImagePreview, ProbeData, TilesPreview } from '../api/types'

const LAYER_COLOURS = ['#0f766e', '#7c3aed', '#c2410c', '#1d4ed8', '#be185d', '#4d7c0f']

function fmt(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)))
  if (v === null || v === undefined || v === '') return '–'
  return String(v)
}

function bboxText(b: number[] | null | undefined): string {
  return b?.length === 4 ? b.map(fmt).join(', ') : '–'
}

export function Problems({ title, items }: { title: string; items: string[] | undefined }) {
  if (!items?.length) return null
  return (
    <div className="notice warning">
      <p>
        <strong>{title}</strong>
      </p>
      <ul>
        {items.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  )
}

function Legend({ img }: { img: ImagePreview }) {
  const stops = (img.ramp ?? []).map((c, i, all) => `rgb(${c.join(',')}) ${Math.round((i / Math.max(1, all.length - 1)) * 100)}%`)
  return (
    <div className="legend" aria-label={`Colour scale from ${fmt(img.min)} to ${fmt(img.max)} ${img.unit ?? ''}`}>
      <div className="legend-bar" style={{ background: `linear-gradient(to right, ${stops.join(', ')})` }} />
      <div className="legend-labels small">
        <span>{fmt(img.min)}</span>
        <span>{img.unit ?? ''}</span>
        <span>{fmt(img.max)}</span>
      </div>
    </div>
  )
}

function DomainTable({ domain, caption }: { domain: CoverageSummary | null | undefined; caption: string }) {
  if (!domain) return null
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="compact">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Axis</th>
            <th scope="col">Values</th>
            <th scope="col">From</th>
            <th scope="col">To</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(domain.axes).map(([name, a]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td>{fmt(a.count)}</td>
              <td className="mono">{fmt(a.first)}</td>
              <td className="mono">{fmt(a.last)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Bands or variables: the fields a coverage or EDR collection offers, with their units. */
export function ParameterTable({ r, caption }: { r: ProbeData; caption: string }) {
  const entries = Object.entries(r.fields)
  if (!entries.length) return null
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="compact">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Title</th>
            <th scope="col">Type</th>
            <th scope="col">Unit</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, f]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td>{fmt(f.title)}</td>
              <td>{f.type}</td>
              <td>{fmt(f['x-ogc-unit'])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function CoveragePreview({ r }: { r: ProbeData }) {
  const c = r.coverage
  const img = r.preview && 'data' in r.preview ? r.preview : null
  if (!c) return null
  return (
    <>
      <Problems title="The CoverageJSON pygeoapi returned has problems:" items={c.problems} />
      {c.metadata_missing.length > 0 && (
        <p className="notice warning">
          The provider's coverage properties lack {c.metadata_missing.join(', ')}, which the collection page reads, so /collections/&#123;id&#125; would fail.
        </p>
      )}
      <div className="two-col">
        {img ? (
          <figure className="coverage-preview">
            <img
              src={`data:${img.mimetype};base64,${img.data}`}
              width={img.width}
              height={img.height}
              alt={`${img.parameter ?? 'The first band'} over the window queried, from ${fmt(img.min)} to ${fmt(img.max)} ${img.unit ?? ''}`}
            />
            <Legend img={img} />
            <figcaption className="small muted">
              {img.parameter} in the window queried ({bboxText(img.bbox)}), drawn from pygeoapi's CoverageJSON. Missing values are transparent.
            </figcaption>
          </figure>
        ) : (
          <p className="muted">The window has no two-dimensional grid to draw.</p>
        )}
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Grid">
          <table className="compact">
            <caption>Grid</caption>
            <tbody>
              <tr>
                <th scope="row">Axes</th>
                <td className="mono">{c.axes.join(', ')}</td>
              </tr>
              <tr>
                <th scope="row">Cells</th>
                <td>
                  {fmt(c.width)} × {fmt(c.height)}
                </td>
              </tr>
              <tr>
                <th scope="row">Resolution</th>
                <td>
                  {fmt(c.resx)} × {fmt(c.resy)}
                </td>
              </tr>
              <tr>
                <th scope="row">CRS</th>
                <td className="mono">{fmt(c.crs)}</td>
              </tr>
              <tr>
                <th scope="row">Extent</th>
                <td className="mono">{bboxText(c.bbox)}</td>
              </tr>
              {c.time_range && (
                <tr>
                  <th scope="row">Time</th>
                  <td className="mono">
                    {c.time_range.join(' to ')}
                    {c.restime ? `, every ${c.restime}` : ''}
                  </td>
                </tr>
              )}
              <tr>
                <th scope="row">Window queried</th>
                <td className="mono small">{JSON.stringify((c.query as { subsets?: unknown }).subsets ?? {})}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <DomainTable domain={c.domain} caption="Axes of the CoverageJSON returned" />
      <ParameterTable r={r} caption="Bands and variables" />
    </>
  )
}

export function MapPreview({ r }: { r: ProbeData }) {
  const m = r.map
  const img = r.preview && 'data' in r.preview ? r.preview : null
  if (!m) return null
  const q = m.query as { bbox?: number[]; crs?: string; width?: number; height?: number }
  return (
    <>
      <Problems title="The map pygeoapi returned has problems:" items={m.problems} />
      {img && (
        <figure className="map-preview">
          <img src={`data:${img.mimetype};base64,${img.data}`} width={img.width} height={img.height} alt={`The map of the collection's extent, ${img.width} by ${img.height} pixels`} />
          <figcaption className="small muted">
            /map for the collection's extent ({bboxText(img.bbox)}), {q.width}×{q.height} pixels in <span className="mono">{q.crs}</span>.
          </figcaption>
        </figure>
      )}
    </>
  )
}

function tileLayers(preview: TilesPreview): LayerSpecification[] {
  const layers: LayerSpecification[] = [{ id: 'bg', type: 'background', paint: { 'background-color': '#e9eef2' } }]
  if (preview.mimetype.startsWith('image/')) {
    layers.push({ id: 'tiles', type: 'raster', source: 'probe' })
    return layers
  }
  preview.layers.forEach((l, i) => {
    const colour = LAYER_COLOURS[i % LAYER_COLOURS.length] ?? '#0f766e'
    const base = { source: 'probe', 'source-layer': l.name }
    layers.push({ id: `${l.name}-fill`, type: 'fill', ...base, filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': colour, 'fill-opacity': 0.35 } })
    layers.push({ id: `${l.name}-line`, type: 'line', ...base, filter: ['!=', ['geometry-type'], 'Point'], paint: { 'line-color': colour, 'line-width': 1.5 } })
    layers.push({ id: `${l.name}-point`, type: 'circle', ...base, filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 4, 'circle-color': colour, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })
  })
  return layers
}

function TileMap({ preview, label }: { preview: TilesPreview; label: string }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!container.current) return
    const id = registerTiles(preview.tiles, preview.mimetype)
    const url = `probe://${id}/{z}/{x}/{y}`
    const raster = preview.mimetype.startsWith('image/')
    const style: StyleSpecification = {
      version: 8,
      sources: {
        probe: raster
          ? { type: 'raster', tiles: [url], tileSize: 256, minzoom: preview.minzoom, maxzoom: preview.maxzoom }
          : { type: 'vector', tiles: [url], minzoom: preview.minzoom, maxzoom: preview.maxzoom },
      },
      layers: tileLayers(preview),
    }
    const m = new MlMap({ container: container.current, style, center: [0, 20], zoom: preview.minzoom, minZoom: preview.minzoom, attributionControl: false })
    m.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    const [x1, y1, x2, y2] = preview.bbox.length === 4 ? preview.bbox : [-180, -90, 180, 90]
    if (x1 !== undefined && y1 !== undefined && x2 !== undefined && y2 !== undefined && !(x1 <= -179 && x2 >= 179)) {
      m.fitBounds([[x1, y1], [x2, y2]], { padding: 24, duration: 0, maxZoom: preview.maxzoom + 2 })
    }
    return () => {
      m.remove()
      unregisterTiles(id)
    }
  }, [preview])
  return (
    <figure className="map">
      <div ref={container} className="map-canvas" role="group" aria-label={label} />
      <figcaption className="small muted">
        The {Object.values(preview.tiles).filter(Boolean).length} tile(s) the probe fetched, zoom {preview.minzoom}
        {preview.maxzoom !== preview.minzoom ? `–${preview.maxzoom}` : ''}; closer in, MapLibre stretches the last zoom.
      </figcaption>
    </figure>
  )
}

export function TilePreview({ r }: { r: ProbeData }) {
  const t = r.tile
  const preview = r.preview && 'tiles' in r.preview ? r.preview : null
  if (!t) return null
  const empty = preview && Object.values(preview.tiles).every((v) => !v)
  return (
    <>
      <Problems title="pygeoapi's tile answers have problems:" items={t.problems} />
      <p>
        Tiling schemes: <span className="mono">{t.schemes.join(', ') || 'none'}</span>. Zoom {fmt(t.zoom.min)}–{fmt(t.zoom.max)}. Format{' '}
        <span className="mono">{t.format}</span> ({t.mimetype}).
      </p>
      {preview && !empty && (preview.scheme === 'WebMercatorQuad' ? <TileMap preview={preview} label="Map of the tiles the probe fetched" /> : <p className="muted">MapLibre draws WebMercatorQuad tiles only, so there is no map of {preview.scheme} tiles.</p>)}
      {preview && preview.layers.length > 0 && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Tile layers">
          <table className="compact">
            <caption>Layers in the tiles</caption>
            <thead>
              <tr>
                <th scope="col">Layer</th>
                <th scope="col">Features in these tiles</th>
                <th scope="col">Geometry</th>
                <th scope="col">Properties</th>
              </tr>
            </thead>
            <tbody>
              {preview.layers.map((l) => (
                <tr key={l.name}>
                  <td className="mono">{l.name}</td>
                  <td>{l.features}</td>
                  <td>{l.geometry_types.join(', ')}</td>
                  <td className="mono small">{l.keys.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const w = 240
  const h = 48
  const points = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(hi === lo ? h / 2 : h - ((v - lo) / (hi - lo)) * h).toFixed(1)}`)
  return (
    <svg className="sparkline" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Values from ${fmt(lo)} to ${fmt(hi)}`}>
      <polyline points={points.join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export function EdrPreview({ r }: { r: ProbeData }) {
  const e = r.edr
  const series = e?.series
  const numbers = (series?.values ?? []).filter((v): v is number => typeof v === 'number')
  return (
    <>
      <p>
        Query types: <span className="mono">{(r.query_types ?? []).join(', ') || 'none'}</span>.
      </p>
      <ParameterTable r={r} caption="Parameters" />
      {e && (
        <>
          <h3>A sample query</h3>
          <p className="mono small">{JSON.stringify(e.query)}</p>
          <Problems title="The answer has problems:" items={e.problems} />
          {e.domain && (
            <p>
              pygeoapi returned a {e.domain.type} ({e.domain.domainType ?? 'no domain type'}) with {e.domain.ranges.length} parameter(s).
            </p>
          )}
          <DomainTable domain={e.domain} caption="Axes of the CoverageJSON returned" />
          {series && (
            <figure>
              <Sparkline values={numbers} />
              <figcaption className="small muted">
                The first {series.values.length} of {series.count} value(s) of <span className="mono">{series.parameter}</span>
                {series.times?.length ? `, ${series.times[0]} to ${series.times[series.times.length - 1]}` : ''}.
              </figcaption>
            </figure>
          )}
        </>
      )}
    </>
  )
}

/** What the probe got back, in a sentence, for the provider types without items. */
export function probeSummary(r: ProbeData): string | null {
  const preview = r.preview
  if (r.provider_type === 'coverage') {
    const img = preview && 'data' in preview ? preview : null
    return img ? `returned a ${img.width}×${img.height} window of ${img.parameter ?? 'the first band'} as CoverageJSON` : 'returned CoverageJSON'
  }
  if (r.provider_type === 'map') {
    const img = preview && 'data' in preview ? preview : null
    return img ? `drew a ${img.width}×${img.height} map` : 'returned no image'
  }
  if (r.provider_type === 'tile') {
    const tiles = preview && 'tiles' in preview ? Object.values(preview.tiles).filter(Boolean).length : 0
    return `served ${tiles} tile(s)`
  }
  if (r.provider_type === 'edr' && !r.sample) {
    return r.edr?.domain ? `answered a ${String(r.edr.query.query_type)} query with ${r.edr.domain.type}` : 'loaded, with no sample query'
  }
  return null
}
