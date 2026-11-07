const BASE = import.meta.env.VITE_API_URL || ''

const tok = () => localStorage.getItem('cw_token') || ''

async function req(path, opts = {}, retry = true) {
  const headers = { ...(opts.headers || {}) }
  if (tok()) headers['Authorization'] = 'Bearer ' + tok()
  const res = await fetch(BASE + path, { ...opts, headers })
  if (res.status === 401 && retry) {                       // server started with CODEWORLD_API_TOKEN
    const t = window.prompt('This CodeWorld server requires an API token:')
    if (t) { localStorage.setItem('cw_token', t.trim()); return req(path, opts, false) }
  }
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = j.detail || JSON.stringify(j) } catch {}
    throw new Error(msg)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('json') ? res.json() : res.text()
}

export const api = {
  get: (p) => req(p),
  post: (p, body) => req(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  del: (p) => req(p, { method: 'DELETE' }),
  upload: (file) => { const f = new FormData(); f.append('file', file); return req('/api/repos/upload', { method: 'POST', body: f }) },
}

export const q = (obj) => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString()
