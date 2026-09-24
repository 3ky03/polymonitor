import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { PanelLoading } from '@/components/Panel';
import { RuntimeStatusBadge } from '@/components/design-system/StatusPrimitives';
import type { PanelRuntimeStatus } from '@/panels/types';
import { useI18n } from '@/services/i18n';

const PANEL_ROW_RESIZE_STEP = 200;
const PANEL_COL_RESIZE_STEP = 260;
const PANEL_DRAG_THRESHOLD = 8;
const PANEL_MIN_ROW_SPAN = 1;
const PANEL_MAX_ROW_SPAN = 4;
const PANEL_MIN_COL_SPAN = 1;
const PANEL_MAX_COL_SPAN = 3;
const PANEL_CONTENT_ROOT_MARGIN = '240px 0px';

export type PanelLayoutPrefs = Record<string, { rowSpan?: number; colSpan?: number }>;
export type PanelSizeHint = 'default' | 'wide' | 'tall' | undefined;

type DragState = {
  active: boolean;
  started: boolean;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  offsetX: number;
  offsetY: number;
  rafId: number;
  ghost: HTMLElement | null;
  indicator: HTMLElement | null;
  lastTarget: HTMLElement | null;
  move?: (event: MouseEvent) => void;
  up?: () => void;
  keydown?: (event: KeyboardEvent) => void;
};

type ResizeState = {
  active: boolean;
  axis: 'row' | 'col';
  startX: number;
  startY: number;
  startSpan: number;
  move?: (event: MouseEvent) => void;
  up?: () => void;
};

type PanelWorkspaceSlotProps = {
  panelId: string;
  size: PanelSizeHint;
  layoutPrefs: PanelLayoutPrefs;
  children: ComponentChildren;
  loading?: boolean;
  runtimeStatus?: PanelRuntimeStatus;
  onRetry?: () => void;
  className?: string;
  layoutManaged?: boolean;
  resizeEnabled?: boolean;
  onMovePanel: (draggedPanelId: string, targetPanelId: string, insertAfter: boolean) => void;
  onResizePanel: (panelId: string, patch: { rowSpan?: number; colSpan?: number }) => void;
  onResetPanelLayout: (panelId: string) => void;
};

type PanelRuntimeBoundaryProps = {
  children: ComponentChildren;
  loading: boolean;
  status?: PanelRuntimeStatus;
  onRetry?: () => void;
};

function clampSpan(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value)));
}

function defaultPanelRowSpan(size: PanelSizeHint) {
  return size === 'tall' ? 2 : 1;
}

function defaultPanelColSpan(size: PanelSizeHint) {
  return size === 'wide' ? 2 : 1;
}

function getPanelLayout(layoutPrefs: PanelLayoutPrefs, panelId: string, size: PanelSizeHint) {
  const saved = layoutPrefs[panelId] || {};
  return {
    rowSpan: clampSpan(saved.rowSpan ?? defaultPanelRowSpan(size), PANEL_MIN_ROW_SPAN, PANEL_MAX_ROW_SPAN),
    colSpan: clampSpan(saved.colSpan ?? defaultPanelColSpan(size), PANEL_MIN_COL_SPAN, PANEL_MAX_COL_SPAN),
  };
}

function removeDragListeners(state: DragState) {
  if (state.move) document.removeEventListener('mousemove', state.move);
  if (state.up) document.removeEventListener('mouseup', state.up);
  if (state.keydown) document.removeEventListener('keydown', state.keydown);
  state.move = undefined;
  state.up = undefined;
  state.keydown = undefined;
}

function removeResizeListeners(state: ResizeState) {
  if (state.move) document.removeEventListener('mousemove', state.move);
  if (state.up) document.removeEventListener('mouseup', state.up);
  state.move = undefined;
  state.up = undefined;
}

