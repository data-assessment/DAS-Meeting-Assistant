import { useEffect, useMemo, useState } from 'react'

interface DocEntry { id: string; title: string; summary?: string }

function renderInline(text: string) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    return part
  })
}

function MarkdownView({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const lines = text.replace(/\r\n/g, '\n').split('\n')
    const nodes: JSX.Element[] = []
    let list: string[] = []
    const flushList = () => {
      if (!list.length) return
      const items = list
      list = []
      nodes.push(<ul key={`ul-${nodes.length}`}>{items.map((item, index) => <li key={index}>{renderInline(item)}</li>)}</ul>)
    }
    lines.forEach((line) => {
      const trimmed = line.trim()
      if (!trimmed) { flushList(); return }
      if (trimmed.startsWith('- ')) {
        list.push(trimmed.slice(2))
        return
      }
      flushList()
      if (trimmed.startsWith('# ')) nodes.push(<h1 key={nodes.length}>{trimmed.slice(2)}</h1>)
      else if (trimmed.startsWith('## ')) nodes.push(<h2 key={nodes.length}>{trimmed.slice(3)}</h2>)
      else nodes.push(<p key={nodes.length}>{renderInline(trimmed)}</p>)
    })
    flushList()
    return nodes
  }, [text])
  return <div className="docs-content">{blocks}</div>
}

export function DocsDialog({ onClose }: { onClose: () => void }) {
  const [entries, setEntries] = useState<DocEntry[]>([])
  const [selected, setSelected] = useState('')
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch('/api/docs')
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) { setErr(data.error || 'Documentation unavailable'); return }
        const nextEntries = Array.isArray(data.docs) ? data.docs : []
        setEntries(nextEntries)
        setSelected(nextEntries[0]?.id || '')
      })
      .catch(() => setErr('Documentation unavailable'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selected) return
    setLoading(true)
    setErr('')
    fetch(`/api/docs/${encodeURIComponent(selected)}`)
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) { setErr(data.error || 'Document unavailable'); setText(''); return }
        setText(data.content || '')
      })
      .catch(() => { setErr('Document unavailable'); setText('') })
      .finally(() => setLoading(false))
  }, [selected])

  return (
    <div className="settings docs-dialog">
      <div className="settings-head"><span>Documentation</span></div>
      <div className="settings-body docs-body">
        {err && <div className="dialog-error"><pre className="dialog-error-text">{err}</pre></div>}
        <aside className="docs-nav" aria-label="Documentation topics">
          {entries.map((entry) => (
            <button key={entry.id} className={entry.id === selected ? 'active' : ''}
              onClick={() => setSelected(entry.id)}>
              <strong>{entry.title}</strong>
              {entry.summary && <span>{entry.summary}</span>}
            </button>
          ))}
        </aside>
        <section className="docs-panel">
          {loading && !text ? (
            <div className="dialog-loading" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>Loading documentation...</span>
            </div>
          ) : text ? <MarkdownView text={text} /> : null}
        </section>
      </div>
      <div className="settings-foot">
        <span className="settings-status">Bundled app documentation</span>
        <span className="settings-buttons"><button className="ghost" onClick={onClose}>Close</button></span>
      </div>
    </div>
  )
}
