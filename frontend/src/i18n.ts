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
let current: AppLanguage = 'de'

export const appLanguage = () => current
export function setAppLanguage(value: unknown) {
  current = value === 'en' ? 'en' : 'de'
  document.documentElement.lang = current
}

function lookup(key: string): string | undefined {
  let node: string | Messages | undefined = catalogs[current]
  for (const part of key.split('.')) node = typeof node === 'object' ? node[part] : undefined
  return typeof node === 'string' ? node : undefined
}

export function t(key: string, params?: Params) {
  const plural = params && typeof params.count === 'number' ? lookup(`${key}_${params.count === 1 ? 'one' : 'other'}`) : undefined
  const text = plural ?? lookup(key) ?? key
  return params ? text.replace(/\{\{(\w+)\}\}/g, (match, name) => name in params ? String(params[name]) : match) : text
}

// Locale for dates and times shown in the UI.
export const uiLocale = () => current === 'en' ? 'en-GB' : 'de-DE'
