import React, { useState } from 'react'
import { api } from '../api.js'
import { Badge, Empty, Err, Spinner, Tabs, fileLink, useFetch, useRepo } from '../components/common.jsx'

const Loc = ({ id, path, line }) => <a className="mono small" href={fileLink(id, path, line || 1)}>{path}{line ? `:${line}` : ''}</a>

export default function Health() {
  const { id } = useRepo()
  const { data: h, loading, error } = useFetch(() => api.get(`/api/repos/${id}/health`), [id])
  const { data: s } = useFetch(() => api.get(`/api/repos/${id}/security`), [id])
  const [tab, setTab] = useState('security')
  if (loading) return <div className="page"><Spinner label="Analysing…" /></div>
  if (error) return <div className="page"><Err>{error}</Err></div>
  const tabs = [['security', 'Security', s ? s.summary.critical + s.summary.high : undefined], ['hotspots', 'Hotspots', h.hotspots.length], ['untested', 'Untested', h.untested.untested], ['dead', 'Dead code', h.dead_code.length],
    ['dups', 'Duplicates', h.duplicates.length], ['arch', 'Architecture', h.cycles.length + h.layering.length], ['complex', 'Complexity', h.complexity.length], ['people', 'People'], ['clusters', 'Clusters', h.communities.length]]
  return (
    <div className="page">
      <div className="row"><h1 className="grow">Health & security</h1></div>
      <div className="grid g4">
        <div className="tile"><b>{h.score}</b><span>health score</span></div>
        <div className="tile"><b>{Math.round(h.untested.coverage * 100)}%</b><span>functions reached by tests (graph)</span></div>
        <div className="tile"><b>{h.cycles.length}</b><span>import cycles</span></div>
        <div className="tile"><b>{h.layering.length}</b><span>layering violations</span></div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <Tabs tabs={tabs} value={tab} onChange={setTab} />
        {tab === 'security' && (!s ? <Spinner /> : <>
          <div className="row"><Badge kind="critical">{s.summary.critical} critical</Badge><Badge kind="high">{s.summary.high} high</Badge><Badge kind="medium">{s.summary.medium} medium</Badge><span className="muted small">{s.disclaimer}</span></div>
          <h3 style={{ marginTop: 14 }}>Endpoints that reach risky sinks</h3>
          {s.attack_surface.length === 0 && <Empty>No endpoint reaches a flagged sink.</Empty>}
          {s.attack_surface.map((a) => <div className="list-item" key={a.id}><Badge kind={a.worst}>{a.worst}</Badge> <b>{a.api}</b> <span className="muted small">→ {a.handler}</span>
            {a.risks.slice(0, 3).map((r, i) => <div className="small" key={i}>{r.sink}: {r.route.join(' → ')} (<Loc id={id} path={r.path} line={r.line} />)</div>)}</div>)}
          <h3 style={{ marginTop: 14 }}>Findings</h3>
          {s.findings.map((f, i) => <div className="list-item" key={i}><Badge kind={f.severity}>{f.severity}</Badge> {f.message} — <b>{f.symbol}</b> <Loc id={id} path={f.path} line={f.line} /></div>)}
          {s.secrets.length > 0 && <><h3 style={{ marginTop: 14 }}>Possible secrets (redacted)</h3>{s.secrets.map((x, i) => <div className="list-item" key={i}><Badge kind="high">{x.kind}</Badge> <Loc id={id} path={x.path} line={x.line} /></div>)}</>}
        </>)}
        {tab === 'hotspots' && <table><thead><tr><th>File</th><th>Score</th><th>Commits</th><th>Complexity</th><th>Authors</th></tr></thead><tbody>{h.hotspots.map((x) => <tr key={x.path}><td><Loc id={id} path={x.path} /></td><td>{x.score}</td><td>{x.commits}</td><td>{x.complexity}</td><td>{x.authors ?? '–'}</td></tr>)}</tbody></table>}
        {tab === 'untested' && <><p className="muted small">Functions that no test reaches through the static call graph (a proxy for coverage — run a real coverage tool for line coverage).</p>
          <table><thead><tr><th>Function</th><th>Complexity</th><th>Callers</th></tr></thead><tbody>{h.untested.top.map((x) => <tr key={x.id}><td><b>{x.name}</b><br /><Loc id={id} path={x.path} line={x.line} /></td><td>{x.complexity}</td><td>{x.callers}</td></tr>)}</tbody></table></>}
        {tab === 'dead' && (h.dead_code.length ? h.dead_code.map((d) => <div className="list-item" key={d.id}><Badge kind={d.confidence === 'high' ? 'high' : 'medium'}>{d.confidence}</Badge> <b>{d.name}</b> <Loc id={id} path={d.path} line={d.line} /><div className="small muted">{d.note}</div></div>) : <Empty>No dead code candidates.</Empty>)}
        {tab === 'dups' && (h.duplicates.length ? h.duplicates.map((d, i) => <div className="list-item" key={i}><Badge>{Math.round(d.similarity * 100)}%</Badge> <b>{d.a.name}</b> ≈ <b>{d.b.name}</b> <span className="muted small">{d.kind}</span><div><Loc id={id} path={d.a.path} line={d.a.start} /> · <Loc id={id} path={d.b.path} line={d.b.start} /></div></div>) : <Empty>No duplicated logic found.</Empty>)}
        {tab === 'arch' && <>
          <h3>Layering rules <span className="muted small">({h.layering_rules}; add .codeworld.yml to customise)</span></h3>
          {h.layering.length ? h.layering.map((v, i) => <div className="list-item" key={i}><Badge kind="high">violation</Badge> <Loc id={id} path={v.from} line={v.line} /> → <Loc id={id} path={v.to} /><div className="small">{v.rule}</div></div>) : <Empty>No layering violations.</Empty>}
          <h3 style={{ marginTop: 14 }}>Import cycles</h3>
          {h.cycles.length ? h.cycles.map((c, i) => <div className="list-item mono small" key={i}>{[...c, c[0]].join(' → ')}</div>) : <Empty>No import cycles.</Empty>}</>}
        {tab === 'complex' && (h.complexity.length ? h.complexity.map((d) => <div className="list-item" key={d.id}><Badge kind={d.complexity > 15 ? 'high' : 'medium'}>{d.complexity}</Badge> <b>{d.name}</b> <Loc id={id} path={d.path} line={d.line} /></div>) : <Empty>No highly complex functions.</Empty>)}
        {tab === 'people' && (h.bus_factor.available ? <>
          <div className="tile" style={{ maxWidth: 280 }}><b>{h.bus_factor.bus_factor}</b><span>bus factor ({h.bus_factor.authors} authors)</span></div>
          <h3 style={{ marginTop: 14 }}>Top contributors</h3>{h.bus_factor.top_authors.map((a) => <div key={a.author} className="list-item">{a.author} <span className="muted small">{a.commits} commits</span></div>)}
          <h3 style={{ marginTop: 14 }}>Files with a single owner</h3>{h.bus_factor.single_owner_files.map((f) => <div key={f.path} className="list-item"><Loc id={id} path={f.path} /> <span className="muted small">{f.commits} commits</span></div>)}</> : <Empty>No git history available (index a git clone, not a ZIP).</Empty>)}
        {tab === 'clusters' && h.communities.map((c) => <div key={c.id} className="list-item"><b>{c.label}</b> <Badge>{c.size} files</Badge><div className="small mono muted">{c.files.slice(0, 6).join(' · ')}</div></div>)}
      </div>
    </div>
  )
}
