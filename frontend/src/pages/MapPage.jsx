import React, { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, q } from '../api.js'
import { Badge, Spinner, SymbolInput, fileLink, useRepo } from '../components/common.jsx'
import GraphCanvas from '../components/GraphCanvas.jsx'
import Code from '../components/Code.jsx'

function Detail({ id, node, onFocus }) {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  useEffect(() => { setD(null); api.get(`/api/repos/${id}/node?` + q({ id: node })).then(setD).catch(() => setD({ error: true })) }, [id, node])
  if (!d) return <div className="panel"><Spinner /></div>
  if (d.error) return <div className="panel muted">No details.</div>
  const list = (title, items, key = 'name') => items?.length ? (<><h3 style={{ marginTop: 14 }}>{title}</h3>{items.slice(0, 14).map((x, i) => (
    <div key={i} className="small" style={{ padding: '2px 0' }}>{x.id ? <a href="#" onClick={(e) => { e.preventDefault(); onFocus(x.id) }}>{x[key]}</a> : <span className="mono">{x}</span>} {x.depth > 1 && <span className="muted">· depth {x.depth}</span>}</div>))}</>) : null
  return (
    <div className="panel">
      <div className="row"><Badge>{d.kind}</Badge><h3 style={{ margin: 0, wordBreak: 'break-all' }}>{d.name}</h3></div>
      {d.path && <div className="small"><a href={fileLink(id, d.path, d.line)} className="mono">{d.path}:{d.line}</a></div>}
      {d.signature && <pre style={{ marginTop: 10 }}>{d.signature}</pre>}
      {d.doc && <p className="small">{d.doc}</p>}
      <div className="row">{(d.effects || []).map((e) => <Badge key={e} kind={['eval', 'process', 'deserialize'].includes(e) ? 'high' : 'medium'}>{e}</Badge>)}{d.route && <Badge>{d.route.method} {d.route.path}</Badge>}{d.complexity ? <Badge>complexity {d.complexity}</Badge> : null}</div>
      <div className="row" style={{ margin: '12px 0' }}>
        <button onClick={() => nav(`/r/${id}/impact?target=${encodeURIComponent(d.id)}`)}>☄ Impact</button>
        <button onClick={() => nav(`/r/${id}/ask?q=${encodeURIComponent('Explain ' + d.name)}`)}>✦ Ask</button>
      </div>
      {d.code && <Code lines={d.code.split('\n')} start={d.line} language={d.path?.match(/\.(ts|tsx)$/) ? 'typescript' : d.path?.match(/\.(js|jsx|mjs)$/) ? 'javascript' : 'python'} maxHeight={260} />}
      {list('Called by', d.callers)}{list('Calls', d.callees)}
      {d.tests?.length ? <><h3 style={{ marginTop: 14 }}>Tests</h3>{d.tests.map((t) => <div key={t} className="small mono">{t}</div>)}</> : null}
      {d.kind === 'file' && <>{list('Imports', d.imports)}{list('Imported by', d.imported_by)}{list('Symbols', d.symbols)}</>}
    </div>
  )
}

export default function MapPage() {
  const { id } = useRepo()
  const [sp, setSp] = useSearchParams()
  const hl = (sp.get('hl') || '').split('|').filter(Boolean)
  const focus = sp.get('focus')
  const [expanded, setExpanded] = useState([])
  const [types, setTypes] = useState(['CALLS', 'IMPORTS', 'INHERITS'])
  const [tests, setTests] = useState(false)
  const [conf, setConf] = useState(0.5)
  const [view, setView] = useState(null)
  const [sel, setSel] = useState(null)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let live = true
    setLoading(true)
    api.get(`/api/repos/${id}/graph?` + q({ expanded: expanded.join('|'), types: types.join(','), highlight: hl.join('|'), min_conf: conf, tests, focus }))
      .then((v) => live && setView(v)).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [id, expanded.join('|'), types.join(','), hl.join('|'), conf, tests, focus])

  useEffect(() => {
    if (!focus) return
    api.get(`/api/repos/${id}/ancestors?` + q({ id: focus })).then((anc) => { setExpanded((p) => [...new Set([...p, ...anc])]); setSel(focus) }).catch(() => {})
  }, [focus, id])

  const toggle = useCallback((n) => setExpanded((p) => (p.includes(n.id) ? p.filter((x) => x !== n.id && !x.startsWith(n.id.replace(/^(mod|file):/, '$1:') + '/')) : [...p, n.id])), [])
  const expandAll = async () => {
    const files = await api.get(`/api/repos/${id}/tree`)
    const mods = new Set()
    files.forEach((f) => { const parts = f.path.split('/').slice(0, -1); for (let i = 1; i <= parts.length; i++) mods.add('mod:' + parts.slice(0, i).join('/')) })
    setExpanded([...mods])
  }
  const goto = (name, o) => o ? setSp({ focus: o.id }) : api.get(`/api/repos/${id}/symbols?` + q({ q: name, limit: 1 })).then((r) => r[0] && setSp({ focus: r[0].id }))
  const T = (t) => <label><input type="checkbox" checked={types.includes(t)} onChange={() => setTypes((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))} />{t.toLowerCase()}</label>
  return (
    <div className={'map-wrap' + (sel ? '' : ' nopanel')} style={{ height: '100%' }}>
      <div className="canvas">
        <div className="toolbar">
          <div className="pill"><span>⌕</span><SymbolInput repoId={id} value={search} onChange={setSearch} onPick={goto} placeholder="find a function, class or file…" /></div>
          <div className="pill">{T('CALLS')}{T('IMPORTS')}{T('INHERITS')}<label><input type="checkbox" checked={tests} onChange={(e) => setTests(e.target.checked)} />tests</label></div>
          <div className="pill">confidence ≥ {conf.toFixed(1)}<input type="range" min="0" max="1" step="0.1" value={conf} onChange={(e) => setConf(+e.target.value)} /></div>
          <div className="pill"><button className="ghost" onClick={expandAll}>expand folders</button><button className="ghost" onClick={() => setExpanded([])}>collapse all</button>
            {hl.length > 0 && <button className="ghost" onClick={() => setSp({})}>clear highlights ({hl.length})</button>}</div>
          {loading && <div className="pill"><Spinner /></div>}
          {view?.truncated && <div className="pill">showing first {view.nodes.length} of {view.total_visible} nodes — collapse something</div>}
        </div>
        {view && <GraphCanvas nodes={view.nodes} edges={view.edges} selected={sel} onSelect={(n) => setSel(n.id)} onToggle={toggle} />}
      </div>
      {sel && <div style={{ position: 'relative', overflow: 'hidden' }}><button className="ghost" style={{ position: 'absolute', right: 10, top: 8, zIndex: 3 }} onClick={() => setSel(null)}>✕</button><Detail id={id} node={sel} onFocus={(nid) => setSp({ focus: nid })} /></div>}
    </div>
  )
}
