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
      if (!response.ok) throw new Error(t('calendar.loadFailed'))
      const data = await response.json()
      if (data.ok === false) throw new Error(data.error)
      refresh()
    } catch (e) { setError(e instanceof Error ? e.message : t('calendar.loadFailed')) }
    finally { setBusy(false) }
  }
  if (locked && !context) return null
  return <section className="notes-calendar" aria-label={t('calendar.meeting')}>
    <strong>{t('calendar.meeting')}</strong>
    {context ? <>
      {context.start && <p>{new Date(context.start).toLocaleDateString(uiLocale())} · {time(context.start)}{context.end ? ' – ' + time(context.end) : ''}</p>}
      {context.organizer && <p>{t('calendar.organizedBy')} {context.organizer}</p>}
      <p><strong>{t('calendar.invitedParticipants', { count: context.people.length })}</strong></p>
      <div className="invitation-people">{context.people.map((p, i) => <span key={p.id} title={p.email}>{i > 0 ? ' · ' : ''}{p.name}</span>)}</div>
    </> : !selected && <>
      <p>{accessNeeded ? t('calendar.connectHint') : candidates.length ? t('calendar.candidatesMatch', { count: candidates.length }) : note || t('calendar.searching')}</p>
      {candidates.length > 0 && <label>{t('calendar.whichMeeting')}
        <select aria-label={t('calendar.whichMeeting')} disabled={busy || locked} value="" onChange={e => { if (e.target.value) void action('calendar-select', { id: e.target.value }) }}>
          <option value="">{t('calendar.selectMeeting')}</option>
          {candidates.map(c => <option key={c.id} value={c.id}>{c.title} · {time(c.start)}{c.end ? ' – ' + time(c.end) : ''}{c.response === 'accepted' ? t('calendar.accepted') : c.response === 'tentativelyAccepted' ? t('calendar.tentative') : ''}</option>)}
        </select>
      </label>}
      <button disabled={busy || locked} onClick={() => void action(accessNeeded ? 'calendar-connect' : 'calendar-refresh')}>{busy ? t('calendar.loading') : accessNeeded ? t('calendar.connect') : t('calendar.reload')}</button>
    </>}
    {error && <p role="alert" className="notes-error">{error}</p>}
  </section>
}
