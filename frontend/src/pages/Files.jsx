import React, { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, q } from '../api.js'
import { Badge, Spinner, useFetch, useRepo } from '../components/common.jsx'
import Code from '../components/Code.jsx'

function buildTree(files) {
  const root = { children: {}, files: [] }
  files.forEach((f) => { let n = root; const parts = f.path.split('/'); parts.slice(0, -1).forEach((p) => { n = n.children[p] ||= { children: {}, files: [] } }); n.files.push(f) })
  return root
}

function Dir({ name, node, depth, cur, open, toggle, pick, prefix }) {
  const key = prefix + name + '/'
  const isOpen = open.has(key) || (cur || '').startsWith(key)
  return (
    <div>
      {name && <div style={{ paddingLeft: depth * 12 }} onClick={() => toggle(key)}>{isOpen ? '▾' : '▸'} {name}</div>}
      {(isOpen || !name) && <>
        {Object.entries(node.children).sort().map(([n, c]) => <Dir key={n} name={n} node={c} depth={depth + 1} cur={cur} open={open} toggle={toggle} pick={pick} prefix={name ? key : ''} />)}
        {node.files.sort((a, b) => a.path.localeCompare(b.path)).map((f) => <div key={f.path} className={f.path === cur ? 'cur' : ''} style={{ paddingLeft: (depth + 1) * 12 }} onClick={() => pick(f.path)}>{f.path.split('/').pop()} {f.is_test ? <span className="muted">·test</span> : null}</div>)}
      </>}
    </div>
  )
}

export default function Files() {
  const { id } = useRepo()
  const [sp, setSp] = useSearchParams()
  const path = sp.get('path'), line = +sp.get('line') || 0
  const { data: files } = useFetch(() => api.get(`/api/repos/${id}/tree`), [id])
  const { data: f, loading } = useFetch(() => (path ? api.get(`/api/repos/${id}/file?` + q({ path })) : Promise.resolve(null)), [id, path])
  const [open, setOpen] = useState(new Set())
  const nav = useNavigate()
  const tree = useMemo(() => (files ? buildTree(files) : null), [files])
  const toggle = (k) => setOpen((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n })
  const end = f?.symbols?.filter((s) => s.start <= line && line <= s.end).sort((a, b) => (a.end - a.start) - (b.end - b.start))[0]
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '290px 1fr 240px', height: '100%' }}>
      <div className="filetree" style={{ borderRight: '1px solid var(--line)', overflow: 'auto', padding: 10 }}>{tree ? <Dir name="" node={tree} depth={-1} cur={path} open={open} toggle={toggle} pick={(p) => setSp({ path: p })} prefix="" /> : <Spinner />}</div>
      <div style={{ overflow: 'auto', padding: 16 }}>
        {!path && <div className="empty">Pick a file on the left.</div>}
        {loading && <Spinner />}
        {f && <><div className="row" style={{ marginBottom: 8 }}><h3 className="mono grow" style={{ margin: 0 }}>{f.path}</h3>{end && <Badge>{end.kind} {end.name}</Badge>}</div>
          <Code lines={f.lines} language={f.language} mark={line ? [end ? Math.max(end.start, line) : line, end ? Math.min(end.end, line + 0) || line : line] : null} /></>}
      </div>
      <div style={{ borderLeft: '1px solid var(--line)', overflow: 'auto', padding: 12 }}>
        {f && <><h3>Outline</h3>{f.symbols.sort((a, b) => a.start - b.start).map((s) => <div key={s.id} className="small" style={{ padding: '2px 0', cursor: 'pointer' }} onClick={() => setSp({ path, line: s.start })}><Badge>{s.kind[0]}</Badge> {s.name}</div>)}
          {f.todos.length > 0 && <><h3 style={{ marginTop: 14 }}>TODOs</h3>{f.todos.map((t) => <div key={t.line} className="small" style={{ cursor: 'pointer' }} onClick={() => setSp({ path, line: t.line })}>L{t.line}: {t.text}</div>)}</>}
          <button style={{ marginTop: 14 }} onClick={() => nav(`/r/${id}/map?focus=${encodeURIComponent('file:' + path)}`)}>⌖ show on map</button>
          <button style={{ marginTop: 8 }} onClick={() => nav(`/r/${id}/impact?target=${encodeURIComponent('file:' + path)}`)}>☄ impact of this file</button></>}
      </div>
    </div>
  )
}
