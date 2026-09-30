import { useState } from 'react'
import type { Person } from './NotesTasks'
import { t, uiLocale } from './i18n'

export type CalendarContext = { title: string; start: string; end: string; organizer: string; people: Person[] }
export type CalendarCandidate = { id: string; title: string; start: string; end?: string; response?: string }
const time = (value: string) => new Date(value).toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit' })

export function NotesCalendar({ id, selected, context, candidates = [], accessNeeded, note, locked, refresh }: {
  id: string; selected?: boolean; context?: CalendarContext | null; candidates?: CalendarCandidate[];
  accessNeeded?: boolean; note?: string; locked: boolean; refresh: () => void
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function action(name: string, body = {}) {
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/notes/${id}/${name}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(t('Outlook-Termine konnten nicht geladen werden.', 'Could not load Outlook meetings.'))
      const data = await response.json()
      if (data.ok === false) throw new Error(data.error)
      refresh()
    } catch (e) { setError(e instanceof Error ? e.message : t('Outlook-Termine konnten nicht geladen werden.', 'Could not load Outlook meetings.')) }
    finally { setBusy(false) }
  }
  if (locked && !context) return null
  return <section className="notes-calendar" aria-label={t('Outlook-Termin', 'Outlook meeting')}>
    <strong>{t('Outlook-Termin', 'Outlook meeting')}</strong>
    {context ? <>
      {context.start && <p>{new Date(context.start).toLocaleDateString(uiLocale())} · {time(context.start)}{context.end ? ' – ' + time(context.end) : ''}</p>}
      {context.organizer && <p>{t('Organisiert von:', 'Organised by:')} {context.organizer}</p>}
      <p><strong>{t(`Teilnehmer laut Einladung (${context.people.length})`, `Invited participants (${context.people.length})`)}</strong></p>
      <div className="invitation-people">{context.people.map((p, i) => <span key={p.id} title={p.email}>{i > 0 ? ' · ' : ''}{p.name}</span>)}</div>
    </> : !selected && <>
      <p>{accessNeeded ? t('Kalender verbinden, um Einladungstitel und Teilnehmer zu übernehmen.', 'Connect your calendar to use the invitation title and participants.') : candidates.length ? t(`${candidates.length} Outlook-Termine passen zu dieser Aufzeichnung. Bitte auswählen.`, candidates.length === 1 ? '1 Outlook meeting matches this recording. Please select it.' : `${candidates.length} Outlook meetings match this recording. Please select one.`) : note || t('Der passende Outlook-Termin wird gesucht.', 'Looking for the matching Outlook meeting.')}</p>
      {candidates.length > 0 && <label>{t('Welcher Kalendertermin gehört zu diesem Gespräch?', 'Which calendar meeting belongs to this conversation?')}
        <select aria-label={t('Welcher Kalendertermin gehört zu diesem Gespräch?', 'Which calendar meeting belongs to this conversation?')} disabled={busy || locked} value="" onChange={e => { if (e.target.value) void action('calendar-select', { id: e.target.value }) }}>
          <option value="">{t('Termin auswählen', 'Select meeting')}</option>
          {candidates.map(c => <option key={c.id} value={c.id}>{c.title} · {time(c.start)}{c.end ? ' – ' + time(c.end) : ''}{c.response === 'accepted' ? t(' · Zugesagt', ' · Accepted') : c.response === 'tentativelyAccepted' ? t(' · Mit Vorbehalt', ' · Tentative') : ''}</option>)}
        </select>
      </label>}
      <button disabled={busy || locked} onClick={() => void action(accessNeeded ? 'calendar-connect' : 'calendar-refresh')}>{busy ? t('Outlook wird geladen …', 'Loading Outlook …') : accessNeeded ? t('Kalender verbinden', 'Connect calendar') : t('Outlook-Termine neu laden', 'Reload Outlook meetings')}</button>
    </>}
    {error && <p role="alert" className="notes-error">{error}</p>}
  </section>
}
