// A keyboard-operable JSON tree for choosing paths (PLAN.md §7.4 step 3, §11).
// WAI-ARIA tree pattern: a flat list of treeitems with aria-level, roving
// tabindex, arrow keys to move and expand, Home/End, and Enter or Space to
// choose. Paths are normalized RFC 9535 JSONPaths, built like the backend's.
import { useMemo, useRef, useState, type KeyboardEvent } from 'react'

interface Props {
  data: unknown
  label: string
  selected?: string | null
  onPick?: (path: string) => void
  /** Which nodes can be chosen; default: arrays of objects (record lists). */
  pickable?: (value: unknown) => boolean
  maxChildren?: number
}

interface Row {
  path: string
  parent: string | null
  key: string
  value: unknown
  level: number
  setSize: number
  posInSet: number
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/

export function childPath(parent: string, key: string | number): string {
  if (typeof key === 'number') return `${parent}[${key}]`
  return IDENT.test(key) ? `${parent}.${key}` : `${parent}['${key.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}']`
}

function isContainer(v: unknown): v is Record<string, unknown> | unknown[] {
  return typeof v === 'object' && v !== null
}

export function isRecordList(v: unknown): boolean {
  return Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === 'object' && x !== null && !Array.isArray(x))
}

function preview(v: unknown): string {
  if (Array.isArray(v)) return `[${v.length} item${v.length === 1 ? '' : 's'}]`
  if (isContainer(v)) {
    const n = Object.keys(v).length
    return `{${n} key${n === 1 ? '' : 's'}}`
  }
  const text = JSON.stringify(v) ?? String(v)
  return text.length > 60 ? `${text.slice(0, 57)}…` : text
}

function entries(v: unknown, max: number): [string | number, unknown][] {
  if (Array.isArray(v)) return v.slice(0, max).map((x, i) => [i, x])
  if (isContainer(v)) return Object.entries(v).slice(0, max)
  return []
}

function ancestors(path: string | null | undefined): string[] {
  if (!path) return []
  const out = ['$']
  const re = /\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\]|\['(?:[^'\\]|\\.)*'\]/g
  let acc = '$'
  for (const m of path.slice(1).match(re) ?? []) {
    acc += m
    out.push(acc)
  }
  return out.slice(0, -1)
}

export function JsonTree({ data, label, selected, onPick, pickable = isRecordList, maxChildren = 50 }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(['$', ...ancestors(selected)]))
  const [focus, setFocus] = useState('$')
  const refs = useRef(new Map<string, HTMLLIElement>())

  const rows = useMemo(() => {
    const out: Row[] = []
    const walk = (path: string, parent: string | null, key: string, value: unknown, level: number, setSize: number, pos: number) => {
      out.push({ path, parent, key, value, level, setSize, posInSet: pos })
      if (!expanded.has(path)) return
      const kids = entries(value, maxChildren)
      kids.forEach(([k, v], i) => walk(childPath(path, k), path, String(k), v, level + 1, kids.length, i + 1))
    }
    walk('$', null, '$', data, 1, 1, 1)
    return out
  }, [data, expanded, maxChildren])

  const move = (path: string | undefined) => {
    if (!path) return
    setFocus(path)
    refs.current.get(path)?.focus()
  }
  const toggle = (path: string, open: boolean) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (open) next.add(path)
      else next.delete(path)
      return next
    })

  const onKey = (e: KeyboardEvent, row: Row, index: number) => {
    const container = isContainer(row.value) && entries(row.value, 1).length > 0
    const open = expanded.has(row.path)
    switch (e.key) {
      case 'ArrowDown':
        move(rows[index + 1]?.path)
        break
      case 'ArrowUp':
        move(rows[index - 1]?.path)
        break
      case 'ArrowRight':
        if (container && !open) toggle(row.path, true)
        else if (container) move(rows[index + 1]?.path)
        break
      case 'ArrowLeft':
        if (container && open) toggle(row.path, false)
        else move(row.parent ?? undefined)
        break
      case 'Home':
        move(rows[0]?.path)
        break
      case 'End':
        move(rows[rows.length - 1]?.path)
        break
      case 'Enter':
      case ' ':
        if (onPick && pickable(row.value)) onPick(row.path)
        else if (container) toggle(row.path, !open)
        break
      default:
        return
    }
    e.preventDefault()
  }

  return (
    <ul role="tree" aria-label={label} className="json-tree">
      {rows.map((row, i) => {
        const container = isContainer(row.value) && entries(row.value, 1).length > 0
        const canPick = Boolean(onPick) && pickable(row.value)
        return (
          <li
            key={row.path}
            ref={(el) => {
              if (el) refs.current.set(row.path, el)
              else refs.current.delete(row.path)
            }}
            role="treeitem"
            aria-level={row.level}
            aria-setsize={row.setSize}
            aria-posinset={row.posInSet}
            aria-expanded={container ? expanded.has(row.path) : undefined}
            aria-selected={selected === row.path}
            tabIndex={focus === row.path ? 0 : -1}
            className={`json-node${selected === row.path ? ' selected' : ''}${canPick ? ' pickable' : ''}`}
            style={{ paddingInlineStart: `${(row.level - 1) * 1.1}rem` }}
            onKeyDown={(e) => onKey(e, row, i)}
            onFocus={() => setFocus(row.path)}
            onClick={(e) => {
              e.stopPropagation()
              setFocus(row.path)
              if (canPick && onPick) onPick(row.path)
              else if (container) toggle(row.path, !expanded.has(row.path))
            }}
          >
            <span aria-hidden="true" className="json-caret">
              {container ? (expanded.has(row.path) ? '▾' : '▸') : ' '}
            </span>
            <span className="json-key">{row.key}</span>
            <span className="json-value">{preview(row.value)}</span>
            {canPick && <span className="json-pick">{selected === row.path ? 'records' : 'use as records'}</span>}
          </li>
        )
      })}
    </ul>
  )
}
