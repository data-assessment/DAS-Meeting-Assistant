import { useEffect, useState } from 'react'

interface Field {
  key: string
  label: string
  type: 'text' | 'number' | 'bool' | 'select' | 'combo' | 'list' | 'secret'
  options?: string[]
  restart?: boolean
  help?: string
  visibleWhenContains?: { key: string; value: string }
  visibleWhenEquals?: { key: string; value: string }
  visibleWhenAllEquals?: { key: string; value: string }[]
}
interface Group { group: string; fields: Field[] }
type Val = string | boolean | string[]
type Values = Record<string, Val>
interface EnvInfo {
  userEnvPath?: string
  userEnvExists?: boolean
  bundledEnvExists?: boolean
  managedBuild?: boolean
  version?: string
}

/** Ordered list editor: reorder (top = default), remove, and add entries. */
function ListEditor({ field, items, onChange }: {
  field: Field; items: string[]; onChange: (v: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const isModelList = field.key.endsWith('_MODELS')
  const placeholder = field.key === 'ONENOTE_SITE_PATHS'
    ? 'add SharePoint site URL…'
    : field.key === 'STT_DICTIONARY' ? 'add business term…'
    : isModelList ? 'add model…' : 'add item…'
  const move = (i: number, d: number) => {
    const next = items.slice()
    const j = i + d
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  const update = (i: number, v: string) => onChange(items.map((item, k) => k === i ? v : item))
  const remove = (i: number) => onChange(items.filter((_, k) => k !== i))
  const add = () => {
    const v = draft.trim()
    if (v && !items.includes(v)) onChange([...items, v])
    setDraft('')
  }
  return (
    <div className="list-editor">
      {items.map((it, i) => (
        <div className="list-row" key={i}>
          <span className="list-item">
            <input className="list-edit" type="text" value={it}
              onChange={(e) => update(i, e.target.value)} />
            {isModelList && i === 0 && <em className="list-default"> · default</em>}
          </span>
          <span className="list-actions">
            <button className="mini" disabled={i === 0} onClick={() => move(i, -1)} title="Move up">↑</button>
            <button className="mini" disabled={i === items.length - 1} onClick={() => move(i, 1)} title="Move down">↓</button>
            <button className="mini" onClick={() => remove(i)} title="Remove">✕</button>
          </span>
        </div>
      ))}
      <div className="list-add">
        <input type="text" value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button className="mini" onClick={add} disabled={!draft.trim()}>Add</button>
      </div>
    </div>
  )
}

function FieldInput({ field, value, secretSet, onChange }: {
  field: Field; value: Val; secretSet: boolean; onChange: (v: Val) => void
}) {
  switch (field.type) {
    case 'bool':
      return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
    case 'select':
      return (
        <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {(field.options ?? []).map((o) => <option key={o} value={o}>{o || (field.key === 'STT_LANGUAGE' ? 'Auto' : '(default)')}</option>)}
        </select>
      )
    case 'combo':
      return (
        <>
          <input type="text" list={`dl-${field.key}`} value={String(value ?? '')}
            onChange={(e) => onChange(e.target.value)} />
          <datalist id={`dl-${field.key}`}>
            {(field.options ?? []).map((o) => <option key={o} value={o} />)}
          </datalist>
        </>
      )
    case 'list':
      return <ListEditor field={field} items={Array.isArray(value) ? value : []} onChange={onChange} />
    case 'secret':
      return (
        <input type="password" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}
          placeholder={secretSet ? '•••••••• (set — blank keeps it)' : 'not set'} />
      )
    case 'number':
      return <input type="number" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />
    default:
      return <input type="text" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />
  }
}

function GraphConnect() {
  const [st, setSt] = useState<{
    configured?: boolean
    connected?: boolean
    aiMode?: string
    cloudConnected?: boolean
    cloudReason?: string
    sharePointSitesConfigured?: boolean
    sharePointConnected?: boolean
    reason?: string
  }>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState<'refresh' | 'signin' | ''>('')
  const refresh = () => {
    setBusy('refresh')
    setMsg('Checking Microsoft Graph sign-in…')
    fetch('/api/graph/status')
      .then((r) => r.json())
      .then((s) => {
        setSt(s)
        if (!s.connected) setMsg(s.reason || 'Sign in again to grant current Graph permissions.')
        else if (['gateway', 'entra'].includes(s.aiMode || '') && !s.cloudConnected) {
          setMsg(s.cloudReason || 'Cloud AI access needs Microsoft consent.')
        } else if (s.sharePointSitesConfigured && !s.sharePointConnected) {
          setMsg('Microsoft Graph is connected. SharePoint site notebooks need admin-approved site access.')
        } else setMsg('Microsoft Graph sign-in is current.')
      })
      .catch(() => setMsg('Could not check Microsoft Graph sign-in.'))
      .finally(() => setBusy(''))
  }
  useEffect(refresh, [])
  const signIn = () => {
    setBusy('signin')
    setMsg('Opening browser — complete Microsoft consent…')
    fetch('/api/sign-in', { method: 'POST' })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { setMsg(d.error || 'Microsoft sign-in failed.'); return }
        setMsg('Connected.'); refresh()
      })
      .catch(() => setMsg('Microsoft sign-in failed.'))
      .finally(() => setBusy(''))
  }
  const grantSharePoint = () => {
    setBusy('signin')
    setMsg('Opening browser — SharePoint site access may require admin approval…')
    fetch('/api/graph/sharepoint-sign-in', { method: 'POST' })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { setMsg(d.error || 'SharePoint site access was not granted.'); return }
        setMsg('SharePoint site access granted.'); refresh()
      })
      .catch(() => setMsg('SharePoint site access was not granted.'))
      .finally(() => setBusy(''))
  }
  const busyNow = Boolean(busy)
  return (
    <div className="connection-row">
      <span className="connection-status">
        {st.connected && (!['gateway', 'entra'].includes(st.aiMode || '') || st.cloudConnected)
          ? '● Connected'
          : st.configured ? '○ Sign-in refresh needed' : '○ Save Graph Client ID first'}
      </span>
      <span className="settings-buttons">
        <button className="ghost" onClick={refresh} disabled={busyNow}>{busy === 'refresh' ? 'Refreshing…' : 'Refresh'}</button>
        <button className="primary" onClick={signIn} disabled={!st.configured || busyNow}>{busy === 'signin' ? 'Opening…' : 'Sign in again'}</button>
        {st.sharePointSitesConfigured && !st.sharePointConnected && (
          <button className="ghost" onClick={grantSharePoint} disabled={!st.configured || busyNow}>Grant SharePoint sites</button>
        )}
      </span>
      {msg && <div className="status-message">{msg}</div>}
    </div>
  )
}

