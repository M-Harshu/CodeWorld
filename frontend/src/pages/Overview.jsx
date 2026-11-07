import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { Badge, Err, Spinner, Tabs, fileLink, useFetch, useRepo } from '../components/common.jsx'
import { Mermaid } from '../components/Markdown.jsx'

export default function Overview() {
  const { id, repo } = useRepo()
  const { data: o, loading, error } = useFetch(() => api.get(`/api/repos/${id}/onboarding`), [id])
  const { data: h } = useFetch(() => api.get(`/api/repos/${id}/health`), [id])
  const [flow, setFlow] = useState(0)
  const s = repo.stats || {}
  if (loading) return <div className="page"><Spinner label="Reading the codebase…" /></div>
  if (error) return <div className="page"><Err>{error}</Err></div>
  const tiles = [['files', s.files], ['lines', s.loc?.toLocaleString()], ['classes', s.classes], ['functions', (s.functions || 0) + (s.methods || 0)], ['endpoints', s.apis], ['tests', s.tests], ['call edges', s.edges?.CALLS]]
  return (
    <div className="page">
      <div className="row"><h1 className="grow">{repo.name}</h1>
        <a href={`/api/repos/${id}/onboarding.md`} download={`${repo.name}-onboarding.md`}><button>⬇ Onboarding guide (.md)</button></a>
        <button onClick={async () => { await api.post(`/api/repos/${id}/reindex`, {}); alert('Re-indexing started (incremental).') }}>↻ Re-index</button></div>
      <p className="muted" style={{ maxWidth: 820 }}>{o.summary}</p>
      <div className="grid g4" style={{ gridTemplateColumns: 'repeat(7, minmax(0,1fr))' }}>{tiles.map(([k, v]) => <div className="tile" key={k}><b>{v ?? 0}</b><span>{k}</span></div>)}</div>
      {h && <div className="card" style={{ marginTop: 16 }}><div className="row"><h3 style={{ margin: 0 }}>Health score</h3><b style={{ fontFamily: 'var(--serif)', fontSize: '1.6rem' }}>{h.score}</b><span className="muted">/ 100</span><div className="bar good grow"><i style={{ width: `${h.score}%` }} /></div>
        <Link to={`/r/${id}/health`}>details →</Link></div></div>}
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card"><h2>Architecture</h2><Mermaid code={o.architecture} /></div>
        <div className="card"><h2>Guided tour</h2>
          {o.tour.map((t) => <div className="list-item" key={t.step}><b>{t.step}. {t.title}</b><div className="muted small">{t.detail} {t.target && <a href={fileLink(id, t.target, 1)}>open</a>}</div></div>)}</div>
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card"><h2>Core modules</h2><div className="muted small">Ranked by how much of the codebase depends on them.</div>
          {o.key_modules.map((m) => <div className="list-item" key={m.path}><a href={fileLink(id, m.path, 1)} className="mono">{m.path}</a> <span className="muted small">{m.loc} lines · imported by {m.depended_on_by}</span>{m.doc && <div className="small muted">{m.doc}</div>}</div>)}</div>
        <div className="card"><h2>Entry points</h2>
          {o.entry_points.slice(0, 14).map((e, i) => <div className="list-item" key={i}><Badge>{e.kind}</Badge> <a href={fileLink(id, e.path, e.line)}><b>{e.name}</b></a> {e.handler && <span className="muted small">→ {e.handler}</span>}</div>)}</div>
      </div>
      {o.flows.length > 0 && <div className="card" style={{ marginTop: 16 }}><h2>Request flows</h2>
        <Tabs tabs={o.flows.map((f, i) => [i, f.endpoint])} value={flow} onChange={setFlow} /><Mermaid key={flow} code={o.flows[flow].mermaid} /></div>}
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card"><h2>Domain glossary</h2><div className="row">{o.glossary.map((g) => <span className="badge" key={g.term} title={g.examples.join(', ')}>{g.term}</span>)}</div></div>
        <div className="card"><h2>Good first changes</h2>
          {o.good_first_changes.map((c, i) => <div className="list-item" key={i}><Badge>{c.kind}</Badge> <a href={fileLink(id, c.path, c.line)}>{c.title}</a></div>)}</div>
      </div>
    </div>
  )
}
