import { useEffect, useRef, useState } from 'react'

export interface JobInfo {
  id: string
  title: string
  detail: string
  startedAt: string | null
  durationSeconds: number | null
}

export interface MeetingCandidate {
  id: string
  subject: string
  start: string | null
  end: string | null
  organizer: string | null
  inviteeCount: number
}

export interface MeetingState {
  notesEnabled: boolean
  notesError: string
  notesCount: number
  active: boolean
  title: string
  startedAt: string | null
  phase: 'idle' | 'meeting'
  jobs: JobInfo[]
  backgroundTranscriptionStatus: string
  liveTranscript: string
  liveOn: boolean
  autoStart: boolean
  meetingDetected: boolean
  meetingCandidates: MeetingCandidate[]
  selectedMeetingCandidateId: string | null
  meetingCandidateStatus: 'idle' | 'loading' | 'matched' | 'none' | 'error' | 'rejected'
  meetingCandidateNote: string | null
  language: string
  health: 'ok' | 'offline' | 'auth' | 'error'
  version: string
  sttEngine: string
  realtimeEngine: string
  liveTranslated: boolean
  usageEnabled: boolean
  transcriptsCanOpen: boolean
  recordingsCanRetry: boolean
  lastTranscriptReady: boolean
  oneNoteCanSave: boolean
  oneNoteCanOpen: boolean
  manual: boolean
  stopSuggestion: {
    id: string
    source: string
    reason: string
    detail: string
    observedAt: string
  } | null
}

const WS_URL = import.meta.env.VITE_WS_URL ?? `ws://${location.host}/ws`

/** Subscribe to the engine's meeting state over the WebSocket, reconnecting on drop. */
export function useMeetingState(): MeetingState {
  const [state, setState] = useState<MeetingState>({
    notesEnabled: false,
    notesError: '',
    notesCount: 0,
    active: false,
    title: '',
    startedAt: null,
    phase: 'idle',
    jobs: [],
    backgroundTranscriptionStatus: '',
    liveTranscript: '',
    liveOn: false,
    autoStart: true,
    meetingDetected: false,
    meetingCandidates: [],
    selectedMeetingCandidateId: null,
    meetingCandidateStatus: 'idle',
    meetingCandidateNote: null,
    language: '',
    health: 'ok',
    version: '',
    sttEngine: '',
    realtimeEngine: '',
    liveTranslated: false,
    usageEnabled: false,
    transcriptsCanOpen: false,
    recordingsCanRetry: false,
    lastTranscriptReady: false,
    oneNoteCanSave: false,
    oneNoteCanOpen: false,
    manual: false,
    stopSuggestion: null,
  })
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let stopped = false
    let reconnectTimer: number | undefined

    function connect() {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data)
        if (data.type === 'state') {
          setState((prev) => ({
            ...prev,
            notesEnabled: data.notesEnabled ?? false,
            notesError: data.notesError ?? '',
            notesCount: data.notesCount ?? 0,
            active: Boolean(data.active),
            title: data.title ?? '',
            startedAt: data.startedAt ?? null,
            phase: data.phase ?? (data.active ? 'meeting' : 'idle'),
            jobs: Array.isArray(data.jobs) ? data.jobs : [],
            backgroundTranscriptionStatus: data.backgroundTranscriptionStatus ?? '',
            liveOn: data.liveOn ?? prev.liveOn,
            autoStart: data.autoStart ?? prev.autoStart,
            meetingDetected: data.meetingDetected ?? false,
            meetingCandidates: Array.isArray(data.meetingCandidates) ? data.meetingCandidates : [],
            selectedMeetingCandidateId: data.selectedMeetingCandidateId ?? null,
            meetingCandidateStatus: data.meetingCandidateStatus ?? 'idle',
            meetingCandidateNote: data.meetingCandidateNote ?? null,
            language: data.language ?? prev.language,
            health: data.health ?? prev.health,
            version: data.version ?? prev.version,
            sttEngine: data.sttEngine ?? prev.sttEngine,
            realtimeEngine: data.realtimeEngine ?? prev.realtimeEngine,
            liveTranslated: data.liveTranslated ?? false,
            usageEnabled: data.usageEnabled ?? prev.usageEnabled,
            transcriptsCanOpen: data.transcriptsCanOpen ?? false,
            recordingsCanRetry: data.recordingsCanRetry ?? false,
            lastTranscriptReady: data.lastTranscriptReady ?? false,
            oneNoteCanSave: data.oneNoteCanSave ?? false,
            oneNoteCanOpen: data.oneNoteCanOpen ?? false,
            manual: data.manual ?? prev.manual,
            stopSuggestion: data.stopSuggestion ?? null,
            // keep the live transcript after the meeting (so it can be copied/saved);
            // only clear it when a NEW meeting actually starts.
            liveTranscript: data.active ? '' : prev.liveTranscript,
          }))
        } else if (data.type === 'transcript') {
          setState((prev) => ({ ...prev, liveTranscript: data.text ?? '' }))
        }
      }
      ws.onclose = () => {
        if (!stopped) reconnectTimer = window.setTimeout(connect, 1000)
      }
    }

    connect()
    return () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      wsRef.current?.close()
    }
  }, [])

  return state
}

/** Seconds elapsed since startedAt, ticking locally (no per-second messages). */
export function useElapsedSeconds(startedAt: string | null, active: boolean): number {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!active || !startedAt) {
      setElapsed(0)
      return
    }
    const start = new Date(startedAt).getTime()
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - start) / 1000)))
    tick()
    const id = window.setInterval(tick, 250)
    return () => clearInterval(id)
  }, [startedAt, active])

  return elapsed
}
