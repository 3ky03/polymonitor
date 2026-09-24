import { type ComponentChildren } from 'preact';
import { useI18n } from '@/services/i18n';

type PanelProps = {
  title: string;
  badge?: string;
  count?: string | number;
  status?: 'live' | 'locked' | 'muted';
  titleControls?: ComponentChildren;
  controls?: ComponentChildren;
  headerOverlay?: ComponentChildren;
  className?: string;
  dataPanelId?: string;
  loading?: boolean;
  loadingLabel?: string;
  loadingDetail?: string;
  children: ComponentChildren;
};

type PanelLoadingProps = {
  label?: string;
  detail?: string;
  className?: string;
};

export function PanelLoading({ label, detail, className }: PanelLoadingProps) {
  const { t } = useI18n();
  const resolvedLabel = label ?? t('panelRuntime.loading');
  const resolvedDetail = detail ?? t('panelRuntime.syncing');
  return (
    <div className={`wm-panel-loading${className ? ` ${className}` : ''}`} role="status" aria-live="polite">
      <div className="wm-panel-loading-radar" aria-hidden="true">
        <span className="wm-panel-loading-sweep" />
        <span className="wm-panel-loading-dot" />
      </div>
      <strong>{resolvedLabel}</strong>
      {resolvedDetail ? <em>{resolvedDetail}</em> : null}
      <div className="wm-panel-loading-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

export function Panel({
  title,
  badge,
  count,
  status = 'live',
  titleControls,
  controls,
  headerOverlay,
  className,
  dataPanelId,
  loading = false,
  loadingLabel,
  loadingDetail,
  children,
}: PanelProps) {
  return (
    <section className={`wm-panel${className ? ` ${className}` : ''}`} data-panel-id={dataPanelId}>
      <header className="wm-panel-header">
        <div className="wm-panel-title-wrap">
          <h3 className="wm-panel-title">{title}</h3>
          {titleControls ? <div className="wm-panel-title-controls">{titleControls}</div> : null}
          {badge ? <span className={`wm-panel-badge ${status}`}>{badge}</span> : null}
        </div>
        <div className="wm-panel-header-right">
          {controls}
          {count !== undefined ? <span className="wm-panel-count">{count}</span> : null}
        </div>
      </header>
      {headerOverlay ? <div className="wm-panel-header-overlay">{headerOverlay}</div> : null}
      <div className="wm-panel-body">
        {loading ? <PanelLoading label={loadingLabel} detail={loadingDetail} /> : children}
      </div>
    </section>
  );
}
