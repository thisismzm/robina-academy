/**
 * Text direction: the single source of truth.
 *
 * Deliberately dependency-free: `app/global-error.tsx` renders outside every
 * provider (no i18n, no router) and still needs to import from here.
 *
 * The RTL language set is duplicated in `public/dir-init.js`, which runs before
 * any bundle loads and therefore cannot import this module. `tests/rtl-guard`
 * asserts the two stay in sync.
 */

export type Direction = 'ltr' | 'rtl'

/**
 * Superset of the locales we translate to. A browser can report `he` or `ur`
 * even though we don't ship those bundles yet; direction should still be
 * right, with the UI falling back to English text.
 */
export const RTL_LANGUAGES = new Set([
  'ar', // Arabic
  'fa', // Persian
  'he', // Hebrew
  'iw', // Hebrew (legacy code, still emitted by some browsers)
  'ur', // Urdu
  'ps', // Pashto
  'sd', // Sindhi
  'ug', // Uyghur
  'yi', // Yiddish
  'dv', // Dhivehi
  'ckb', // Central Kurdish
])

/** `en-GB` -> `en`. Mirrors the `.split('-')[0]` used throughout lib/i18n.ts. */
export function baseCode(lng?: string | null): string {
  return String(lng || 'fa').split('-')[0].toLowerCase()
}

export function directionForLanguage(lng?: string | null): Direction {
  return RTL_LANGUAGES.has(baseCode(lng)) ? 'rtl' : 'ltr'
}

/** +1 in LTR, -1 in RTL. Multiply any physical x-offset by this. */
export function dirMultiplier(dir: Direction): 1 | -1 {
  return dir === 'rtl' ? -1 : 1
}

/**
 * Detect the language the same way i18next's LanguageDetector does, in the same
 * order (see `detection.order` in lib/i18n.ts): localStorage → cookie →
 * querystring. With no explicit choice it resolves to Persian, the default
 * language. Returns 'en' on the server only, to match the static `lang="en"`
 * SSR baseline and keep hydration safe.
 *
 * Kept byte-for-byte equivalent to the detector in public/dir-init.js.
 */
export function detectClientLanguage(): string {
  if (typeof window === 'undefined') return 'en'

  try {
    const stored = window.localStorage.getItem('i18nextLng')
    if (stored) return stored
  } catch {
    /* localStorage can throw in private mode / sandboxed iframes */
  }

  const cookie = document.cookie.match(/(?:^|;\s*)i18next=([^;]*)/)
  if (cookie) {
    try {
      return decodeURIComponent(cookie[1])
    } catch {
      /* malformed cookie value */
    }
  }

  try {
    const qs = new URLSearchParams(window.location.search).get('lng')
    if (qs) return qs
  } catch {
    /* malformed query string */
  }

  // Persian is the default: with no explicit choice (localStorage/cookie/
  // querystring) we land on 'fa' rather than following the browser locale.
  return 'fa'
}

/**
 * Write `lang`/`dir` onto <html>. Idempotent.
 *
 * `--dir` is exposed as a CSS custom property so stylesheets can consume a
 * direction multiplier without reaching for JS.
 */
export function applyDocumentDirection(lng: string): Direction {
  const code = baseCode(lng)
  const dir = directionForLanguage(code)

  if (typeof document === 'undefined') return dir

  const el = document.documentElement
  el.setAttribute('lang', code)
  el.setAttribute('dir', dir)
  el.style.setProperty('--dir', dir === 'rtl' ? '-1' : '1')

  return dir
}
