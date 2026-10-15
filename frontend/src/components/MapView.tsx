// Sample data and a bbox on a MapLibre map. The default style needs no
// network; an OpenStreetMap background is opt-in. Drawing a bbox always has a
// keyboard alternative (the numeric inputs in the Describe step).
import 'maplibre-gl/dist/maplibre-gl.css'

import { Map as MlMap, NavigationControl, type GeoJSONSource, type LngLat, type MapMouseEvent, type StyleSpecification } from 'maplibre-gl'
import { useEffect, useRef, useState } from 'react'

import type { FeatureCollection } from '../api/types'
import './maplibre'

type Bbox = [number, number, number, number]

interface Props {
  features?: FeatureCollection | null
  bbox?: number[] | null
  onDraw?: (bbox: Bbox) => void
  label: string
}

const BLANK: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e9eef2' } }],
}

const OSM: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

function bboxPolygon(b: number[]) {
  const [x1, y1, x2, y2] = b as Bbox
  return {
    type: 'FeatureCollection' as const,
    features: [
      {
        type: 'Feature' as const,
        properties: {},
        geometry: { type: 'Polygon' as const, coordinates: [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]] },
      },
    ],
  }
}

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

export function MapView({ features, bbox, onDraw, label }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MlMap | null>(null)
  const [osm, setOsm] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const [ready, setReady] = useState(0)

  useEffect(() => {
    if (!container.current) return
    const m = new MlMap({ container: container.current, style: osm ? OSM : BLANK, center: [0, 20], zoom: 0.5, attributionControl: { compact: true } })
    m.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    m.on('load', () => {
      m.addSource('data', { type: 'geojson', data: EMPTY })
      m.addSource('bbox', { type: 'geojson', data: EMPTY })
      m.addLayer({ id: 'bbox-fill', type: 'fill', source: 'bbox', paint: { 'fill-color': '#2563eb', 'fill-opacity': 0.08 } })
      m.addLayer({ id: 'bbox-line', type: 'line', source: 'bbox', paint: { 'line-color': '#2563eb', 'line-width': 2, 'line-dasharray': [2, 1] } })
      m.addLayer({ id: 'data-fill', type: 'fill', source: 'data', filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': '#0f766e', 'fill-opacity': 0.35 } })
      m.addLayer({ id: 'data-line', type: 'line', source: 'data', filter: ['!=', ['geometry-type'], 'Point'], paint: { 'line-color': '#0f766e', 'line-width': 2 } })
      m.addLayer({ id: 'data-point', type: 'circle', source: 'data', filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 5, 'circle-color': '#0f766e', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } })
      setReady((n) => n + 1)
    })
    map.current = m
    return () => {
      m.remove()
      map.current = null
    }
  }, [osm])

  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    ;(m.getSource('data') as GeoJSONSource | undefined)?.setData((features ?? EMPTY) as Parameters<GeoJSONSource['setData']>[0])
    ;(m.getSource('bbox') as GeoJSONSource | undefined)?.setData(bbox?.length === 4 ? bboxPolygon(bbox) : EMPTY)
    const b = bbox?.length === 4 ? (bbox as Bbox) : null
    if (b && !(b[0] <= -179 && b[2] >= 179)) {
      m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 24, maxZoom: 12, duration: 0 })
    }
  }, [features, bbox, ready])

  useEffect(() => {
    const m = map.current
    if (!m || !drawing || !onDraw) return
    m.dragPan.disable()
    m.getCanvas().style.cursor = 'crosshair'
    let start: LngLat | null = null
    const down = (e: MapMouseEvent) => {
      start = e.lngLat
    }
    const move = (e: MapMouseEvent) => {
      if (!start) return
      const b = [start.lng, start.lat, e.lngLat.lng, e.lngLat.lat]
      ;(m.getSource('bbox') as GeoJSONSource | undefined)?.setData(bboxPolygon(b))
    }
    const up = (e: MapMouseEvent) => {
      if (!start) return
      const r = (v: number) => Math.round(v * 1e6) / 1e6
      onDraw([r(Math.min(start.lng, e.lngLat.lng)), r(Math.min(start.lat, e.lngLat.lat)), r(Math.max(start.lng, e.lngLat.lng)), r(Math.max(start.lat, e.lngLat.lat))])
      start = null
      setDrawing(false)
    }
    m.on('mousedown', down)
    m.on('mousemove', move)
    m.on('mouseup', up)
    return () => {
      m.off('mousedown', down)
      m.off('mousemove', move)
      m.off('mouseup', up)
      m.dragPan.enable()
      m.getCanvas().style.cursor = ''
    }
  }, [drawing, onDraw])

  return (
    <figure className="map">
      {/* A group, not an image: MapLibre puts focusable controls inside (axe: nested-interactive). */}
      <div ref={container} className="map-canvas" role="group" aria-label={label} />
      <figcaption className="inline">
        <label className="small">
          <input type="checkbox" checked={osm} onChange={(e) => setOsm(e.target.checked)} /> OpenStreetMap background
        </label>
        {onDraw && (
          <button type="button" className="small" aria-pressed={drawing} onClick={() => setDrawing((d) => !d)}>
            {drawing ? 'Drag on the map to draw…' : 'Draw extent on the map'}
          </button>
        )}
      </figcaption>
    </figure>
  )
}
