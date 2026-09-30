// App language for the Meeting Notes UI. Separate from the meeting (speech) language.
// Texts stay inline as t('Deutsch', 'English') pairs, so each screen keeps its wording
// next to its markup; the backend uses the same pattern (engine/notes_i18n.py).
export type AppLanguage = 'de' | 'en'

let current: AppLanguage = 'de'

export const appLanguage = () => current
export function setAppLanguage(value: unknown) {
  current = value === 'en' ? 'en' : 'de'
  document.documentElement.lang = current
}
export const t = (de: string, en: string) => current === 'en' ? en : de
// Locale for dates and times shown in the UI.
export const uiLocale = () => current === 'en' ? 'en-GB' : 'de-DE'
