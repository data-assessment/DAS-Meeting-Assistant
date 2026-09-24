import { useState } from 'react'
import type { Person } from './NotesTasks'

export type CalendarContext = { title: string; start: string; end: string; organizer: string; people: Person[] }
export type CalendarCandidate = { id: string; title: string; start: string; end?: string; response?: string }
const time = (value: string) => new Date(value).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })

export function NotesCalendar({ id, selected, context, candidates = [], accessNeeded, note, locked, refresh }: {
  id: string; selected?: boolean; context?: CalendarContext | null; candidates?: CalendarCandidate[];
  accessNeeded?: boolean; note?: string; locked: boolean; refresh: () => void
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function action(name: string, body = {}) {
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/notes/${id}/${name}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('Outlook-Termine konnten nicht geladen werden.')
      const data = await response.json()
      if (data.ok === false) throw new Error(data.error)
      refresh()
    } catch (e) { setError(e instanceof Error ? e.message : 'Outlook-Termine konnten nicht geladen werden.') }
    finally { setBusy(false) }
  }
  if (locked && !context) return null
  return <section className="notes-calendar" aria-label="Outlook-Termin">
    <strong>Outlook-Termin</strong>
    {context ? <>
      {context.start && <p>{new Date(context.start).toLocaleDateString('de-DE')} · {time(context.start)}{context.end ? ' – ' + time(context.end) : ''}</p>}
      {context.organizer && <p>Organisiert von: {context.organizer}</p>}
      <p><strong>Teilnehmer laut Einladung ({context.people.length})</strong></p>
      <div className="invitation-people">{context.people.map((p, i) => <span key={p.id} title={p.email}>{i > 0 ? ' · ' : ''}{p.name}</span>)}</div>
    </> : !selected && <>
      <p>{accessNeeded ? 'Kalender verbinden, um Einladungstitel und Teilnehmer zu übernehmen.' : candidates.length ? `${candidates.length} Outlook-Termine passen zu dieser Aufzeichnung. Bitte auswählen.` : note || 'Der passende Outlook-Termin wird gesucht.'}</p>
      {candidates.length > 0 && <label>Welcher Kalendertermin gehört zu diesem Gespräch?
        <select aria-label="Welcher Kalendertermin gehört zu diesem Gespräch?" disabled={busy || locked} value="" onChange={e => { if (e.target.value) void action('calendar-select', { id: e.target.value }) }}>
          <option value="">Termin auswählen</option>
          {candidates.map(c => <option key={c.id} value={c.id}>{c.title} · {time(c.start)}{c.end ? ' – ' + time(c.end) : ''}{c.response === 'accepted' ? ' · Zugesagt' : c.response === 'tentativelyAccepted' ? ' · Mit Vorbehalt' : ''}</option>)}
        </select>
      </label>}
      <button disabled={busy || locked} onClick={() => void action(accessNeeded ? 'calendar-connect' : 'calendar-refresh')}>{busy ? 'Outlook wird geladen …' : accessNeeded ? 'Kalender verbinden' : 'Outlook-Termine neu laden'}</button>
    </>}
    {error && <p role="alert" className="notes-error">{error}</p>}
  </section>
}
