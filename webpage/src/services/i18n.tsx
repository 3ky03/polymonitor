import { createContext, type ComponentChildren } from 'preact';
import { useContext, useEffect, useMemo, useState } from 'preact/hooks';
import en from '@/locales/en.json';
import zh from '@/locales/zh.json';
import { specialistEn, specialistZh } from '@/locales/specialist';

export type Locale = 'en' | 'zh';
const enCatalog = { ...en, ...specialistEn };
const zhCatalog: Record<keyof typeof enCatalog, string> = { ...zh, ...specialistZh };
export type MessageKey = keyof typeof enCatalog;
type MessageParams = Record<string, string | number>;

const STORAGE_KEY = 'polydata:locale:v1';
const catalogs: Record<Locale, Record<MessageKey, string>> = { en: enCatalog, zh: zhCatalog };

function detectLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'zh') return stored;
  } catch {
    // Storage may be blocked. English remains the deterministic default.
  }
  return 'en';
}

function interpolate(message: string, params?: MessageParams) {
  if (!params) return message;
  return message.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) => {
    const value = params[key];
    return value === undefined ? match : String(value);
  });
}

type I18nValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, params?: MessageParams) => string;
  formatDateTime: (value: string | number | Date) => string;
  formatRelativeTime: (value?: string | number | Date | null) => string;
  formatDuration: (seconds?: number | null) => string;
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string;
  formatPercent: (value: number, options?: Intl.NumberFormatOptions) => string;
};

const I18nContext = createContext<I18nValue | null>(null);

export function LocaleProvider({ children }: { children: ComponentChildren }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale);
  useEffect(() => {
    document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en';
    if (locale === 'zh') void import('@fontsource-variable/noto-sans-sc/wght.css');
  }, [locale]);
  const value = useMemo<I18nValue>(() => {
    const intlLocale = locale === 'zh' ? 'zh-CN' : 'en-US';
    const setLocale = (next: Locale) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Keep the in-memory language even when persistence is unavailable.
      }
      document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en';
      setLocaleState(next);
    };
    return {
      locale,
      setLocale,
      t: (key, params) => interpolate(catalogs[locale][key] || catalogs.en[key] || key, params),
      formatDateTime: (input) => {
        const date = input instanceof Date ? input : new Date(input);
        return Number.isNaN(date.getTime())
          ? '—'
          : new Intl.DateTimeFormat(intlLocale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
      },
      formatRelativeTime: (input) => {
        if (input == null || input === '') return '—';
        const date = input instanceof Date ? input : new Date(input);
        if (Number.isNaN(date.getTime())) return '—';
        const diffSeconds = (date.getTime() - Date.now()) / 1_000;
        const absoluteSeconds = Math.abs(diffSeconds);
        const [divisor, unit]: [number, Intl.RelativeTimeFormatUnit] = absoluteSeconds < 60
          ? [1, 'second']
          : absoluteSeconds < 3_600
            ? [60, 'minute']
            : absoluteSeconds < 86_400
              ? [3_600, 'hour']
              : [86_400, 'day'];
        return new Intl.RelativeTimeFormat(intlLocale, { numeric: 'auto', style: 'short' })
          .format(Math.round(diffSeconds / divisor), unit);
      },
      formatDuration: (seconds) => {
        if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
        const [value, unit]: [number, Intl.NumberFormatOptions['unit']] = seconds < 60
          ? [seconds, 'second']
          : seconds < 3_600
            ? [seconds / 60, 'minute']
            : seconds < 86_400
              ? [seconds / 3_600, 'hour']
              : [seconds / 86_400, 'day'];
        return new Intl.NumberFormat(intlLocale, {
          style: 'unit',
          unit,
          unitDisplay: 'narrow',
          maximumFractionDigits: 0,
        }).format(Math.round(value));
      },
      formatNumber: (input, options) => new Intl.NumberFormat(intlLocale, options).format(input),
      formatPercent: (input, options) => new Intl.NumberFormat(intlLocale, {
        style: 'percent',
        maximumFractionDigits: 1,
        ...options,
      }).format(input),
    };
  }, [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside LocaleProvider');
  return value;
}
