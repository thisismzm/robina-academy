/**
 * Locale-aware date and number formatting in one place, so a locale change
 * reaches every date on screen.
 *
 * Before this existed, all 12 dayjs call sites extended `relativeTime` locally
 * and none imported a `dayjs/locale/*`, so "2 hours ago" rendered in English
 * in all 22 languages.
 */

import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import i18n from 'i18next'
import { baseCode } from './direction'
import { toPersianDigits } from './jalali'

dayjs.extend(relativeTime)

/** Mirrors LOCALE_LOADERS in lib/i18n.ts. English is dayjs's built-in default. */
const DAYJS_LOCALES: Record<string, () => Promise<unknown>> = {
  ar: () => import('dayjs/locale/ar'),
  bn: () => import('dayjs/locale/bn'),
  de: () => import('dayjs/locale/de'),
  es: () => import('dayjs/locale/es'),
  fa: () => import('dayjs/locale/fa'),
  fr: () => import('dayjs/locale/fr'),
  hi: () => import('dayjs/locale/hi'),
  id: () => import('dayjs/locale/id'),
  it: () => import('dayjs/locale/it'),
  ja: () => import('dayjs/locale/ja'),
  ko: () => import('dayjs/locale/ko'),
  nl: () => import('dayjs/locale/nl'),
  pl: () => import('dayjs/locale/pl'),
  pt: () => import('dayjs/locale/pt'),
  ru: () => import('dayjs/locale/ru'),
  sk: () => import('dayjs/locale/sk'),
  th: () => import('dayjs/locale/th'),
  tr: () => import('dayjs/locale/tr'),
  uk: () => import('dayjs/locale/uk'),
  vi: () => import('dayjs/locale/vi'),
  zh: () => import('dayjs/locale/zh'),
}

const INTL_LOCALE_CACHE_MAX = 64
const intlLocaleCache = new Map<string, string>()

/**
 * NUMERALS: a deliberate product decision, kept behind one function.
 *
 * `Intl.NumberFormat('ar')` resolves to Eastern Arabic-Indic digits (٠١٢٣٤) on
 * most ICU builds, and `ar-SA` additionally defaults to the Hijri calendar.
 * Neither is right here: prices, seat counts and analytics sit next to
 * Latin-digit data coming straight from the API, and mixed numeral systems on
 * one screen read as broken rather than localised. Arabic-speaking regions are
 * also split (ar-EG and ar-SA use Arabic-Indic, ar-MA and ar-TN use Western),
 * so a bare `ar` has no single correct answer.
 *
 * So: Latin digits and the Gregorian calendar, everywhere. Month names and
 * relative times still localise. Flip this one function if an org asks for
 * native numerals.
 */
export function intlLocale(lng?: string): string {
  const key = lng ?? ''
  const cached = intlLocaleCache.get(key)
  if (cached) return cached
  const tag = canonicalTag(lng)
  const base = baseCode(tag)
  const locale = base === 'ar' || base === 'fa' ? `${base}-u-nu-latn` : tag
  // The input can come from a request cookie, so on the server it isn't a
  // small fixed set; keep the cache bounded.
  if (intlLocaleCache.size >= INTL_LOCALE_CACHE_MAX) intlLocaleCache.clear()
  intlLocaleCache.set(key, locale)
  return locale
}

/**
 * `Intl.*` throws a RangeError ("Incorrect locale information provided") on a
 * malformed tag, which took down whole pages. The language can come from a
 * cookie, localStorage or the browser, so expect `en_US`, empty strings and
 * outright junk; fall back to the base language, then English.
 */
function canonicalTag(lng?: string): string {
  const raw = String(lng || '').trim().replace(/_/g, '-')
  for (const candidate of [raw, raw.split('-')[0]]) {
    if (!candidate) continue
    try {
      const [tag] = Intl.getCanonicalLocales(candidate)
      if (tag) return tag
    } catch {
      // Malformed; try the next, shorter candidate.
    }
  }
  return 'en'
}

/**
 * Load and activate the dayjs locale. Called from lib/i18n.ts on startup and on
 * every language change, so `dayjs.locale` is set before anything renders.
 */
export async function loadDateLocale(lng?: string): Promise<void> {
  const code = baseCode(lng)
  if (code === 'en') {
    dayjs.locale('en')
    return
  }
  const loader = DAYJS_LOCALES[code]
  if (!loader) return
  try {
    await loader()
    dayjs.locale(code)
  } catch (e) {
    console.warn(`Failed to load date locale: ${lng}`, e)
  }
}

