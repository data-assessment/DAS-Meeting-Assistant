import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { MeetingNotes } from './MeetingNotes'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {new URLSearchParams(window.location.search).get('view') === 'notes' ? <MeetingNotes /> : <App />}
  </StrictMode>,
)
