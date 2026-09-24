import { useEffect, useState } from 'preact/hooks';
import { activatePwaUpdate, getPwaState, installPwa, subscribePwa } from '@/services/pwa';
import { useI18n } from '@/services/i18n';

export function PwaControl() {
  const { t } = useI18n();
  const [state, setState] = useState(getPwaState);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => subscribePwa(() => setState({ ...getPwaState() })), []);

  const showButton = !state.online || state.updateReady || state.installable || state.installed;
  return (
    <>
      {showButton ? (
        <button
          className={`wm-pwa-control ${!state.online ? 'is-offline' : ''} ${state.updateReady ? 'is-update' : ''}`}
          type="button"
          disabled={state.installing || (state.installed && !state.updateReady)}
          onClick={() => state.updateReady ? activatePwaUpdate() : void installPwa()}
        >
          <i aria-hidden="true" />
          {state.updateReady ? t('pwa.reloadShort')
            : !state.online ? t('pwa.offline')
            : state.installing ? t('pwa.installing')
            : state.installed ? t('pwa.installed')
            : t('pwa.install')}
        </button>
      ) : null}
      {state.updateReady && !dismissed ? (
        <aside className="wm-pwa-update" aria-live="polite">
          <div>
            <span>{t('pwa.updateReady')}</span>
            <strong>{t('pwa.updateTitle')}</strong>
            <p>{t('pwa.updateDetail')}</p>
          </div>
          <div>
            <button type="button" onClick={activatePwaUpdate}>{t('pwa.reload')}</button>
            <button type="button" className="secondary" onClick={() => setDismissed(true)}>{t('pwa.dismiss')}</button>
          </div>
        </aside>
      ) : null}
    </>
  );
}
