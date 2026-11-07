import React, { useState } from 'react'
import { api } from '../api.js'
import { Badge, Err, Spinner, Tabs, fileLink, useRepo } from '../components/common.jsx'
import GraphCanvas from '../components/GraphCanvas.jsx'
import Markdown from '../components/Markdown.jsx'

const SAMPLE = `diff --git a/app/services/order_service.py b/app/services/order_service.py
index 97f1600..c84c86d 100644
--- a/app/services/order_service.py
+++ b/app/services/order_service.py
@@ -1,3 +1,6 @@
+import requests_oauth_magic
+from app.utils.security import encrypt_everything
+from app.api.auth import login
 from app.repositories.order_repository import OrderRepository
 from app.models.order import Order
 from app.utils.legacy import total_price_a
@@ -14,3 +17,10 @@ class OrderService:
 
     def history(self, user_id: int):
         return self.orders.for_user(user_id)
+
+
+def total_price_c(items):
+    total = 0
+    for item in items:
+        total += item["price"] * item["qty"]
+    return total
`

export default function Review() {
  const { id } = useRepo()
  const [mode, setMode] = useState('diff')
  const [diff, setDiff] = useState('')
  const [base, setBase] = useState('HEAD~1')
  const [head, setHead] = useState('HEAD')
  const [r, setR] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('findings')
  const run = async () => {
    setBusy(true); setErr(null); setR(null)
    try { setR(await api.post(`/api/repos/${id}/review`, mode === 'diff' ? { diff } : { base, head })) } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const by = (s) => r.findings.filter((f) => f.severity === s)
  return (
    <div className="page">
      <h1>PR guardrail</h1>
      <p className="muted" style={{ maxWidth: 820 }}>AI assistants write plausible code fast. This check uses the code graph to catch what reviewers miss: invented imports and APIs, duplicated logic that ignores existing helpers, new layering violations, untested changes, risky sinks, and the blast radius of the change.</p>
      <div className="card">
        <Tabs value={mode} onChange={setMode} tabs={[['diff', 'Paste a diff'], ['refs', 'Git refs']]} />
        {mode === 'diff' ? <>
          <textarea rows={11} value={diff} onChange={(e) => setDiff(e.target.value)} placeholder="Paste the output of `git diff` or a PR .diff here…" />
          <div className="small muted" style={{ marginTop: 6 }}>The diff is applied to a scratch copy of the indexed snapshot (never executed), re-indexed incrementally, and reviewed. Your index is not modified.</div>
        </> : <div className="row"><input type="text" value={base} onChange={(e) => setBase(e.target.value)} placeholder="base ref" /><input type="text" value={head} onChange={(e) => setHead(e.target.value)} placeholder="head ref" /></div>}
        <div className="row" style={{ marginTop: 10 }}><button className="primary" disabled={busy || (mode === 'diff' && !diff.trim())} onClick={run}>Run guardrail</button>
          {mode === 'diff' && <button className="ghost" onClick={() => setDiff(SAMPLE)}>load example (works on the bundled shop_app fixture)</button>}{busy && <Spinner />}</div>
      </div>
      <Err>{err}</Err>
      {r && <div style={{ marginTop: 16 }}>
        <div className={`verdict ${r.verdict}`}><span>{{ pass: '✅', warn: '⚠️', fail: '❌' }[r.verdict]}</span><b style={{ textTransform: 'uppercase' }}>{r.verdict}</b>
          <span className="small muted">{r.counts.high} high · {r.counts.medium} medium · {r.counts.low} low — {r.changed_files.length} files, {r.changed_symbols.length} symbols changed</span></div>
        {r.note && <p className="small muted">{r.note}</p>}
        <Tabs value={tab} onChange={setTab} tabs={[['findings', 'Findings', r.findings.length], ['blast', 'Blast radius'], ['md', 'PR comment (Markdown)']]} />
        {tab === 'findings' && (r.findings.length === 0 ? <div className="empty card">No issues found by the graph checks.</div> : ['high', 'medium', 'low'].map((s) => by(s).length > 0 && (
          <div className="card" key={s} style={{ marginBottom: 12 }}><h3><Badge kind={s}>{s}</Badge></h3>
            {by(s).map((f, i) => <div className="list-item" key={i}><b>{f.title}</b> <Badge>{f.category}</Badge><div className="small">{f.detail}</div>{f.path && <a className="small mono" href={fileLink(id, f.path, f.line || 1)}>{f.path}{f.line ? `:${f.line}` : ''}</a>}</div>)}</div>)))}
        {tab === 'blast' && r.blast_radius && <div className="grid g2"><div className="card"><h3>{r.blast_radius.risk.level} · {r.blast_radius.risk.score}/100</h3>{r.blast_radius.risk.reasons.map((x) => <div key={x} className="small">• {x}</div>)}
          <h3 style={{ marginTop: 14 }}>Tests to run</h3>{r.blast_radius.tests.length ? r.blast_radius.tests.map((t) => <div key={t.id} className="small mono">{t.path} · {t.name}</div>) : <div className="muted small">none reach this change</div>}</div>
          <div className="card" style={{ height: 380, padding: 0, overflow: 'hidden' }}><GraphCanvas minimap={false} nodes={r.graph.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind, role: n.role, top: n.path, children: 0 }))} edges={r.graph.edges.map((e) => ({ ...e, weight: 1 }))} /></div></div>}
        {tab === 'md' && <div className="card"><div className="row"><button onClick={() => navigator.clipboard.writeText(r.markdown)}>Copy</button></div><Markdown text={r.markdown} /></div>}
      </div>}
    </div>
  )
}