export function PanelWorkspaceSlot({
  panelId,
  size,
  layoutPrefs,
  children,
  loading = false,
  runtimeStatus,
  onRetry,
  className = '',
  layoutManaged = true,
  resizeEnabled = true,
  onMovePanel,
  onResizePanel,
  onResetPanelLayout,
}: PanelWorkspaceSlotProps) {
  const { t } = useI18n();
  const slotRef = useRef<HTMLDivElement | null>(null);
  const [contentReady, setContentReady] = useState(!layoutManaged);
  const layout = getPanelLayout(layoutPrefs, panelId, size);
  const dragRef = useRef<DragState>({
    active: false,
    started: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
    offsetX: 0,
    offsetY: 0,
    rafId: 0,
    ghost: null,
    indicator: null,
    lastTarget: null,
  });
  const resizeRef = useRef<ResizeState>({
    active: false,
    axis: 'row',
    startX: 0,
    startY: 0,
    startSpan: 1,
  });

  const clearDragVisuals = () => {
    const state = dragRef.current;
    if (state.rafId) {
      window.cancelAnimationFrame(state.rafId);
      state.rafId = 0;
    }
    slotRef.current?.classList.remove('dragging-source');
    if (state.lastTarget) {
      state.lastTarget.classList.remove('panel-drop-target');
      state.lastTarget = null;
    }
    if (state.ghost) {
      const ghost = state.ghost;
      ghost.style.opacity = '0';
      window.setTimeout(() => ghost.remove(), 160);
      state.ghost = null;
    }
    if (state.indicator) {
      const indicator = state.indicator;
      indicator.style.opacity = '0';
      window.setTimeout(() => indicator.remove(), 160);
      state.indicator = null;
    }
  };

  const findDropTarget = (clientX: number, clientY: number) => {
    const hit = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
    const targetSlot = hit?.closest<HTMLElement>('.wm-panel-slot[data-workspace-panel-id]') || null;
    if (!targetSlot) return null;
    const targetPanelId = targetSlot.dataset.workspacePanelId;
    if (!targetPanelId || targetPanelId === panelId) return null;
    const rect = targetSlot.getBoundingClientRect();
    return {
      targetSlot,
      targetPanelId,
      insertAfter: clientY > rect.top + rect.height / 2 || (
        Math.abs(clientY - (rect.top + rect.height / 2)) < Math.min(48, rect.height / 4)
        && clientX > rect.left + rect.width / 2
      ),
      rect,
    };
  };

  const updateDropIndicator = (clientX: number, clientY: number) => {
    const state = dragRef.current;
    if (!state.indicator) return;
    const target = findDropTarget(clientX, clientY);
    if (!target) {
      state.indicator.style.opacity = '0';
      if (state.lastTarget) {
        state.lastTarget.classList.remove('panel-drop-target');
        state.lastTarget = null;
      }
      return;
    }
    if (target.targetSlot !== state.lastTarget) {
      state.lastTarget?.classList.remove('panel-drop-target');
      target.targetSlot.classList.add('panel-drop-target');
      state.lastTarget = target.targetSlot;
    }
    state.indicator.style.left = `${target.rect.left}px`;
    state.indicator.style.top = `${target.insertAfter ? target.rect.bottom : target.rect.top - 4}px`;
    state.indicator.style.width = `${target.rect.width}px`;
    state.indicator.style.opacity = '0.9';
  };

  const startDrag = (event: MouseEvent) => {
    if (event.button !== 0 || !slotRef.current) return;
    const target = event.target as HTMLElement;
    if (target.closest('button, a, input, select, textarea, [role="button"], .wm-panel-resize-handle, .wm-panel-col-resize-handle')) return;
    if (!target.closest('.wm-panel-header')) return;
    const rect = slotRef.current.getBoundingClientRect();
    dragRef.current = {
      active: true,
      started: false,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      rafId: 0,
      ghost: null,
      indicator: null,
      lastTarget: null,
    };
    event.preventDefault();

    const onMouseMove = (moveEvent: MouseEvent) => {
      const state = dragRef.current;
      if (!state.active || !slotRef.current) return;
      state.lastX = moveEvent.clientX;
      state.lastY = moveEvent.clientY;
      if (!state.started) {
        const dx = Math.abs(moveEvent.clientX - state.startX);
        const dy = Math.abs(moveEvent.clientY - state.startY);
        if (dx < PANEL_DRAG_THRESHOLD && dy < PANEL_DRAG_THRESHOLD) return;
        const sourceRect = slotRef.current.getBoundingClientRect();
        const sourcePanel = slotRef.current.querySelector<HTMLElement>(':scope > .wm-panel') || slotRef.current;
        state.started = true;
        const ghost = sourcePanel.cloneNode(true) as HTMLElement;
        ghost.querySelectorAll('iframe').forEach((frame) => frame.remove());
        ghost.classList.remove('dragging-source');
        ghost.classList.add('wm-panel-drag-ghost');
        ghost.setAttribute('aria-hidden', 'true');
        ghost.style.position = 'fixed';
        ghost.style.pointerEvents = 'none';
        ghost.style.zIndex = '10000';
        ghost.style.opacity = '0.9';
        ghost.style.boxShadow = '0 28px 80px rgba(0, 0, 0, 0.68), 0 0 0 1px rgba(146, 192, 246, 0.42), 0 0 40px rgba(78, 132, 198, 0.32)';
        ghost.style.transform = 'scale(1.02)';
        ghost.style.width = `${sourceRect.width}px`;
        ghost.style.height = `${sourceRect.height}px`;
        ghost.style.left = `${moveEvent.clientX - state.offsetX}px`;
        ghost.style.top = `${moveEvent.clientY - state.offsetY}px`;
        document.body.appendChild(ghost);
        state.ghost = ghost;
        slotRef.current.classList.add('dragging-source');
        const indicator = document.createElement('div');
        indicator.className = 'wm-panel-drop-indicator';
        document.body.appendChild(indicator);
        state.indicator = indicator;
      }
      if (state.rafId) window.cancelAnimationFrame(state.rafId);
      state.rafId = window.requestAnimationFrame(() => {
        const latest = dragRef.current;
        if (latest.ghost) {
          latest.ghost.style.left = `${latest.lastX - latest.offsetX}px`;
          latest.ghost.style.top = `${latest.lastY - latest.offsetY}px`;
        }
        updateDropIndicator(latest.lastX, latest.lastY);
        latest.rafId = 0;
      });
    };

    const finishDrag = () => {
      const state = dragRef.current;
      removeDragListeners(state);
      if (state.active && state.started) {
        const dropTarget = findDropTarget(state.lastX, state.lastY);
        if (dropTarget) onMovePanel(panelId, dropTarget.targetPanelId, dropTarget.insertAfter);
      }
      state.active = false;
      state.started = false;
      clearDragVisuals();
    };

    const onKeyDown = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key !== 'Escape') return;
      removeDragListeners(dragRef.current);
      dragRef.current.active = false;
      dragRef.current.started = false;
      clearDragVisuals();
    };

    dragRef.current.move = onMouseMove;
    dragRef.current.up = finishDrag;
    dragRef.current.keydown = onKeyDown;
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', finishDrag);
    document.addEventListener('keydown', onKeyDown);
  };

  const startResize = (axis: 'row' | 'col', event: MouseEvent) => {
    if (!resizeEnabled) return;
    event.preventDefault();
    event.stopPropagation();
    resizeRef.current.active = true;
    resizeRef.current.axis = axis;
    resizeRef.current.startX = event.clientX;
    resizeRef.current.startY = event.clientY;
    resizeRef.current.startSpan = axis === 'row' ? layout.rowSpan : layout.colSpan;
    slotRef.current?.classList.add(axis === 'row' ? 'resizing' : 'col-resizing');
    document.body.classList.add('panel-resize-active');

    const onMouseMove = (moveEvent: MouseEvent) => {
      const state = resizeRef.current;
      if (!state.active) return;
      if (state.axis === 'row') {
        const nextSpan = clampSpan(state.startSpan + Math.round((moveEvent.clientY - state.startY) / PANEL_ROW_RESIZE_STEP), PANEL_MIN_ROW_SPAN, PANEL_MAX_ROW_SPAN);
        onResizePanel(panelId, { rowSpan: nextSpan });
      } else {
        const nextSpan = clampSpan(state.startSpan + Math.round((moveEvent.clientX - state.startX) / PANEL_COL_RESIZE_STEP), PANEL_MIN_COL_SPAN, PANEL_MAX_COL_SPAN);
        onResizePanel(panelId, { colSpan: nextSpan });
      }
    };

    const onMouseUp = () => {
      const state = resizeRef.current;
      state.active = false;
      slotRef.current?.classList.remove('resizing', 'col-resizing');
      document.body.classList.remove('panel-resize-active');
      removeResizeListeners(state);
    };

    resizeRef.current.move = onMouseMove;
    resizeRef.current.up = onMouseUp;
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };

  useEffect(() => {
    return () => {
      const dragState = dragRef.current;
      const resizeState = resizeRef.current;
      dragState.active = false;
      resizeState.active = false;
      removeDragListeners(dragState);
      removeResizeListeners(resizeState);
      document.body.classList.remove('panel-resize-active');
      clearDragVisuals();
    };
  }, []);

  useEffect(() => {
    if (contentReady || !layoutManaged) return;
    const slot = slotRef.current;
    if (!slot || typeof IntersectionObserver === 'undefined') {
      setContentReady(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      setContentReady(true);
    }, { rootMargin: PANEL_CONTENT_ROOT_MARGIN });
    observer.observe(slot);
    return () => observer.disconnect();
  }, [contentReady, layoutManaged]);

  return (
    <div
      className={`wm-panel-slot ${layoutManaged ? 'is-layout-managed' : ''} ${className}`.trim()}
      data-workspace-panel-id={panelId}
      data-runtime-phase={runtimeStatus?.phase || 'idle'}
      data-content-ready={contentReady ? 'true' : 'false'}
      ref={slotRef}
      onMouseDown={startDrag}
      style={{
        '--wm-panel-row-span': String(layout.rowSpan),
        '--wm-panel-col-span': String(layout.colSpan),
      } as Record<string, string>}
    >
      {contentReady ? (
        <PanelRuntimeBoundary loading={loading} status={runtimeStatus} onRetry={onRetry}>
          {children}
        </PanelRuntimeBoundary>
      ) : <div className="wm-panel-slot-deferred" aria-hidden="true" />}
      {resizeEnabled ? (
        <>
          <button
            aria-label={t('panelRuntime.resizeHeight')}
            className="wm-panel-resize-handle"
            type="button"
            onDblClick={() => onResetPanelLayout(panelId)}
            onMouseDown={(resizeEvent) => startResize('row', resizeEvent)}
          />
          <button
            aria-label={t('panelRuntime.resizeWidth')}
            className="wm-panel-col-resize-handle"
            type="button"
            onDblClick={() => onResetPanelLayout(panelId)}
            onMouseDown={(resizeEvent) => startResize('col', resizeEvent)}
          />
        </>
      ) : null}
    </div>
  );
}

