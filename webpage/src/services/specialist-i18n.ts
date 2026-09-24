import { useI18n, type MessageKey } from './i18n';

type Params = Record<string, string | number>;

function translatedOrFallback(
  t: (key: MessageKey, params?: Params) => string,
  key: string,
  fallback: string,
  params?: Params,
) {
  const translated = t(key as MessageKey, params);
  return translated === key ? fallback : translated;
}

export function useSpecialistCopy(panelId: string) {
  const i18n = useI18n();
  return {
    ...i18n,
    copy: (field: string, fallback: string, params?: Params) => translatedOrFallback(
      i18n.t,
      `specialist.${panelId}.${field}`,
      fallback,
      params,
    ),
    shared: (field: string, fallback: string, params?: Params) => translatedOrFallback(
      i18n.t,
      `specialistShared.${field}`,
      fallback,
      params,
    ),
  };
}

export function specialistPanelMeta(
  panelId: string,
  title: string,
  description: string,
  t: (key: MessageKey, params?: Params) => string,
) {
  const firstTranslation = (fields: string[], fallback: string) => {
    for (const field of fields) {
      const key = `specialist.${panelId}.${field}`;
      const translated = t(key as MessageKey);
      if (translated !== key) return translated;
    }
    return fallback;
  };
  return {
    title: firstTranslation(['metaTitle', 'title'], title),
    description: firstTranslation(['metaDescription', 'helpText', 'question'], description),
  };
}
