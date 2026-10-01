import { useEffect, useRef, useState } from 'react'
import { t } from './i18n'

export type Person = { id: string; name: string; email: string; source: 'calendar' | 'self' | 'manual' | 'call' | 'contact' }
export type Question = { id: string; label: string; options: string[]; answer: string; field: 'context' | 'owner' | 'recipient' }
export type Task = { included?: boolean; suggested?: boolean; id: string; title: string; owner: string; ownerId: string; recipient: string; due: string; uncertainty: string; questions: Question[] }
export type Draft = { summary: string; decisions: string; openQuestions: string; tasks: Task[]; people: Person[] }
export const questionOpen = (q: Question, task: Task) => q.field === 'owner' ? !task.owner.trim() : q.field === 'recipient' ? !task.recipient.trim() && !q.answer.trim() : !q.answer.trim()
export const unresolved = (task: Task) => task.included !== false && (!task.title.trim() || !task.owner.trim() || task.questions.some(q => questionOpen(q, task)))

function Pencil() { return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m16 3 5 5-13 13H3v-5L16 3Z M13 6l5 5" /></svg> }

function InlineText({ value, label, editable, change, max = 1000, title = false }: {
  value: string; label: string; editable: boolean; change: (value: string) => void; max?: number; title?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [hint, setHint] = useState('')
  const original = useRef(value), current = useRef(value), held = useRef(false), pending = useRef(false)
  const input = useRef<HTMLTextAreaElement>(null), button = useRef<HTMLButtonElement>(null)
  const changeRef = useRef(change); changeRef.current = change; current.current = value
  const end = (cancel = false) => {
    pending.current = false
    if (cancel || (title && !current.current.trim())) {
      changeRef.current(original.current)
      setHint(cancel ? t('tasks.inline.reverted') : t('tasks.inline.emptyDiscarded'))
    }
    setEditing(false)
  }
  const endRef = useRef(end); endRef.current = end
  useEffect(() => {
    const down = () => { held.current = true }
    const up = () => { held.current = false; if (pending.current) window.setTimeout(() => { if (pending.current) endRef.current() }, 0) }
    document.addEventListener('pointerdown', down, true); document.addEventListener('pointerup', up, true); document.addEventListener('pointercancel', up, true)
    return () => { pending.current = false; document.removeEventListener('pointerdown', down, true); document.removeEventListener('pointerup', up, true); document.removeEventListener('pointercancel', up, true) }
  }, [])
  useEffect(() => { if (editing && input.current) { input.current.style.height = 'auto'; input.current.style.height = input.current.scrollHeight + 2 + 'px' } }, [editing, value])
  const showValue = value || (title ? t('tasks.inline.describeTask') : t('tasks.inline.enterAnswer'))
  return <div className={title ? 'inline-task-title' : 'inline-task-value'}>
    {editable && editing ? <><textarea ref={input} autoFocus aria-label={label} value={value} maxLength={max} rows={2}
      onChange={e => change(e.target.value)} onBlur={() => { if (held.current) pending.current = true; else end() }}
      onKeyDown={e => { if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Escape') { e.preventDefault(); end(e.key === 'Escape'); window.setTimeout(() => button.current?.focus(), 0) } }} />
      <div className="inline-hint">{t('tasks.inline.hint')}</div></>
      : editable ? <button ref={button} className="inline-edit" aria-label={label + ': ' + showValue} onClick={() => { original.current = value; setHint(''); setEditing(true) }}><span>{showValue}</span><Pencil /></button>
      : <span className="task-title">{showValue}</span>}
    {hint && <div className="inline-hint" role="status">{hint}</div>}
  </div>
}

export function NotesTasks({ draft, people, peopleNote, editable, expanded, change }: {
  draft: Draft; people: Person[]; peopleNote: string; editable: boolean; expanded: boolean;
  change: (update: (current: Draft) => Draft) => void
}) {
  const [newFor, setNewFor] = useState<string | null>(null), [name, setName] = useState(''), [email, setEmail] = useState(''), [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const merged = new Map<string, Person>()
  for (const p of [...people, ...draft.people]) if (!merged.has(p.id)) merged.set(p.id, p)
  const persons = [...merged.values()]
  const patch = (id: string, update: Partial<Task> | ((task: Task) => Task)) => change(d => ({ ...d, tasks: d.tasks.map(t => t.id === id ? typeof update === 'function' ? update(t) : { ...t, ...update } : t) }))
  function assign(id: string, personId: string) {
    if (personId === '__add') { setNewFor(id); setName(''); setEmail(''); setError(''); return }
    const person = persons.find(p => p.id === personId)
    patch(id, { ownerId: person?.id || '', owner: person?.name || '' }); setNewFor(null)
  }
  function add(id: string) {
    if (!name.trim()) { setError(t('tasks.errors.nameRequired')); return }
    if (email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setError(t('tasks.errors.checkEmail')); return }
    const match = persons.find(p => email.trim() && p.email.toLowerCase() === email.trim().toLowerCase())
    const person: Person = match || { id: crypto.randomUUID(), name: name.trim(), email: email.trim(), source: 'manual' }
    change(d => ({ ...d, people: match ? d.people : [...d.people, person], tasks: d.tasks.map(t => t.id === id ? { ...t, ownerId: person.id, owner: person.name } : t) }))
    setNewFor(null)
  }
  useEffect(() => { if (newFor) input.current?.focus() }, [newFor])
  return <>
    {peopleNote && expanded && <p className="quiet">{peopleNote}</p>}
    {(expanded ? draft.tasks : draft.tasks.slice(0, 3)).map((task, index) => {
      const selected = persons.find(p => p.id === task.ownerId) || persons.filter(p => p.name.toLowerCase() === task.owner.toLowerCase()).filter((_, __, all) => all.length === 1)[0]
      const value = selected?.id || (task.owner ? '__retained' : '')
      return <section className={"notes-task" + (task.included === false ? " task-excluded" : "")} key={task.id} aria-label={t('tasks.task.label', { number: index + 1 })}>
        <div className="task-selection"><label className="checkbox"><input type="checkbox" checked={task.included !== false} disabled={!editable} onChange={e => patch(task.id, { included: e.target.checked })} />{t('tasks.task.include')}</label>{task.included === false && <span>{t('tasks.task.deselected')}</span>}</div>
        {task.suggested === false && <p className="quiet">{t('tasks.task.noLongerSuggested')}</p>}
        <InlineText title value={task.title} label={t('tasks.task.edit', { number: index + 1 })} editable={editable} change={title => patch(task.id, { title })} />
        <label className="person-field">{t('tasks.owner.label')}
          <select aria-label={t('tasks.owner.ariaLabel', { number: index + 1 })} disabled={!editable} value={value} className={!task.owner && editable ? 'person-missing' : ''} onChange={e => assign(task.id, e.target.value)}>
            <option value="">{t('tasks.owner.selectPerson')}</option>
            {(['self', 'calendar', 'call', 'contact', 'manual'] as const).map(source => persons.some(p => p.source === source) && <optgroup key={source} label={t(`tasks.source.${source}`)}>
              {persons.filter(p => p.source === source).map(p => <option key={p.id} value={p.id}>{p.name}{p.source === 'self' ? t('tasks.owner.me') : ''}{persons.filter(other => other.name === p.name).length > 1 ? ' · ' + p.email : ''}</option>)}
            </optgroup>)}
            {value === '__retained' && <option value="__retained">{task.owner}</option>}
            <option value="__add">{t('tasks.owner.addAnother')}</option>
          </select>
        </label>
        {newFor === task.id && <div className="new-person"><label>{t('tasks.newPerson.name')}<input ref={input} aria-label={t('tasks.newPerson.nameAria')} value={name} maxLength={200} onChange={e => setName(e.target.value)} /></label>
          <label>{t('tasks.newPerson.email')}<input aria-label={t('tasks.newPerson.emailAria')} type="email" value={email} maxLength={300} onChange={e => setEmail(e.target.value)} onKeyDown={e => { if(e.key === 'Enter') add(task.id) }} /></label>
          {error && <p role="alert">{error}</p>}<div className="notes-more"><button className="primary" onClick={() => add(task.id)}>{t('tasks.newPerson.add')}</button><button onClick={() => setNewFor(null)}>{t('tasks.newPerson.cancel')}</button></div></div>}
        {task.questions.filter(q => q.field !== 'owner').map(q => <div key={q.id} className={'task-clarification' + (questionOpen(q, task) ? ' unanswered' : '')}>
          <p>{q.label}</p>
          {q.options.length ? <div className="notes-more" role="group" aria-label={q.label}>{q.options.map(option => <button key={option} disabled={!editable} aria-pressed={q.answer === option}
            onClick={() => patch(task.id, t => ({ ...t, questions: t.questions.map(item => item.id === q.id ? { ...item, answer: option } : item) }))}>{q.answer === option ? '✓ ' : ''}{option}</button>)}</div>
            : <InlineText value={q.answer} label={t('tasks.task.answerPrefix') + q.label} editable={editable} change={answer => patch(task.id, t => ({ ...t, questions: t.questions.map(item => item.id === q.id ? { ...item, answer } : item) }))} />}
        </div>)}
        {expanded && <details className="task-details"><summary>{t('tasks.task.moreDetails')}</summary><label>{t('tasks.task.recipientField')}<input aria-label={t('tasks.task.recipient')} disabled={!editable} value={task.recipient} maxLength={300} onChange={e => patch(task.id, { recipient: e.target.value })} /></label>
          <label>{t('tasks.task.dueField')}<input aria-label={t('tasks.task.due')} disabled={!editable} value={task.due} maxLength={100} onChange={e => patch(task.id, { due: e.target.value })} /></label>
</details>}
      </section>
    })}
  </>
}
