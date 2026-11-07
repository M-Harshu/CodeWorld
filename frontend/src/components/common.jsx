import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import { api, q } from '../api.js'

export const RepoCtx = createContext(null)
export const useRepo = () => useContext(RepoCtx)

export function useFetch(fn, deps) {
  const [s, set] = useState({ data: null, loading: true, error: null })
  useEffect(() => {
    let live = true
    set((p) => ({ ...p, loading: true, error: null }))
    fn().then((data) => live && set({ data, loading: false, error: null })).catch((e) => live && set({ data: null, loading: false, error: e.message }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return s
}

export const Spinner = ({ label }) => <span className="muted"><span className="spinner" /> {label || ''}</span>
export const Badge = ({ kind, children }) => <span className={`badge ${kind || ''}`}>{children}</span>
export const Err = ({ children }) => children ? <div className="err">{children}</div> : null
export const Empty = ({ children }) => <div className="empty">{children}</div>
export const fileLink = (id, path, line) => `#/r/${id}/files?${q({ path, line })}`

export function Tabs({ tabs, value, onChange }) {
  return <div className="tabs">{tabs.map(([k, label, count]) => (
    <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{label}{count !== undefined ? <span className="muted"> {count}</span> : null}</button>
  ))}</div>
}

export function SymbolInput({ repoId, value, onChange, onPick, placeholder }) {
  const [opts, setOpts] = useState([])
  const [open, setOpen] = useState(false)
  const t = useRef()
  useEffect(() => {
    clearTimeout(t.current)
    if (!value || value.length < 2) { setOpts([]); return }
    t.current = setTimeout(() => api.get(`/api/repos/${repoId}/symbols?` + q({ q: value, limit: 8 })).then(setOpts).catch(() => {}), 160)
  }, [value, repoId])
  return (
    <div style={{ position: 'relative', flex: 1 }}>
      <input type="text" value={value} placeholder={placeholder} onChange={(e) => { onChange(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => e.key === 'Enter' && (onPick ? onPick(value) : null)} />
      {open && opts.length > 0 && (
        <div className="card" style={{ position: 'absolute', zIndex: 20, top: '100%', left: 0, right: 0, padding: 4, marginTop: 4 }}>
          {opts.map((o) => (
            <div key={o.id} className="list-item" style={{ cursor: 'pointer', padding: '6px 10px' }} onMouseDown={() => { onChange(o.name); onPick && onPick(o.name, o) }}>
              <b>{o.name}</b> <span className="badge">{o.kind}</span> <span className="muted small">{o.path}:{o.line}</span>
            </div>))}
        </div>)}
    </div>
  )
}
