import { type ComponentChildren } from 'preact';
import { PanelLoading } from '@/components/Panel';

type WorldCupPanelProps = {
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
  liveLabel?: string;
  newCount?: number;
  tools?: Array<'download' | 'summarize' | 'close'>;
};

function isChineseTitle(title: string) {
  return /[\u3400-\u9fff]/.test(title);
}

function DefaultTools({ tools }: { tools: Array<'download' | 'summarize' | 'close'> }) {
  return (
    <>
      {tools.includes('download') ? (
        <button className="wc-panel-tool wc-panel-tool--download" type="button" aria-label="Download panel">
          ↓
        </button>
      ) : null}
      {tools.includes('summarize') ? (
        <button className="wc-panel-tool wc-panel-tool--summary" type="button" aria-label="Summarize panel">
          ✦
        </button>
      ) : null}
      {tools.includes('close') ? (
        <button className="wc-panel-tool wc-panel-tool--close" type="button" aria-label="Close panel">
          ×
        </button>
      ) : null}
    </>
  );
}

export function WorldCupPanel({
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
  liveLabel = '实时',
  newCount,
  tools = ['download', 'summarize'],
}: WorldCupPanelProps) {
  const titleClass = `wm-panel-title wc-panel-title${isChineseTitle(title) ? ' is-cn' : ''}`;
  const panelClass = `wm-panel wc-panel${className ? ` ${className}` : ''}`;

  return (
    <section className={panelClass} data-panel-id={dataPanelId}>
      <header className="wm-panel-header wc-panel-header">
        <div className="wm-panel-title-wrap wc-panel-title-side">
          <h3 className={titleClass}>{title}</h3>
          {titleControls ? <div className="wm-panel-title-controls wc-panel-title-controls">{titleControls}</div> : null}
          {badge ? <span className={`wm-panel-badge wc-panel-badge ${status}`}>{badge}</span> : null}
          {newCount && newCount > 0 ? <span className="wc-panel-new">{newCount} 新</span> : null}
        </div>
        <div className="wm-panel-header-right wc-panel-actions">
          {controls}
          {status === 'live' ? <span className="wc-panel-live">{liveLabel}</span> : null}
          <DefaultTools tools={tools} />
          {count !== undefined ? <span className="wm-panel-count wc-panel-count">{count}</span> : null}
        </div>
      </header>
      {headerOverlay ? <div className="wm-panel-header-overlay wc-panel-header-overlay">{headerOverlay}</div> : null}
      <div className="wm-panel-body wc-panel-body">
        {loading ? <PanelLoading label={loadingLabel} detail={loadingDetail} /> : children}
      </div>
    </section>
  );
}
