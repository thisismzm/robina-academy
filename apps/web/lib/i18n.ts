'use client'

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import en from '../locales/en.json';
import { loadDateLocale } from './format';

const LOCALE_LOADERS: Record<string, () => Promise<{ default: any }>> = {
  fr: () => import('../locales/fr.json'),
  de: () => import('../locales/de.json'),
  es: () => import('../locales/es.json'),
  ar: () => import('../locales/ar.json'),
  ja: () => import('../locales/ja.json'),
  pt: () => import('../locales/pt.json'),
  ru: () => import('../locales/ru.json'),
  zh: () => import('../locales/zh.json'),
  hi: () => import('../locales/hi.json'),
  ko: () => import('../locales/ko.json'),
  it: () => import('../locales/it.json'),
  tr: () => import('../locales/tr.json'),
  vi: () => import('../locales/vi.json'),
  id: () => import('../locales/id.json'),
  pl: () => import('../locales/pl.json'),
  uk: () => import('../locales/uk.json'),
  nl: () => import('../locales/nl.json'),
  th: () => import('../locales/th.json'),
  bn: () => import('../locales/bn.json'),
  fa: () => import('../locales/fa.json'),
  sk: () => import('../locales/sk.json'),
};

// Only bundle English; lazy-load all other locales on demand
const resources = {
  en: { common: en },
};

async function loadLocale(lng: string) {
  const code = lng.split('-')[0]
  if (code === 'en' || !LOCALE_LOADERS[code]) return;
  if (i18n.hasResourceBundle(code, 'common')) return;

  try {
    const mod = await LOCALE_LOADERS[code]();
    i18n.addResourceBundle(code, 'common', mod.default, true, true);
  } catch (e) {
    console.warn(`Failed to load locale: ${lng}`, e);
  }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    // The FIRST client render must reproduce the server's HTML exactly, or
    // React throws a hydration mismatch (the server has no access to
    // localStorage/cookie/navigator, so it always renders English). We
    // therefore pin the initial language to 'en' and let LanguageDetector run
    // only AFTER mount, via applyDetectedLanguage() below (called from
    // I18nProvider's effect). The detector plugin stays registered so its
    // caches (localStorage + cookie) keep working for changeLanguage().
    lng: 'en',
    fallbackLng: 'en',
    ns: ['common'],
    defaultNS: 'common',
    interpolation: {
      escapeValue: false, // react already safes from xss
    },
    detection: {
      order: ['localStorage', 'cookie', 'querystring', 'navigator', 'path', 'subdomain'],
      caches: ['localStorage', 'cookie'],
      lookupLocalStorage: 'i18nextLng',
      lookupCookie: 'i18next',
    },
    react: {
      useSuspense: false,
    }
  });

/**
 * Switch language safely: preloads the bundle before switching
 * so the UI never flashes English as a fallback.
 */
export async function changeLanguage(lng: string) {
  await Promise.all([loadLocale(lng), loadDateLocale(lng)])
  return i18n.changeLanguage(lng)
}

/**
 * Resolve the user's real language and apply it. MUST be called only after the
 * React tree has mounted (i.e. from an effect), never during render: i18next is
 * initialised with lng:'en' so the first client render matches the server, and
 * this is what then moves the UI to the detected locale.
 *
 * Runs the exact same detection LanguageDetector would have (localStorage →
 * cookie → querystring → navigator), preloads the bundle + dayjs locale, then
 * switches. No-op on the server and when the detected language is already
 * English, so hydration is never disturbed.
 */
export async function applyDetectedLanguage(): Promise<void> {
  if (typeof window === 'undefined') return
  const detected = (i18n.services.languageDetector?.detect() as
    | string
    | string[]
    | undefined)
  const lng = Array.isArray(detected) ? detected[0] : detected
  const code = (lng || 'en').split('-')[0]
  if (!code || code === i18n.language.split('-')[0]) return
  await changeLanguage(lng || code)
}

export default i18n;
