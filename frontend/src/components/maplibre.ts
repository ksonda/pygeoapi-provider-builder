// MapLibre setup every map shares: the ES-module worker Vite bundles, and the
// probe:// protocol, which serves tiles the sandbox probe fetched from memory,
// so a tile preview needs no network and no tile server.
import { addProtocol, setWorkerUrl } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

setWorkerUrl(workerUrl)

interface TileSet {
  tiles: Record<string, string | null>
  raster: boolean
}

const TILE_SETS = new Map<string, TileSet>()
let counter = 0

// A transparent 1×1 PNG: a raster tile that wasn't fetched draws as nothing.
const BLANK_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII='

function decode(b64: string): ArrayBuffer {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}

/** Make tiles (base64 by "z/x/y") reachable as probe://<id>/{z}/{x}/{y}; returns the id. */
export function registerTiles(tiles: Record<string, string | null>, mimetype: string): string {
  counter += 1
  const id = `set${counter}`
  TILE_SETS.set(id, { tiles, raster: mimetype.startsWith('image/') })
  return id
}

export function unregisterTiles(id: string): void {
  TILE_SETS.delete(id)
}

addProtocol('probe', async (params) => {
  const m = /^probe:\/\/([^/]+)\/(\d+)\/(\d+)\/(\d+)/.exec(params.url)
  const set = m ? TILE_SETS.get(m[1] ?? '') : undefined
  const tile = m && set ? set.tiles[`${m[2]}/${m[3]}/${m[4]}`] : undefined
  if (tile) return { data: decode(tile) }
  // Not fetched by the probe, or empty: an empty vector tile, or a blank image.
  return { data: set?.raster ? decode(BLANK_PNG) : new ArrayBuffer(0) }
})
