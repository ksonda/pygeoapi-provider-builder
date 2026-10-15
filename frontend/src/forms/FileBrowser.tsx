// Pick a file or folder inside a data root. Paths are server paths (as pygeoapi
// sees them); the backend refuses anything outside the configured roots.
import { useEffect, useRef, useState } from 'react'

import { api, unwrap } from '../api/client'
import type { FsListing } from '../api/types'

interface Props {
  open: boolean
  start: string
  extensions?: string[] | null
  /** file: pick files; folder: pick folders (a tile tree); any: either (a .zarr store). */
  pathKind?: 'file' | 'folder' | 'any'
  onPick: (path: string) => void
  onClose: () => void
}

function formatSize(n: number | null): string {
  if (n === null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function FileBrowser({ open, start, extensions, pathKind = 'file', onPick, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const [path, setPath] = useState(start)
  const [listing, setListing] = useState<FsListing | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  useEffect(() => {
    if (!open) return
    let live = true
    api
      .GET('/api/fs', { params: { query: { path } } })
      .then((res) => {
        if (!live) return
        setListing(unwrap(res) as unknown as FsListing)
        setError(null)
      })
      .catch((e: unknown) => live && setError(String(e instanceof Error ? e.message : e)))
    return () => {
      live = false
    }
  }, [open, path])

  const allowed = (name: string) => !extensions?.length || extensions.some((ext) => name.toLowerCase().endsWith(ext))
  // A folder can be the answer: any folder for a tile tree, a matching one (x.zarr) for "any".
  const folderPick = (name: string) => pathKind === 'folder' || (pathKind === 'any' && Boolean(extensions?.length) && allowed(name))

  return (
    <dialog ref={ref} className="file-browser" aria-labelledby="fb-title" onClose={onClose}>
      <h2 id="fb-title">{pathKind === 'folder' ? 'Choose a folder' : 'Choose a file'}</h2>
      <p className="muted">
        Folder <code>{listing?.server_path ?? path}</code>
        {extensions?.length ? <> · showing {extensions.join(', ')} {pathKind === 'file' ? 'files' : 'entries'}</> : null}
      </p>
      {pathKind === 'folder' && listing && (
        <p>
          <button type="button" onClick={() => onPick(listing.server_path)}>
            Use this folder
          </button>
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
      <ul className="fs-list">
        {listing?.parent && (
          <li>
            <button type="button" className="link" onClick={() => setPath(listing.parent ?? start)}>
              ↑ Parent folder
            </button>
          </li>
        )}
        {listing?.entries.map((e) => (
          <li key={e.server_path}>
            {e.kind === 'dir' ? (
              <>
                <button type="button" className="link" onClick={() => setPath(e.server_path)}>
                  📁 {e.name}/
                </button>
                {folderPick(e.name) && (
                  <>
                    {' '}
                    <button type="button" className="small" onClick={() => onPick(e.server_path)} aria-label={`Use the folder ${e.name}`}>
                      Use
                    </button>
                  </>
                )}
              </>
            ) : (
              <button type="button" className="link" disabled={pathKind === 'folder' || !allowed(e.name)} onClick={() => onPick(e.server_path)}>
                {e.name} <span className="muted">{formatSize(e.size)}</span>
              </button>
            )}
          </li>
        ))}
      </ul>
      <div className="actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </dialog>
  )
}
