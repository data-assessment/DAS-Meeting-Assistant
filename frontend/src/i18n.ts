// App language for the Meeting Notes UI. Separate from the meeting (speech) language.
// Texts live in locales/<language>.json under nested English keys (i18next conventions:
// {{name}} placeholders, key_one/key_other plurals chosen by params.count).
// The backend uses the same format in engine/locales.
import de from './locales/de.json'
import en from './locales/en.json'

export type AppLanguage = 'de' | 'en'
type Messages = { [key: string]: string | Messages }
type Params = Record<string, string | number>

const catalogs: Record<AppLanguage, Messages> = { de, en }
const normalize = (value: unknown): AppLanguage => value === 'en' ? 'en' : 'de'
// The backend opens the window with ?lang=, so the first render before any state poll is right.
let current: AppLanguage = normalize(new URLSearchParams(window.location.search).get('lang'))
// A choice the backend has not confirmed yet; a state poll started before it must not undo it.
let pending: AppLanguage | null = null

export const appLanguage = () => current
function apply(value: AppLanguage) {
  current = value
  document.documentElement.lang = current
}
apply(current)
// Called with the saved language from each state poll; undefined until the first poll.
export function setAppLanguage(saved: unknown) {
  if (saved === undefined && !pending) return
  if (pending && normalize(saved) === pending) pending = null
  apply(pending ?? normalize(saved))
}
// A choice re-renders the whole window at once, not only the switch; polls re-render anyway.
const listeners = new Set<() => void>()
export function subscribeAppLanguage(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
const notify = () => listeners.forEach(listener => listener())
export function chooseAppLanguage(value: AppLanguage) { pending = value; apply(value); notify() }
export function cancelAppLanguage(previous: AppLanguage) { pending = null; apply(previous); notify() }

function lookup(key: string, language: AppLanguage): string | undefined {
  let node: string | Messages | undefined = catalogs[language]
  for (const part of key.split('.')) node = typeof node === 'object' ? node[part] : undefined
  return typeof node === 'string' ? node : undefined
}

// Text in a given language, e.g. a meeting's notes language instead of the app language.
export function tIn(language: unknown, key: string, params?: Params) {
  const lang = normalize(language)
  const plural = params && typeof params.count === 'number' ? lookup(`${key}_${params.count === 1 ? 'one' : 'other'}`, lang) : undefined
  const text = plural ?? lookup(key, lang) ?? key
  return params ? text.replace(/\{\{(\w+)\}\}/g, (match, name) => Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match) : text
}

export const t = (key: string, params?: Params) => tIn(current, key, params)

// Locale for dates and times shown in the UI.
export const uiLocale = () => current === 'en' ? 'en-GB' : 'de-DE'