function isVisible(field: Field, values: Values): boolean {
  const allEqual = field.visibleWhenAllEquals
  if (allEqual?.some((condition) => String(values[condition.key] ?? '') !== condition.value)) return false
  const equal = field.visibleWhenEquals
  if (equal && String(values[equal.key] ?? '') !== equal.value) return false
  const cond = field.visibleWhenContains
  if (!cond) return true
  const v = values[cond.key]
  const hay = Array.isArray(v) ? v.join(',') : String(v ?? '')
  return hay.toLowerCase().includes(cond.value.toLowerCase())
}

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [schema, setSchema] = useState<Group[]>([])
  const [values, setValues] = useState<Values>({})
  const [secretsSet, setSecretsSet] = useState<Record<string, boolean>>({})
  const [envInfo, setEnvInfo] = useState<EnvInfo>({})
  const [status, setStatus] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    fetch('/api/settings').then((r) => r.json()).then((d) => {
      setSchema(d.schema || [])
      setValues(d.values || {})
      setSecretsSet(d.secretsSet || {})
      setEnvInfo(d.envInfo || {})
      setLoaded(true)
    }).catch(() => setStatus('Failed to load settings'))
  }, [])

  const set = (k: string, v: Val) => setValues((s) => ({ ...s, [k]: v }))

  const save = () => {
    setStatus('Saving…')
    fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { setStatus(d.error || 'Save failed'); return }
        if (d.restartNeeded) setStatus(`Saved. Restart to apply: ${(d.restartKeys || []).join(', ')}`)
        else onClose()  // applied live — close
      })
      .catch(() => setStatus('Save failed'))
  }

  return (
    <div className="settings">
      <div className="settings-head"><span>Settings</span></div>
      <div className="settings-body">
        {!loaded && <div className="muted">Loading…</div>}
        {loaded && envInfo.userEnvPath && !envInfo.managedBuild && (
          <div className="status-message">
            User settings override: {envInfo.userEnvPath}
            {envInfo.bundledEnvExists ? ' · bundled defaults loaded' : ''}
          </div>
        )}
        {schema.map((g) => (
          <fieldset key={g.group} className="settings-group">
            <legend>{g.group}</legend>
            {g.fields.filter((f) => isVisible(f, values)).map((f) => (
              <label key={f.key} className={`settings-field${f.type === 'list' ? ' field-block' : ''}`}
                title={f.help || ''}>
                <span className="settings-flabel">
                  {f.label}{f.restart && <em className="restart-tag"> · restart</em>}
                </span>
                <FieldInput field={f} value={values[f.key]} secretSet={!!secretsSet[f.key]}
                  onChange={(v) => set(f.key, v)} />
              </label>
            ))}
            {g.group === 'Microsoft sign-in (Graph)' && <GraphConnect />}
            {g.group === 'Managed service' && (
              <div className="status-message">
                Cloud AI identity, routing, API versions, and available models are managed by
                your organization and cannot be changed on this device.
                {envInfo.version ? ` · App ${envInfo.version}` : ''}
              </div>
            )}
          </fieldset>
        ))}
      </div>
      <div className="settings-foot">
        <span className="settings-status">{status}</span>
        <span className="settings-buttons">
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button className="primary" onClick={save} disabled={!loaded}>Save</button>
        </span>
      </div>
    </div>
  )
}
