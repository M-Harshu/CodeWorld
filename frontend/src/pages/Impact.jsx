import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, q } from '../api.js'
import { Badge, Err, Spinner, SymbolInput, Tabs, fileLink, useRepo } from '../components/common.jsx'
import GraphCanvas from '../components/GraphCanvas.jsx'

const LV = { low: 'low', medium: 'medium', high: 'high', critical: 'high' }

export default function Impact() {
  const { id } = useRepo()
  const [sp, setSp] = useSearchParams()
  const [target, setTarget] = useState(sp.get('target') || '')
  const [depth, setDepth] = useState(5)
  const [r, setR] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('dependents')
  const run = async (t = target) => {
    if (!t) return
    setBusy(true); setErr(null)
    try { setR(await api.get(`/api/repos/${id}/impact?` + q({ target: t, depth }))) } catch (e) { setErr(e.message); setR(null) }
    setBusy(false)
  }
  useEffect(() => { if (sp.get('target')) run(sp.get('target')) }, [])
  const pyTests = r?.tests?.filter((t) => t.path.endsWith('.py')).map((t) => `${t.path}::${t.name.split('.').pop()}`) || []
  return (
    <div className="page">
      <h1>Change impact</h1>
      <p className="muted">Pick a function, class or file. CodeWorld walks the call graph backwards, adds git co-change history, and tells you what to re-test.</p>
      <div className="row"><SymbolInput repoId={id} value={target} onChange={setTarget} onPick={(n) => { setTarget(n); run(n) }} placeholder="e.g. verify_password or app/utils/security.py" />
        <label className="small">depth {depth}<input type="range" min="1" max="8" value={depth} onChange={(e) => setDepth(+e.target.value)} /></label>
        <button className="primary" disabled={busy || !target} onClick={() => run()}>Analyse</button></div>
      <Err>{err}</Err>
      {busy && <p><Spinner /></p>}
      {r && <>
        <div className="grid g3" style={{ marginTop: 16 }}>
          <div className="card"><span className="muted small">RISK</span><div className="row"><b style={{ fontFamily: 'var(--serif)', fontSize: '2.2rem' }}>{r.risk.score}</b><Badge kind={LV[r.risk.level]}>{r.risk.level}</Badge></div>
            <div className="bar"><i style={{ width: `${r.risk.score}%` }} /></div>{r.risk.reasons.map((x) => <div key={x} className="small" style={{ marginTop: 4 }}>• {x}</div>)}</div>
          <div className="card"><div className="grid g2"><div className="tile"><b>{r.counts.symbols}</b><span>dependents</span></div><div className="tile"><b>{r.counts.files}</b><span>other files</span></div><div className="tile"><b>{r.counts.apis}</b><span>endpoints</span></div><div className="tile"><b>{r.counts.tests}</b><span>tests reach it</span></div></div></div>
          <div className="card"><h3>Run these tests first</h3>{pyTests.length ? <pre style={{ whiteSpace: 'pre-wrap' }}>pytest {pyTests.join(' ')}</pre> : r.tests.length ? r.tests.map((t) => <div key={t.id} className="small mono">{t.path} · {t.name}</div>) : <div className="muted small">No test reaches this code — add one before changing it.</div>}
            {r.likely_tests.map((t) => <div key={t.path} className="small muted">probably also {t.path} ({t.via} match)</div>)}</div>
        </div>
        <div className="card" style={{ marginTop: 16, height: 420, padding: 0, overflow: 'hidden' }}>
          <GraphCanvas minimap={false} nodes={r.graph.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind, role: n.role, top: n.path, children: 0 }))} edges={r.graph.edges.map((e) => ({ ...e, weight: 1 }))} />
        </div>
        <div className="card" style={{ marginTop: 16 }}>
          <Tabs value={tab} onChange={setTab} tabs={[['dependents', 'Dependents', r.direct.length + r.indirect.length], ['apis', 'Endpoints', r.apis.length], ['tests', 'Tests', r.tests.length], ['cochange', 'Co-changed files', r.cochange.length], ['files', 'Files', r.files.length]]} />
          {tab === 'dependents' && [...r.direct.map((d) => ({ ...d, depth: 1 })), ...r.indirect].map((d) => <div key={d.id} className="list-item"><a href={fileLink(id, d.path, d.line)}><b>{d.name}</b></a> <Badge>{d.kind}</Badge> <span className="muted small">{d.path}:{d.line}</span></div>)}
          {tab === 'apis' && r.apis.map((a) => <div key={a.id} className="list-item"><Badge>{a.api}</Badge> via {a.handler}</div>)}
          {tab === 'tests' && r.tests.map((t) => <div key={t.id} className="list-item"><b>{t.name}</b> <span className="muted small">{t.path} · distance {t.distance}</span></div>)}
          {tab === 'cochange' && (r.cochange.length ? r.cochange.map((c, i) => <div key={i} className="list-item"><a href={fileLink(id, c.path, 1)} className="mono">{c.path}</a> <span className="muted small">{c.support} commits · {Math.round(c.confidence * 100)}% </span>{!c.in_static && <Badge kind="medium">not linked statically</Badge>}</div>) : <div className="empty">No git history (or no co-changing files). Index a git clone to enable this.</div>)}
          {tab === 'files' && r.files.map((f) => <div key={f} className="list-item"><a href={fileLink(id, f, 1)} className="mono">{f}</a></div>)}
        </div></>}
    </div>
  )
}
