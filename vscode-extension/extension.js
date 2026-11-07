// CodeWorld VS Code extension - a thin client over the CodeWorld REST API (no dependencies).
const vscode = require('vscode')
const cp = require('child_process')

let output, status, diagnostics
const cache = new Map()   // path -> {t, data}

const cfg = () => vscode.workspace.getConfiguration('codeworld')
const base = () => cfg().get('serverUrl').replace(/\/$/, '')
const rel = (uri) => vscode.workspace.asRelativePath(uri, false).replace(/\\/g, '/')

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const tok = cfg().get('apiToken')
  if (tok) headers['Authorization'] = 'Bearer ' + tok
  const res = await fetch(base() + path, { ...opts, headers })
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = j.detail || JSON.stringify(j) } catch (e) { /* ignore */ }
    throw new Error(`CodeWorld ${res.status}: ${msg}`)
  }
  return res.json()
}

async function repoId(interactive = true) {
  let id = cfg().get('repoId')
  if (id) return id
  const repos = (await api('/api/repos')).filter((r) => r.status === 'ready')
  const ws = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0].name
  const guess = repos.find((r) => r.name === ws)
  if (guess) { await cfg().update('repoId', guess.id, vscode.ConfigurationTarget.Workspace); return guess.id }
  if (!interactive) return null
  return selectRepo()
}

async function selectRepo() {
  const repos = (await api('/api/repos')).filter((r) => r.status === 'ready')
  if (!repos.length) { vscode.window.showWarningMessage('No indexed repositories. Index one in the CodeWorld web UI or with `codeworld index <path>`.'); return null }
  const pick = await vscode.window.showQuickPick(repos.map((r) => ({ label: r.name, description: r.id, detail: `${r.stats.files} files, ${r.stats.loc} LOC`, id: r.id })), { placeHolder: 'Choose the repository for this workspace' })
  if (!pick) return null
  await cfg().update('repoId', pick.id, vscode.ConfigurationTarget.Workspace)
  status.text = `$(globe) CodeWorld: ${pick.label}`
  cache.clear()
  return pick.id
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
function md(text) {
  let h = esc(text)
  h = h.replace(/```[\w]*\n([\s\S]*?)```/g, (_, c) => `<pre>${c}</pre>`)
  h = h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>')
  h = h.replace(/\[([\w./@-]+\.\w+):(\d+)(?:-(\d+))?\]/g, (_, p, a) => `<a href="command:codeworld.openLocation?${encodeURIComponent(JSON.stringify([p, +a]))}">${p}:${a}</a>`)
  return h.replace(/\n/g, '<br>')
}

function panel(title, html) {
  const p = vscode.window.createWebviewPanel('codeworld', title, vscode.ViewColumn.Beside, { enableCommandUris: true })
  p.webview.html = `<html><body style="font-family:var(--vscode-font-family);line-height:1.5;padding:8px 14px">${html}</body></html>`
}

async function openLocation(path, line) {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0]
  if (!folder) return
  const doc = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(folder.uri, path))
  const ed = await vscode.window.showTextDocument(doc)
  const pos = new vscode.Position(Math.max(0, (line || 1) - 1), 0)
  ed.selection = new vscode.Selection(pos, pos)
  ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter)
}

async function ask() {
  const id = await repoId(); if (!id) return
  const q = await vscode.window.showInputBox({ prompt: 'Ask about this codebase', placeHolder: 'e.g. who calls verify_password? · how does login work? · what breaks if I change X?' })
  if (!q) return
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'CodeWorld is searching the graph…' }, async () => {
    const r = await api(`/api/repos/${id}/ask`, { method: 'POST', body: JSON.stringify({ question: q, mode: 'graph', agent: true, history: [] }) })
    const ver = r.citation_precision == null ? '' : ` · ${Math.round(r.citation_precision * 100)}% citations verified`
    panel('CodeWorld', `<h3>${esc(q)}</h3>${md(r.answer)}<p style="opacity:.6">${esc(r.provider)} · ${r.latency_ms} ms${ver}</p>`)
  })
}

