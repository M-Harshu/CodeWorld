import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import { Badge, Spinner, useFetch, useRepo } from '../components/common.jsx'
import Markdown from '../components/Markdown.jsx'

const MODES = [
  ['auto', 'Auto (graph for relational questions)'],
  ['graph', 'Graph-RAG always (BM25 + vectors + call graph)'],
  ['hybrid', 'Hybrid (BM25 + vectors)'],
  ['vector', 'Vectors only'],
  ['bm25', 'BM25 only']
]

// Fallback model list used before /api/health responds.
// Real list (with env-driven model IDs) comes from health.models.
const DEFAULT_MODELS = [
  { id: 'gemini', provider: 'gemini', model: 'gemini-3.5-flash-lite', name: '⚡ Google Gemini', badge: 'Fast · Vision', vision: true },
  { id: 'openrouter', provider: 'openrouter', model: 'nvidia/nemotron-3-ultra-550b-a55b:free', name: '🧠 OpenRouter 550B', badge: 'Reasoning', vision: false },
  { id: 'ollama', provider: 'ollama', model: 'qwen3.5:4b', name: '💻 Ollama Local', badge: 'Offline', vision: false }
]

export default function Ask() {
  const { id, repo } = useRepo()
  const nav = useNavigate()
  const [sp] = useSearchParams()

  // 1. Conversation state retained across tab navigation via localStorage
  const [msgs, setMsgs] = useState(() => {
    try {
      const saved = localStorage.getItem(`codeworld_chat_${id}`)
      return saved ? JSON.parse(saved) : []
    } catch {
      return []
    }
  })

  // 2. Persisted model selection
  const [selectedModel, setSelectedModel] = useState(() => {
    return localStorage.getItem('codeworld_selected_model') || 'gemini'
  })

  const [text, setText] = useState(sp.get('q') || '')
  const [mode, setMode] = useState('auto')
  const [agent, setAgent] = useState(true)
  const [busy, setBusy] = useState(false)
  const [image, setImage] = useState(null) // Data URL for multimodal image input

  const end = useRef()
  const fileInputRef = useRef()
  const { data: health } = useFetch(() => api.get('/api/health'), [])
  const { data: ov } = useFetch(() => api.get(`/api/repos/${id}/onboarding`), [id])

  // Use API-driven model list (env vars control model IDs), fallback to defaults
  const MODELS = (health?.models?.length > 0)
    ? health.models.map(m => ({
        ...m,
        name: m.id === 'gemini' ? `⚡ ${m.name}` :
              m.id === 'openrouter' ? `🧠 ${m.name}` :
              m.id === 'ollama' ? `💻 ${m.name}` : m.name
      }))
    : DEFAULT_MODELS

  // Sync chat messages to localStorage whenever they change
  useEffect(() => {
    try {
      localStorage.setItem(`codeworld_chat_${id}`, JSON.stringify(msgs))
    } catch (e) {
      console.warn('Failed to persist chat', e)
    }
  }, [msgs, id])

  // Sync selected model to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('codeworld_selected_model', selectedModel)
    } catch {}
  }, [selectedModel])

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, busy, image])

  const clearChat = () => {
    if (msgs.length > 0 && !window.confirm('Clear conversation history?')) return
    setMsgs([])
    try {
      localStorage.removeItem(`codeworld_chat_${id}`)
    } catch {}
  }

  // Handle image file selection (2 MB size cap to avoid localStorage quota issues)
  const handleImageFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 2_000_000) {
      alert('Image must be under 2 MB. Please resize or crop it first.')
      e.target.value = ''
      return
    }
    const reader = new FileReader()
    reader.onload = (ev) => setImage(ev.target.result)
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  // Handle clipboard paste (Ctrl+V) for screenshots
  const handlePaste = (e) => {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          const reader = new FileReader()
          reader.onload = (ev) => setImage(ev.target.result)
          reader.readAsDataURL(file)
          e.preventDefault()
          break
        }
      }
    }
  }

  const send = async (qtext) => {
    const question = (qtext ?? text).trim()
    if ((!question && !image) || busy) return

    const currentImage = image
    setImage(null)

    // Construct conversation history for multi-turn context
    const history = msgs.slice(-8).map((m) => {
      const item = { role: m.role === 'bot' ? 'assistant' : 'user', content: m.text }
      if (m.r?.reasoning_details) item.reasoning_details = m.r.reasoning_details
      return item
    })

    const newMsg = { role: 'user', text: question || '(Image query)', image: currentImage }
    setMsgs((m) => [...m, newMsg])
    setText('')
    setBusy(true)

    try {
      const activeModelObj = MODELS.find((m) => m.id === selectedModel)
      const payload = {
        question: question || 'Please describe and analyze this image in relation to the codebase.',
        mode,
        agent,
        history,
        provider: activeModelObj?.provider || selectedModel,
        model: activeModelObj?.model,
        image: currentImage
      }
      const r = await api.post(`/api/repos/${id}/ask`, payload)
      setMsgs((m) => [...m, { role: 'bot', text: r.answer, r }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'bot', text: '**Error:** ' + e.message }])
    }
    setBusy(false)
  }

  const first = ov?.key_modules?.[0]?.path
  const ep = ov?.entry_points?.find((e) => e.kind === 'endpoint')
  const sug = [
    'What does this project do and how is it organised?',
    ep && `How does ${ep.name} work end to end?`,
    first && `What breaks if I change ${first}?`,
    'Is there any security risk in this codebase?',
    'Which code has no tests?'
  ].filter(Boolean)

  const activeModelMeta = MODELS.find((m) => m.id === selectedModel) || MODELS[0]

  return (
    <div className="chat" onPaste={handlePaste}>
      {/* Topbar with repo name, active model selector, and clear button */}
      <div className="topbar">
        <span className="name">Ask {repo.name}</span>
        <span className="grow" />

        {/* Model switcher dropdown */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>Model:</span>
          <select
            style={{ width: 'auto', padding: '4px 8px', fontSize: 13, fontWeight: 500 }}
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.badge})
              </option>
            ))}
          </select>
        </div>

        <Badge kind={activeModelMeta.vision ? 'ok' : 'medium'}>{activeModelMeta.badge}</Badge>

        {msgs.length > 0 && (
          <button
            className="ghost"
            style={{ fontSize: 12, marginLeft: 8, padding: '4px 8px' }}
            onClick={clearChat}
            title="Clear saved conversation"
          >
            🗑️ Clear
          </button>
        )}
      </div>

      {/* Messages view */}
      <div className="msgs">
        {msgs.length === 0 && (
          <div className="card" style={{ maxWidth: 760 }}>
            <h2>Ask anything about this codebase</h2>
            <p className="muted">
              Conversation history is preserved across tabs. You can attach or paste (Ctrl+V) images, screenshots of UI, or architecture diagrams to query.
            </p>
            <div className="row" style={{ marginTop: 12 }}>
              {sug.map((s) => (
                <span key={s} className="badge chip" onClick={() => send(s)}>
                  {s}
                </span>
              ))}
            </div>
          </div>
        )}

        {msgs.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="msg user">
              {m.image && (
                <div style={{ marginBottom: 8 }}>
                  <img
                    src={m.image}
                    alt="Attached screenshot"
                    style={{ maxWidth: 360, maxHeight: 220, borderRadius: 6, display: 'block', objectFit: 'contain' }}
                  />
                </div>
              )}
              <div>{m.text}</div>
            </div>
          ) : (
            <div key={i} className="msg bot">
              <Markdown text={m.text} repoId={id} />

              {/* Reasoning chain (if model provided reasoning details) */}
              {m.r?.reasoning_details && (
                <details style={{ marginTop: 8 }}>
                  <summary className="small muted">🧠 View deep reasoning details</summary>
                  <div className="reasoning-box mono">
                    {typeof m.r.reasoning_details === 'string'
                      ? m.r.reasoning_details
                      : JSON.stringify(m.r.reasoning_details, null, 2)}
                  </div>
                </details>
              )}

              {m.r && (
                <div style={{ marginTop: 10 }}>
                  <div className="row small muted">
                    <Badge>{m.r.intent}</Badge>
                    <span>{m.r.provider}</span>
                    <span>{m.r.latency_ms} ms</span>
                    {m.r.citation_precision != null && (
                      <Badge kind={m.r.citation_precision === 1 ? 'ok' : 'medium'}>
                        {Math.round(m.r.citation_precision * 100)}% citations verified
                      </Badge>
                    )}
                    {m.r.highlight?.length > 0 && (
                      <button
                        className="ghost"
                        onClick={() => nav(`/r/${id}/map?hl=${encodeURIComponent(m.r.highlight.join('|'))}`)}
                      >
                        ⌖ show on map
                      </button>
                    )}
                  </div>
                  {m.r.notes?.map((n, k) => (
                    <div key={k} className="small" style={{ color: 'var(--warn)' }}>
                      {n}
                    </div>
                  ))}
                  <details>
                    <summary>
                      How this answer was built ({m.r.trace.length} step{m.r.trace.length === 1 ? '' : 's'})
                    </summary>
                    {m.r.trace.map((t, k) => (
                      <div key={k} className="small mono" style={{ padding: '3px 0' }}>
                        {t.tool}({JSON.stringify(t.args).slice(0, 90)}) → {t.summary}
                      </div>
                    ))}
                  </details>
                </div>
              )}
            </div>
          )
        )}
        {busy && (
          <div className="msg bot">
            <Spinner label={`Thinking with ${activeModelMeta.name}…`} />
          </div>
        )}
        <div ref={end} />
      </div>

      {/* Composer */}
      <div className="composer">
        {/* Image preview chip */}
        {image && (
          <div className="image-preview-chip">
            <img src={image} alt="Preview" />
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>
              Screenshot attached ({activeModelMeta.vision ? 'ready for Gemini vision' : 'tip: select Gemini for vision'})
            </span>
            <button
              type="button"
              className="ghost small"
              onClick={() => setImage(null)}
              style={{ marginLeft: 'auto', padding: '2px 8px' }}
            >
              ✕ Remove
            </button>
          </div>
        )}

        <div className="row" style={{ marginBottom: 8 }}>
          <select style={{ width: 'auto' }} value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          <label className="small">
            <input type="checkbox" checked={agent} onChange={(e) => setAgent(e.target.checked)} /> agent tools (callers, impact, tests…)
          </label>
        </div>

        <div className="row">
          {/* Hidden file input for images */}
          <input
            type="file"
            ref={fileInputRef}
            accept="image/*"
            style={{ display: 'none' }}
            onChange={handleImageFile}
          />
          <button
            type="button"
            className="ghost"
            title="Attach image or paste (Ctrl+V) screenshot"
            onClick={() => fileInputRef.current?.click()}
            style={{ padding: '0 10px', fontSize: 16 }}
          >
            🖼️
          </button>

          <input
            type="text"
            value={text}
            placeholder={
              image
                ? 'Ask a question about this image/screenshot…'
                : 'Ask anything, or paste a screenshot (Ctrl+V) · e.g. who calls verify_password? · how does login work?'
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
          />

          <button className="primary" disabled={busy || (!text.trim() && !image)} onClick={() => send()}>
            Ask
          </button>
        </div>
      </div>
    </div>
  )
}