/** The language to format for: the caller's, else the active i18next language. */
function activeLanguage(lng?: string): string | undefined {
  return lng || i18n.language || undefined
}

function isPersian(lng?: string): boolean {
  return baseCode(activeLanguage(lng)) === 'fa'
}

/**
 * DATES: Persian (fa) is the one exception to the Gregorian + Latin digits rule
 * above. It gets the Solar Hijri (Jalali) calendar and Persian digits, so
 * "۱۴۰۴ فروردین ۱". Every other language keeps Gregorian + Latin digits.
 * Numbers and currency are unaffected; this only applies to dates and times.
 */
function dateLocale(lng?: string): string {
  return isPersian(lng) ? 'fa-u-ca-persian-nu-arabext' : intlLocale(activeLanguage(lng))
}

const FIELD_OPTIONS = [
  'weekday', 'era', 'year', 'month', 'day', 'dayPeriod',
  'hour', 'minute', 'second', 'fractionalSecondDigits', 'timeZoneName',
] as const

function hasFieldOptions(options?: Intl.DateTimeFormatOptions): boolean {
  return !!options && FIELD_OPTIONS.some((k) => options[k] !== undefined)
}

/** Anything `new Date()` accepts, plus dayjs objects. */
export type DateLike = string | number | Date | { toDate(): Date }

function toDate(value: DateLike): Date {
  if (value instanceof Date) return value
  if (typeof value === 'object') return value.toDate()
  return new Date(value)
}

function formatWith(
  value: DateLike,
  lng: string | undefined,
  defaults: Intl.DateTimeFormatOptions,
  options?: Intl.DateTimeFormatOptions
): string {
  const date = toDate(value)
  if (Number.isNaN(date.getTime())) return ''
  const persian = isPersian(lng)
  // `dateStyle`/`timeStyle` cannot be combined with individual fields.
  const base = hasFieldOptions(options) ? {} : defaults
  return new Intl.DateTimeFormat(dateLocale(lng), {
    ...(persian ? {} : { calendar: 'gregory' }),
    ...base,
    ...options,
  }).format(date)
}

/**
 * Absolute date. Goes through Intl rather than dayjs on purpose: dayjs's `ar`
 * locale emits Arabic-Indic digits in its date formats, which is exactly what
 * intlLocale() is there to avoid. `lng` defaults to the active UI language.
 */
export function formatDate(
  value: DateLike,
  lng?: string,
  options?: Intl.DateTimeFormatOptions
): string {
  return formatWith(value, lng, { dateStyle: 'medium' }, options)
}

export function formatDateTime(
  value: DateLike,
  lng?: string,
  options?: Intl.DateTimeFormatOptions
): string {
  return formatWith(value, lng, { dateStyle: 'medium', timeStyle: 'short' }, options)
}

/** Time of day only, 24-hour, e.g. "14:05:09" or "۱۴:۰۵:۰۹". */
export function formatTime(
  value: DateLike,
  lng?: string,
  options?: Intl.DateTimeFormatOptions
): string {
  return formatWith(
    value,
    lng,
    {},
    { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', ...options }
  )
}

/** Year as displayed in the active calendar, e.g. 2025 or ۱۴۰۴. */
export function formatYear(value: DateLike, lng?: string): string {
  return formatWith(value, lng, {}, { year: 'numeric' })
}

/** "2 hours ago". dayjs supplies the words; Persian digits are applied for fa. */
export function formatRelative(value: DateLike, lng?: string): string {
  const text = dayjs(toDate(value)).locale(baseCode(activeLanguage(lng))).fromNow()
  return isPersian(lng) ? toPersianDigits(text) : text
}

export function formatNumber(
  value: number,
  lng?: string,
  options?: Intl.NumberFormatOptions
): string {
  return new Intl.NumberFormat(intlLocale(lng), options).format(value)
}

export function formatCurrency(
  value: number,
  currency: string,
  lng?: string,
  options?: Intl.NumberFormatOptions
): string {
  return new Intl.NumberFormat(intlLocale(lng), {
    style: 'currency',
    currency,
    ...options,
  }).format(value)
}

export function formatPercent(
  value: number,
  lng?: string,
  options?: Intl.NumberFormatOptions
): string {
  return new Intl.NumberFormat(intlLocale(lng), {
    style: 'percent',
    maximumFractionDigits: 0,
    ...options,
  }).format(value)
}