async function impactAtCursor() {
  const ed = vscode.window.activeTextEditor; if (!ed) return
  const id = await repoId(); if (!id) return
  const range = ed.document.getWordRangeAtPosition(ed.selection.active)
  const word = ed.selection.isEmpty ? (range ? ed.document.getText(range) : '') : ed.document.getText(ed.selection)
  const target = word || rel(ed.document.uri)
  const r = await api(`/api/repos/${id}/impact?target=${encodeURIComponent(target)}`)
  const li = (a) => a.map((d) => `<li><a href="command:codeworld.openLocation?${encodeURIComponent(JSON.stringify([d.path, d.line]))}">${esc(d.name)}</a> <small>${esc(d.path)}:${d.line}</small></li>`).join('')
  panel(`Impact: ${target}`, `<h3>${esc(r.target.join(', '))} - risk <b>${r.risk.level}</b> (${r.risk.score}/100)</h3><ul>${r.risk.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
    <h4>Direct dependents (${r.direct.length})</h4><ul>${li(r.direct)}</ul>
    <h4>Endpoints</h4><ul>${r.apis.map((a) => `<li>${esc(a.api)}</li>`).join('') || '<li>none</li>'}</ul>
    <h4>Tests that reach it</h4><ul>${r.tests.map((t) => `<li>${esc(t.name)} <small>${esc(t.path)}</small></li>`).join('') || '<li><b>none - add one before changing this</b></li>'}</ul>
    <h4>Usually changes together (git history)</h4><ul>${r.cochange.map((c) => `<li>${esc(c.path)} <small>${Math.round(c.confidence * 100)}%</small></li>`).join('') || '<li>no history</li>'}</ul>`)
}

function gitDiff(cwd) {
  return new Promise((resolve, reject) => cp.execFile('git', ['diff', '--unified=0', '--no-color', 'HEAD'], { cwd, maxBuffer: 20 * 1024 * 1024 }, (e, out) => (e ? reject(e) : resolve(out))))
}

async function reviewChanges() {
  const id = await repoId(); if (!id) return
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0]; if (!folder) return
  const diff = await gitDiff(folder.uri.fsPath)
  if (!diff.trim()) { vscode.window.showInformationMessage('No uncommitted changes to review.'); return }
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'CodeWorld PR guardrail…' }, async () => {
    const r = await api(`/api/repos/${id}/review`, { method: 'POST', body: JSON.stringify({ diff }) })
    diagnostics.clear()
    const byFile = new Map()
    for (const f of r.findings) {
      if (!f.path) continue
      const line = Math.max(0, (f.line || 1) - 1)
      const sev = f.severity === 'high' ? vscode.DiagnosticSeverity.Error : f.severity === 'medium' ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Information
      const d = new vscode.Diagnostic(new vscode.Range(line, 0, line, 200), `${f.title} - ${f.detail}`, sev)
      d.source = 'CodeWorld'; d.code = f.category
      if (!byFile.has(f.path)) byFile.set(f.path, [])
      byFile.get(f.path).push(d)
    }
    for (const [p, ds] of byFile) diagnostics.set(vscode.Uri.joinPath(folder.uri, p), ds)
    output.clear(); output.appendLine(r.markdown); output.show(true)
    vscode.window.showInformationMessage(`CodeWorld guardrail: ${r.verdict.toUpperCase()} (${r.counts.high} high, ${r.counts.medium} medium, ${r.counts.low} low). See Problems panel.`)
  })
}

