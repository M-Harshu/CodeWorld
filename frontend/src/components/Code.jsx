import React, { useEffect, useRef } from 'react'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export default function Code({ lines, language = 'python', start = 1, mark, maxHeight }) {
  const ref = useRef()
  useEffect(() => {
    if (mark && ref.current) {
      const el = ref.current.querySelector('.ln.on')
      el && el.scrollIntoView({ block: 'center' })
    }
  }, [mark?.[0], lines])
  const lang = hljs.getLanguage(language) ? language : null
  return (
    <div className="code" ref={ref} style={{ maxHeight, overflow: 'auto' }}>
      {lines.map((l, i) => {
        const no = start + i
        let h
        try { h = lang ? hljs.highlight(l, { language: lang, ignoreIllegals: true }).value : esc(l) } catch { h = esc(l) }
        return (
          <div key={i} className={'ln' + (mark && no >= mark[0] && no <= mark[1] ? ' on' : '')}>
            <span className="no">{no}</span><span className="tx" dangerouslySetInnerHTML={{ __html: h || ' ' }} />
          </div>)
      })}
    </div>
  )
}
