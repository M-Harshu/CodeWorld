import React, { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { Badge, Err, Spinner } from '../components/common.jsx'

function Job({ job, onDone }) {
  const [j, setJ] = useState(null)
  useEffect(() => {
    const t = setInterval(async () => {
      try { const x = await api.get(`/api/jobs/${job.job_id}`); setJ(x); if (x.status !== 'running') { clearInterval(t); onDone(x) } } catch {}
    }, 700)
    return () => clearInterval(t)
  }, [job.job_id])
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="row"><Spinner /> <b>{j?.stage || 'queued'}</b><span className="grow" /><span className="muted">{Math.round(j?.pct || 0)}%</span></div>
      <div className="bar" style={{ marginTop: 8 }}><i style={{ width: `${j?.pct || 2}%` }} /></div>
      {j?.status === 'error' && <Err>{j.error}</Err>}
    </div>
  )
}

export default function Home() {
  const nav = useNavigate()
  const [repos, setRepos] = useState([])
  const [url, setUrl] = useState('')
  const [branch, setBranch] = useState('')
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  const [health, setHealth] = useState(null)
  const file = useRef()
  const load = () => api.get('/api/repos').then(setRepos).catch((e) => setErr(e.message))
  useEffect(() => { load(); api.get('/api/health').then(setHealth).catch(() => {}); const t = setInterval(load, 4000); return () => clearInterval(t) }, [])
  const start = async (p) => { setErr(null); try { setJob(await p) } catch (e) { setErr(e.message) } }
  return (
    <div className="page">
      <div className="hero">
        <h1>CodeWorld</h1>
        <p>A map for any codebase. Parse it into a knowledge graph, ask questions with verified citations, see what a change will break, and check AI-written pull requests before they merge.</p>
        <div className="row">
          <input type="text" style={{ flex: 3, minWidth: 260 }} placeholder="https://github.com/owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
          <input type="text" style={{ flex: 1, minWidth: 110 }} placeholder="branch (optional)" value={branch} onChange={(e) => setBranch(e.target.value)} />
          <button className="primary" disabled={!url} onClick={() => start(api.post('/api/repos', { url, branch: branch || null }))}>Index repository</button>
          <button onClick={() => file.current.click()}>Upload .zip</button>
          <input ref={file} type="file" accept=".zip" hidden onChange={(e) => e.target.files[0] && start(api.upload(e.target.files[0]))} />
        </div>
        {job && <Job job={job} onDone={(x) => { load(); if (x.status === 'done') nav(`/r/${job.repo_id}/overview`) }} />}
        <Err>{err}</Err>
        <div className="small" style={{ marginTop: 14, color: '#9FC0C6' }}>
          Runs fully on CPU. LLM: <b>{health?.llm?.provider === 'none' ? 'offline mode (no LLM configured)' : `${health?.llm?.provider} · ${health?.llm?.model}`}</b> · Embeddings: <b>{health?.embeddings?.model}</b>. Repository code is never executed.
        </div>
      </div>
      <h2 style={{ marginTop: 28 }}>Your repositories</h2>
      {repos.length === 0 && <div className="empty card">Nothing indexed yet. Paste a GitHub URL above to begin.</div>}
      <div className="grid g3">
        {repos.map((r) => (
          <div className="card" key={r.id}>
            <div className="row"><h3 className="grow" style={{ margin: 0 }}>{r.name}</h3><Badge kind={r.status === 'ready' ? 'ok' : r.status === 'error' ? 'high' : 'medium'}>{r.status}</Badge></div>
            <div className="muted small" style={{ margin: '6px 0 10px' }}>{r.source}</div>
            {r.stats?.files != null && <div className="small">{r.stats.files} files · {r.stats.loc?.toLocaleString()} LOC · {r.stats.edges?.CALLS ?? 0} call edges · {r.stats.apis ?? 0} endpoints</div>}
            <div className="row" style={{ marginTop: 12 }}>
              {r.status === 'ready' && <Link to={`/r/${r.id}/overview`}><button className="primary">Open</button></Link>}
              <button className="ghost" onClick={async () => { if (confirm(`Delete ${r.name}?`)) { await api.del(`/api/repos/${r.id}`); load() } }}>Delete</button>
            </div>
          </div>))}
      </div>
    </div>
  )
}