async function proposePatch() {
  const id = await repoId(); if (!id) return
  const task = await vscode.window.showInputBox({ prompt: 'Describe the change you want', placeHolder: 'e.g. make the token length configurable' })
  if (!task) return
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'CodeWorld is drafting a patch…' }, async () => {
    const r = await api(`/api/repos/${id}/propose`, { method: 'POST', body: JSON.stringify({ task }) })
    if (!r.ok) { vscode.window.showWarningMessage(r.report.slice(0, 300)); return }
    const doc = await vscode.workspace.openTextDocument({ content: r.diff, language: 'diff' })
    await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside)
    vscode.window.showInformationMessage(`Patch drafted - guardrail verdict: ${r.review.verdict.toUpperCase()}. Review the diff, then apply with: git apply <file>.`)
  })
}

class Lens {
  constructor() { this._e = new vscode.EventEmitter(); this.onDidChangeCodeLenses = this._e.event }
  refresh() { cache.clear(); this._e.fire() }
  async provideCodeLenses(doc) {
    if (!cfg().get('codeLens') || doc.uri.scheme !== 'file') return []
    const id = await repoId(false); if (!id) return []
    const p = rel(doc.uri)
    let hit = cache.get(p)
    if (!hit || Date.now() - hit.t > 30000) {
      try { hit = { t: Date.now(), data: await api(`/api/repos/${id}/lens?path=${encodeURIComponent(p)}`) } } catch (e) { return [] }
      cache.set(p, hit)
    }
    return hit.data.map((s) => {
      const range = new vscode.Range(Math.max(0, s.line - 1), 0, Math.max(0, s.line - 1), 0)
      const bits = [`${s.callers} caller${s.callers === 1 ? '' : 's'}`, s.tested ? 'tested ✓' : 'untested ✗']
      if (s.route) bits.push(`${s.route.method} ${s.route.path}`)
      if (s.effects.length) bits.push('⚠ ' + s.effects.join(','))
      if (s.public_api) bits.push('public API')
      return new vscode.CodeLens(range, { title: bits.join(' · '), command: 'codeworld.impactAtCursor', tooltip: 'CodeWorld: show impact' })
    })
  }
}

function activate(ctx) {
  output = vscode.window.createOutputChannel('CodeWorld')
  diagnostics = vscode.languages.createDiagnosticCollection('codeworld')
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50)
  status.text = '$(globe) CodeWorld'; status.command = 'codeworld.selectRepo'; status.show()
  const lens = new Lens()
  const safe = (fn) => async (...a) => { try { await fn(...a) } catch (e) { vscode.window.showErrorMessage(String(e.message || e)) } }
  const reg = (id, fn) => ctx.subscriptions.push(vscode.commands.registerCommand(id, safe(fn)))
  reg('codeworld.selectRepo', selectRepo)
  reg('codeworld.ask', ask)
  reg('codeworld.impactAtCursor', impactAtCursor)
  reg('codeworld.reviewChanges', reviewChanges)
  reg('codeworld.proposePatch', proposePatch)
  reg('codeworld.openLocation', openLocation)
  reg('codeworld.reindex', async () => { const id = await repoId(); if (id) { await api(`/api/repos/${id}/reindex`, { method: 'POST', body: '{}' }); lens.refresh(); vscode.window.showInformationMessage('CodeWorld: re-indexing started (incremental).') } })
  reg('codeworld.openMap', async () => { const id = await repoId(); if (!id) return; const ed = vscode.window.activeTextEditor
    const focus = ed ? `?focus=${encodeURIComponent('file:' + rel(ed.document.uri))}` : ''
    vscode.env.openExternal(vscode.Uri.parse(`${base()}/#/r/${id}/map${focus}`)) })
  ctx.subscriptions.push(output, diagnostics, status,
    vscode.languages.registerCodeLensProvider([{ language: 'python' }, { language: 'javascript' }, { language: 'typescript' }, { language: 'typescriptreact' }, { language: 'javascriptreact' }], lens),
    vscode.workspace.onDidSaveTextDocument(() => lens.refresh()))
}

function deactivate() {}
module.exports = { activate, deactivate }