export function PanelRuntimeBoundary({
  children,
  loading,
  status,
  onRetry,
}: PanelRuntimeBoundaryProps) {
  const { t, formatDateTime } = useI18n();
  const phase = status?.phase || 'idle';
  const showBadge = Boolean(status) && phase !== 'idle' && phase !== 'loading';
  const showNotice = phase === 'stale' || phase === 'degraded' || phase === 'error' || phase === 'suspended';
  const updatedLabel = status?.updatedAt
    ? formatDateTime(status.updatedAt)
    : null;
  const label = phase === 'degraded'
    ? t('panelRuntime.degraded')
    : phase === 'stale'
      ? t('panelRuntime.stale')
      : phase === 'suspended'
        ? t('panelRuntime.suspended')
        : t('panelRuntime.unavailable');

  return (
    <>
      {children}
      {showBadge && status ? (
        <div className="ds-panel-status-anchor">
          <RuntimeStatusBadge status={status} compact />
        </div>
      ) : null}
      {loading ? (
        <div className="wm-panel-slot-loading">
          <PanelLoading detail={t('panelRuntime.syncingPanel')} />
        </div>
      ) : null}
      {showNotice && !loading ? (
        <div className={`wm-panel-runtime-notice is-${phase}`} role="status">
          <span>{label}{updatedLabel ? ` · ${t('panelRuntime.updated', { time: updatedLabel })}` : ''}</span>
          {onRetry && phase !== 'suspended' ? <button type="button" onClick={onRetry}>{t('panelRuntime.retry')}</button> : null}
        </div>
      ) : null}
    </>
  );
}
