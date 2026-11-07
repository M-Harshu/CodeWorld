import React, { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useParams, Link } from 'react-router-dom'
import { api } from './api.js'
import { RepoCtx, Badge } from './components/common.jsx'
import Home from './pages/Home.jsx'
import Overview from './pages/Overview.jsx'
import MapPage from './pages/MapPage.jsx'
import Ask from './pages/Ask.jsx'
import Impact from './pages/Impact.jsx'
import Review from './pages/Review.jsx'
import Health from './pages/Health.jsx'
import City from './pages/City.jsx'
import Files from './pages/Files.jsx'

const Logo = () => (
  <svg width="30" height="30" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="none" stroke="#E0A526" strokeWidth="1.6" /><path d="M16 3v26M3 16h26" stroke="#5FB3B3" strokeWidth="1" /><path d="M16 8l3.6 8-3.6 8-3.6-8z" fill="#E0A526" /></svg>
)

const NAV = [
  ['overview', 'Overview', '◎'], ['map', 'Code map', '⌖'], ['ask', 'Ask', '✦'], ['impact', 'Impact', '☄'],
  ['review', 'PR guardrail', '⛨'], ['health', 'Health & security', '♥'], ['city', 'Code city', '▦'], ['files', 'Files', '❐'],
]

function RepoLayout() {
  const { id } = useParams()
  const [repo, setRepo] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get('/api/repos').then((rs) => { const r = rs.find((x) => x.id === id); r ? setRepo(r) : setErr('Repository not found') }).catch((e) => setErr(e.message))
  }, [id])
  if (err) return <div className="page"><div className="err">{err}</div><p><Link to="/">← back</Link></p></div>
  if (!repo) return <div className="page muted">Loading…</div>
  return (
    <RepoCtx.Provider value={{ id, repo }}>
      <div className="shell">
        <aside className="side">
          <Link to="/" style={{ textDecoration: 'none' }}><div className="brand"><Logo /><span>CodeWorld<small>map any codebase</small></span></div></Link>
          <div className="nav">
            <div className="sec">{repo.name}</div>
            {NAV.map(([k, label, ic]) => <NavLink key={k} to={`/r/${id}/${k}`}>{ic}<span>{label}</span></NavLink>)}
          </div>
          <div style={{ marginTop: 'auto', padding: '12px', fontSize: 12, color: '#7FA3AB' }}>
            {repo.stats?.files} files · {repo.stats?.loc?.toLocaleString()} LOC<br />
            resolver L{repo.stats?.resolver_level} · {repo.stats?.embed_model}
          </div>
        </aside>
        <main className="main">
          <Routes>
            <Route path="overview" element={<Overview />} />
            <Route path="map" element={<MapPage />} />
            <Route path="ask" element={<Ask />} />
            <Route path="impact" element={<Impact />} />
            <Route path="review" element={<Review />} />
            <Route path="health" element={<Health />} />
            <Route path="city" element={<City />} />
            <Route path="files" element={<Files />} />
            <Route path="*" element={<Navigate to="overview" replace />} />
          </Routes>
        </main>
      </div>
    </RepoCtx.Provider>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/r/:id/*" element={<RepoLayout />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
