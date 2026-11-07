import React, { useEffect, useMemo, useRef } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { createPortal } from 'react-dom'
import { fileLink } from './common.jsx'

let mermaidP
const loadMermaid = () => mermaidP || (mermaidP = import('mermaid').then((m) => {
  m.default.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict', flowchart: { useMaxWidth: true, htmlLabels: false } })
  return m.default
}))
let n = 0

export function Mermaid({ code }) {
  const ref = useRef()
  useEffect(() => {
    let live = true
    loadMermaid().then(async (m) => {
      try {
        const { svg } = await m.render('mm' + ++n, code)
        if (live && ref.current) ref.current.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true }, ADD_TAGS: ['foreignObject'] })
      } catch (e) { if (live && ref.current) ref.current.innerHTML = '<pre>' + code.replace(/</g, '&lt;') + '</pre>' }
    })
    return () => { live = false }
  }, [code])
  return <div ref={ref} style={{ overflow: 'auto', textAlign: 'center' }} />
}

const CITE = /\[([\w./@-]+\.\w+):(\d+)(?:-(\d+))?\]/g

export default function Markdown({ text, repoId }) {
  const { html, diagrams } = useMemo(() => {
    const diagrams = []
    let src = text || ''
    src = src.replace(/```mermaid\n([\s\S]*?)```/g, (_, c) => { diagrams.push(c); return `<div data-mm="${diagrams.length - 1}"></div>` })
    if (repoId) src = src.replace(CITE, (_, p, a) => `[${_.slice(1, -1)}](${fileLink(repoId, p, a)})`)
    const html = DOMPurify.sanitize(marked.parse(src, { breaks: true }), { ADD_ATTR: ['data-mm'] })
    return { html, diagrams }
  }, [text, repoId])
  const ref = useRef()
  const [slots, setSlots] = React.useState([])
  useEffect(() => { setSlots(ref.current ? [...ref.current.querySelectorAll('[data-mm]')] : []) }, [html])
  return (
    <>
      <div className="md" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
      {slots.map((el, i) => el.isConnected ? createPortal(<Mermaid key={i} code={diagrams[+el.dataset.mm]} />, el) : null)}
    </>
  )
}
