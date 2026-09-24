import { type ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { PanelLoading } from '@/components/Panel';
import {
  filterWorldCupNews,
  getNextWorldCupMatch,
  applyWorldCupMarketLinks,
  loadWorldCupDashboard,
  matchCity,
  mergeWorldCupSelectedMarkets,
  refreshWorldCupDashboard,
  WORLD_CUP_HOST_MATCH_COUNTS,
} from './data';
import { WorldCupMap } from './WorldCupMap';
import { CalendarPanel } from './panels/CalendarPanel';
import { GroupAdvancePanel } from './panels/GroupAdvancePanel';
import { GroupTablePanel } from './panels/GroupTablePanel';
import { HostVenuePanel } from './panels/HostVenuePanel';
import { InjuryLoadPanel } from './panels/InjuryLoadPanel';
import { LineupBoardPanel } from './panels/LineupBoardPanel';
import { MarketBoardPanel } from './panels/MarketBoardPanel';
import { MatchControlPanel } from './panels/MatchControlPanel';
import { MatchModelPanel } from './panels/MatchModelPanel';
import { MatchTempoPanel } from './panels/MatchTempoPanel';
import { MediaWirePanel } from './panels/MediaWirePanel';
import { NewsImpactPanel } from './panels/NewsImpactPanel';
import { NewsPanel } from './panels/NewsPanel';
import { LineMovementPanel } from './panels/LineMovementPanel';
import { OddsLiquidityPanel } from './panels/OddsLiquidityPanel';
import { OddsSourceCoveragePanel } from './panels/OddsSourceCoveragePanel';
import { RefCardsPanel } from './panels/RefCardsPanel';
import { SourceAuditPanel } from './panels/SourceAuditPanel';
import { TeamPowerPanel } from './panels/TeamPowerPanel';
import { TeamStatusPanel } from './panels/TeamStatusPanel';
import { TravelLoadPanel } from './panels/TravelLoadPanel';
import { VenueRefPanel } from './panels/VenueRefPanel';
import { VenueRiskPanel } from './panels/VenueRiskPanel';
import { WinProbabilityPanel } from './panels/WinProbabilityPanel';
import type {
  WorldCupDashboardPayload,
  WorldCupMatch,
  WorldCupWorkspaceProps,
} from './types';
import {
  WORLD_CUP_PANEL_DRAG_THRESHOLD,
  WORLD_CUP_PANEL_ORDER,
  WORLD_CUP_PANEL_ORDER_STORAGE_KEY,
  readWorldCupPanelOrder,
  reorderWorldCupPanels,
  type MatchFilter,
  type WorldCupPanelId,
} from './panels/registry';
import { kickoffDay, kickoffTime } from './panels/formatters';
import { groupName } from './panels/panelUtils';
import { latestContentNewsFallback } from './panels/newsUtils';
import {
  buildBroadcastSignals,
  buildHostOpsSignals,
  buildInjurySignals,
  buildLineupSignals,
  buildLocalMediaSignals,
  buildMarketSignals,
  buildMatchSignals,
  buildOddsSignals,
  buildOfficialFactSignals,
  buildPlayerPoolSignals,
  buildRefVenueSignals,
  buildRiskSignals,
  buildSquadSignals,
  buildTacticalSignals,
  buildXgSignals,
} from './panels/signalBuilders';
import './styles/worldcup-reference-ui.css';
import './styles/layout.css';
import './styles/panels/index.css';
import './styles/panels/worldmonitor-chrome.css';

const WORLD_CUP_DASHBOARD_CLASS = 'wm-dashboard wm-worldcup-dashboard wm-worldcup-v5';

function formatCountdown(match: WorldCupMatch | null, now: Date) {
  if (!match) return '--';
  const diffSeconds = Math.max(0, Math.floor((new Date(match.kickoffUtc).getTime() - now.getTime()) / 1000));
  const days = Math.floor(diffSeconds / 86400);
  const hours = Math.floor((diffSeconds % 86400) / 3600);
  const minutes = Math.floor((diffSeconds % 3600) / 60);
  const seconds = diffSeconds % 60;
  const pad = (value: number) => String(value).padStart(2, '0');
  if (days > 0) return `${days}D ${pad(hours)}H ${pad(minutes)}M ${pad(seconds)}S`;
  return `${pad(hours)}H ${pad(minutes)}M ${pad(seconds)}S`;
}

function formatUpdatedAgo(iso: string, now: Date) {
  const diffSeconds = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 1000));
  if (diffSeconds < 60) return `${diffSeconds}s ago`;
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}

function formatBjtClock(now: Date) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(now);
}

function WorldCupPanelSlot({
  panelId,
  draggingId,
  children,
  onMovePanel,
  onDragStateChange,
}: {
  panelId: WorldCupPanelId;
  draggingId: WorldCupPanelId | null;
  children: ComponentChildren;
  onMovePanel: (draggedId: WorldCupPanelId, targetId: WorldCupPanelId, insertAfter: boolean) => void;
  onDragStateChange: (panelId: WorldCupPanelId | null) => void;
}) {
  const slotRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef({
    active: false,
    started: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
    offsetX: 0,
    offsetY: 0,
    rafId: 0,
    ghost: null as HTMLElement | null,
    indicator: null as HTMLElement | null,
    lastTarget: null as HTMLElement | null,
  });

  const clearDragVisuals = () => {
    const state = dragRef.current;
    if (state.rafId) {
      window.cancelAnimationFrame(state.rafId);
      state.rafId = 0;
    }
    slotRef.current?.classList.remove('dragging-source');
    state.lastTarget?.classList.remove('panel-drop-target');
    state.lastTarget = null;
    if (state.ghost) {
      const ghost = state.ghost;
      ghost.style.opacity = '0';
      window.setTimeout(() => ghost.remove(), 140);
      state.ghost = null;
    }
    if (state.indicator) {
      const indicator = state.indicator;
      indicator.style.opacity = '0';
      window.setTimeout(() => indicator.remove(), 140);
      state.indicator = null;
    }
    onDragStateChange(null);
  };

  const findDropTarget = (clientX: number, clientY: number) => {
    const hit = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
    const targetSlot = hit?.closest<HTMLElement>('.wm-worldcup-matrix-cell[data-worldcup-panel-id]') || null;
    if (!targetSlot) return null;
    const targetPanelId = targetSlot.dataset.worldcupPanelId as WorldCupPanelId | undefined;
    if (!targetPanelId || targetPanelId === panelId) return null;
    const rect = targetSlot.getBoundingClientRect();
    return {
      targetSlot,
      targetPanelId,
      insertAfter: clientY > rect.top + rect.height / 2 || (
        Math.abs(clientY - (rect.top + rect.height / 2)) < Math.min(44, rect.height / 4)
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
      state.lastTarget?.classList.remove('panel-drop-target');
      state.lastTarget = null;
      return;
    }
    if (target.targetSlot !== state.lastTarget) {
      state.lastTarget?.classList.remove('panel-drop-target');
      target.targetSlot.classList.add('panel-drop-target');
      state.lastTarget = target.targetSlot;
    }
    state.indicator.style.left = `${target.rect.left}px`;
    state.indicator.style.top = `${target.insertAfter ? target.rect.bottom : target.rect.top - 3}px`;
    state.indicator.style.width = `${target.rect.width}px`;
    state.indicator.style.opacity = '0.92';
  };

  const startDrag = (event: MouseEvent) => {
    if (event.button !== 0 || !slotRef.current) return;
    const target = event.target as HTMLElement;
    if (target.closest('button, a, input, select, textarea, [role="button"]')) return;
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
        if (dx < WORLD_CUP_PANEL_DRAG_THRESHOLD && dy < WORLD_CUP_PANEL_DRAG_THRESHOLD) return;
        const sourceRect = slotRef.current.getBoundingClientRect();
        const sourcePanel = slotRef.current.querySelector<HTMLElement>(':scope > .wm-worldcup-panel') || slotRef.current;
        state.started = true;
        onDragStateChange(panelId);
        const ghost = sourcePanel.cloneNode(true) as HTMLElement;
        ghost.querySelectorAll('iframe').forEach((frame) => frame.remove());
        ghost.classList.add('wm-panel-drag-ghost', 'wm-worldcup-drag-ghost');
        ghost.setAttribute('aria-hidden', 'true');
        ghost.style.position = 'fixed';
        ghost.style.pointerEvents = 'none';
        ghost.style.zIndex = '10000';
        ghost.style.width = `${sourceRect.width}px`;
        ghost.style.height = `${sourceRect.height}px`;
        ghost.style.left = `${moveEvent.clientX - state.offsetX}px`;
        ghost.style.top = `${moveEvent.clientY - state.offsetY}px`;
        document.body.appendChild(ghost);
        state.ghost = ghost;
        slotRef.current.classList.add('dragging-source');
        const indicator = document.createElement('div');
        indicator.className = 'wm-panel-drop-indicator wm-worldcup-drop-indicator';
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
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', finishDrag);
      document.removeEventListener('keydown', onKeyDown);
      if (state.active && state.started) {
        const targetDrop = findDropTarget(state.lastX, state.lastY);
        if (targetDrop) onMovePanel(panelId, targetDrop.targetPanelId, targetDrop.insertAfter);
      }
      state.active = false;
      state.started = false;
      clearDragVisuals();
    };

    const onKeyDown = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key !== 'Escape') return;
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', finishDrag);
      document.removeEventListener('keydown', onKeyDown);
      dragRef.current.active = false;
      dragRef.current.started = false;
      clearDragVisuals();
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', finishDrag);
    document.addEventListener('keydown', onKeyDown);
  };

  useEffect(() => clearDragVisuals, []);

  return (
    <div
      ref={slotRef}
      className={`wm-worldcup-matrix-cell wm-worldcup-draggable-cell ${draggingId === panelId ? 'dragging-source' : ''}`}
      data-worldcup-panel-id={panelId}
      onMouseDown={(event) => startDrag(event as MouseEvent)}
    >
      {children}
    </div>
  );
}

function useWorldCupDashboard(marketGroups: WorldCupWorkspaceProps['marketGroups']) {
  const [basePayload, setBasePayload] = useState<WorldCupDashboardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadWorldCupDashboard()
      .then((nextPayload) => {
        if (cancelled) return;
        setBasePayload(nextPayload);
        setError(null);
        void refreshWorldCupDashboard(nextPayload)
          .then((refreshedPayload) => {
            if (!cancelled) setBasePayload(refreshedPayload);
          })
          .catch(() => {
            // Keep the first-paint schedule visible when runtime enrichment is slow.
          });
      })
      .catch((loadError) => {
        if (cancelled) return;
        setError(loadError instanceof Error ? loadError.message : 'World Cup payload unavailable.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const payload = useMemo(
    () => (basePayload ? applyWorldCupMarketLinks(basePayload, marketGroups) : null),
    [basePayload, marketGroups],
  );
  return { payload, loading, error };
}


export function WorldCupWorkspace({ now, marketGroups, latestContent, geoShockPayload }: WorldCupWorkspaceProps) {
  const { payload, loading, error } = useWorldCupDashboard(marketGroups);
  const [selectedMatchId, setSelectedMatchId] = useState<string | null>(null);
  const [selectedCityId, setSelectedCityId] = useState<string | null>(null);
  const [filter, setFilter] = useState<MatchFilter>('future');
  const [selectedGroup, setSelectedGroup] = useState('Group A');
  const [panelOrder, setPanelOrder] = useState<WorldCupPanelId[]>(readWorldCupPanelOrder);
  const [draggingPanelId, setDraggingPanelId] = useState<WorldCupPanelId | null>(null);

  const nextMatch = useMemo(() => payload ? getNextWorldCupMatch(payload.matches, now) : null, [now, payload]);

  useEffect(() => {
    if (!payload || selectedMatchId) return;
    const next = getNextWorldCupMatch(payload.matches, now) || payload.matches[0] || null;
    setSelectedMatchId(next?.id || null);
    setSelectedCityId(next?.cityId || payload.cities[0]?.id || null);
  }, [now, payload, selectedMatchId]);

  const selectedMatch = payload?.matches.find((match) => match.id === selectedMatchId) || nextMatch || payload?.matches[0] || null;
  const selectedOdds = payload?.odds.filter((item) => item.matchId === selectedMatch?.id) || [];
  const selectedMarkets = useMemo(
    () => mergeWorldCupSelectedMarkets(selectedMatch, marketGroups, selectedOdds),
    [marketGroups, selectedMatch, selectedOdds],
  );
  const news = useMemo(() => {
    const runtimeNews = payload?.news || [];
    const contentNews = filterWorldCupNews(latestContent, selectedMatch);
    const seen = new Set<string>();
    const filteredNews = [...runtimeNews, ...contentNews].filter((item) => {
      const key = `${item.source}:${item.title}`.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 24);
    return filteredNews.length ? filteredNews : latestContentNewsFallback(latestContent);
  }, [latestContent, payload?.news, selectedMatch]);

  useEffect(() => {
    if (selectedMatch?.group) setSelectedGroup(selectedMatch.group);
  }, [selectedMatch?.group]);

  useEffect(() => {
    window.localStorage.setItem(WORLD_CUP_PANEL_ORDER_STORAGE_KEY, JSON.stringify(panelOrder));
  }, [panelOrder]);

  if (loading && !payload) {
    return (
      <main className={WORLD_CUP_DASHBOARD_CLASS}>
        <PanelLoading label="Loading World Cup workspace" detail="Syncing schedule, host cities and market links" />
      </main>
    );
  }

  if (!payload) {
    return (
      <main className={WORLD_CUP_DASHBOARD_CLASS}>
        <div className="wm-banner error">{error || 'World Cup workspace unavailable.'}</div>
      </main>
    );
  }

  const selectedCity = matchCity(payload.cities, selectedCityId || selectedMatch?.cityId || nextMatch?.cityId || payload.cities[0]?.id || '');
  const selectedWeather = payload.weather.find((item) => item.cityId === selectedCity.id) || null;
  const nextCity = nextMatch ? matchCity(payload.cities, nextMatch.cityId) : null;
  const geoConflictEvents = useMemo(
    () => (geoShockPayload?.items || []).filter((item) => {
      const lat = Number(item.latitude);
      const lon = Number(item.longitude);
      return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    }),
    [geoShockPayload],
  );
  const linkedMarketCount = payload.matches.filter((match) => match.marketLinked).length + selectedMarkets.length;
  const weatherWatchCount = payload.weather.filter((item) => {
    const rain = item.current.precipitationProbability ?? 0;
    const wind = item.current.windKph ?? 0;
    return rain >= 35 || wind >= 22 || /storm|thunder|rain|humid|watch/i.test(item.current.condition);
  }).length;
  const selectedCityMatchCount = Math.max(
    WORLD_CUP_HOST_MATCH_COUNTS[selectedCity.id] || 0,
    payload.matches.filter((match) => match.cityId === selectedCity.id).length,
  );
  const plannedMatchTotal = payload.cities.reduce((sum, city) => sum + (WORLD_CUP_HOST_MATCH_COUNTS[city.id] || 0), 0);
  const displayOdds = selectedOdds;
  const matchSignals = buildMatchSignals(selectedMatch, selectedMarkets);
  const hostOpsSignals = buildHostOpsSignals(payload, selectedCity.id);
  const marketSignals = buildMarketSignals(selectedMarkets, selectedMatch);
  const squadSignals = buildSquadSignals(payload, selectedMatch);
  const oddsSignals = buildOddsSignals(displayOdds, selectedMatch);
  const riskSignals = buildRiskSignals(payload, selectedMatch);
  const broadcastSignals = buildBroadcastSignals(selectedMatch);
  const officialFactSignals = buildOfficialFactSignals(payload, selectedMatch, selectedCity);
  const injurySignals = buildInjurySignals(payload, selectedMatch);
  const lineupSignals = buildLineupSignals(payload, selectedMatch);
  const playerPoolSignals = buildPlayerPoolSignals(payload, selectedMatch);
  const xgSignals = buildXgSignals(payload, selectedMatch);
  const tacticalSignals = buildTacticalSignals(payload, selectedMatch);
  const localMediaSignals = buildLocalMediaSignals(payload, selectedMatch);
  const refVenueSignals = buildRefVenueSignals(payload, selectedMatch);
  const terminalMetrics = [
    { label: 'next_kickoff', value: formatCountdown(nextMatch, now), meta: nextMatch ? `${nextMatch.homeTeam} vs ${nextMatch.awayTeam}` : 'schedule complete' },
    { label: 'selected_city', value: selectedCity.city, meta: `${selectedCity.country} · ${selectedCityMatchCount} matches` },
    { label: 'match_count', value: String(Math.max(payload.matches.length, plannedMatchTotal)), meta: `${payload.cities.length} host cities` },
    { label: 'market_links', value: String(linkedMarketCount), meta: 'Dashboard feed' },
  ];
  const worldCupPanels: Record<WorldCupPanelId, ComponentChildren> = {
    calendar: (
      <CalendarPanel
        matches={payload.matches}
        selectedMatchId={selectedMatch?.id || null}
        filter={filter}
        now={now}
        onFilter={setFilter}
        onSelectMatch={(match) => {
          setSelectedMatchId(match.id);
          setSelectedCityId(match.cityId);
        }}
      />
    ),
    'match-control': (
      <MatchControlPanel
        match={selectedMatch}
        markets={selectedMarkets}
        odds={displayOdds}
        weather={selectedWeather}
        city={selectedCity}
        facts={officialFactSignals}
        broadcast={broadcastSignals}
      />
    ),
    news: <NewsPanel items={news} />,
    'win-probability': <WinProbabilityPanel markets={selectedMarkets} odds={displayOdds} match={selectedMatch} />,
    'line-movement': <LineMovementPanel payload={payload} odds={displayOdds} markets={selectedMarkets} match={selectedMatch} />,
    'odds-source-coverage': <OddsSourceCoveragePanel payload={payload} odds={payload.odds} markets={selectedMarkets} match={selectedMatch} />,
    'venue-risk': <VenueRiskPanel payload={payload} match={selectedMatch} weather={selectedWeather} />,
    'host-venue': (
      <HostVenuePanel
        payload={payload}
        selectedCityId={selectedCity.id}
        onSelectCity={setSelectedCityId}
        hostOps={hostOpsSignals}
        risk={riskSignals}
        refVenue={refVenueSignals}
      />
    ),
    'market-board': <MarketBoardPanel markets={selectedMarkets} odds={displayOdds} />,
    'group-advance': <GroupAdvancePanel matches={payload.matches} group={selectedGroup || groupName(selectedMatch)} onGroupChange={setSelectedGroup} />,
    'team-power': <TeamPowerPanel payload={payload} match={selectedMatch} />,
    'injury-load': <InjuryLoadPanel payload={payload} match={selectedMatch} injuries={injurySignals} />,
    'match-tempo': <MatchTempoPanel xgSignals={xgSignals} tacticalSignals={tacticalSignals} />,
    'ref-cards': <RefCardsPanel refVenue={refVenueSignals} match={selectedMatch} weather={selectedWeather} />,
    'travel-load': <TravelLoadPanel payload={payload} match={selectedMatch} />,
    'news-impact': <NewsImpactPanel news={news} />,
    'team-status': <TeamStatusPanel payload={payload} match={selectedMatch} injuries={injurySignals} players={playerPoolSignals} />,
    'lineup-board': <LineupBoardPanel lineups={lineupSignals} squadSignals={squadSignals} />,
    'match-model': <MatchModelPanel xgSignals={xgSignals} tacticalSignals={tacticalSignals} />,
    'group-table': <GroupTablePanel matches={payload.matches} group={selectedGroup || groupName(selectedMatch)} onGroupChange={setSelectedGroup} />,
    'media-wire': <MediaWirePanel news={news} matchSignals={matchSignals} localMedia={localMediaSignals} />,
    'odds-liquidity': <OddsLiquidityPanel odds={displayOdds} markets={selectedMarkets} oddsSignals={oddsSignals} marketSignals={marketSignals} />,
    'venue-ref': <VenueRefPanel refVenue={refVenueSignals} risk={riskSignals} payload={payload} match={selectedMatch} />,
    'source-audit': <SourceAuditPanel payload={payload} markets={selectedMarkets} odds={displayOdds} news={news} />,
  };
  const orderedPanels = [...panelOrder, ...WORLD_CUP_PANEL_ORDER.filter((id) => !panelOrder.includes(id))]
    .filter((id, index, array) => array.indexOf(id) === index);
  const movePanel = (draggedId: WorldCupPanelId, targetId: WorldCupPanelId, insertAfter: boolean) => {
    setPanelOrder((current) => reorderWorldCupPanels(current, draggedId, targetId, insertAfter));
    setDraggingPanelId(null);
  };

  return (
    <main className={WORLD_CUP_DASHBOARD_CLASS}>
      <section className="wm-worldcup-hero">
        <div className="wm-worldcup-hero-copy">
          <div className="wm-worldcup-hero-topline">
            <span>FIFA WORLD CUP 2026</span>
            <span>UPDATED {formatUpdatedAgo(payload.generatedAt, now)}</span>
            <span>{formatBjtClock(now)} BJT</span>
          </div>
          <h1>World Cup Host Atlas</h1>
          <p>Venue schedule, match markets, weather watch and host-city intelligence in one premium World Cup workspace.</p>
          <div className="wm-worldcup-next-context">
            <span>NEXT MATCH CONTEXT</span>
            <strong>{nextMatch ? `${nextMatch.homeTeam} / ${nextMatch.awayTeam}` : 'Tournament window complete'}</strong>
            <em>{nextMatch ? `${kickoffDay(nextMatch)} · ${kickoffTime(nextMatch)} BJT · ${nextCity?.city || nextMatch.city}` : 'No upcoming kickoff in schedule'}</em>
          </div>
        </div>
        <div className="wm-worldcup-hero-metrics">
          {terminalMetrics.map((metric) => (
            <div className="wm-worldcup-terminal-card" key={metric.label}>
              <span><b>$</b> {metric.label}</span>
              <strong>{metric.value}</strong>
              <em>{metric.meta}</em>
            </div>
          ))}
        </div>
      </section>

      <section className="wm-worldcup-map-section">
        <div className="wm-map-header">
          <div className="wm-map-heading">
            <span className="wm-map-kicker">United States · Canada · Mexico</span>
            <div className="wm-map-title">World Cup Host Atlas</div>
          </div>
          <div className="wm-map-status-strip">
            <span className="wm-map-kpi-chip"><b>{payload.cities.length}</b> Host Cities</span>
            <span className="wm-map-kpi-chip"><b>{Math.max(payload.matches.length, plannedMatchTotal)}</b> Matches</span>
            <span className="wm-map-kpi-chip"><b>{linkedMarketCount}</b> Market-linked</span>
            <span className="wm-map-kpi-chip"><b>{weatherWatchCount}</b> Weather watch</span>
            <span className="wm-status-chip">WORLD CUP · PRE-TOURNAMENT</span>
            <div className="wm-map-clock">{formatBjtClock(now)} BJT</div>
            <span className="wm-map-next-chip">
              {nextCity && nextMatch
                ? `NEXT · ${nextCity.city} · M#${nextMatch.fifaMatchNumber || '--'} · ${formatCountdown(nextMatch, now)}`
                : 'NEXT · --'}
            </span>
          </div>
        </div>
        <WorldCupMap
          cities={payload.cities}
          matches={payload.matches}
          weather={payload.weather}
          marketGroups={marketGroups}
          odds={payload.odds}
          conflicts={geoConflictEvents}
          nextMatch={nextMatch}
          selectedCityId={selectedCityId}
          selectedMatchId={selectedMatch?.id || null}
          onSelectCity={setSelectedCityId}
          onSelectMatch={setSelectedMatchId}
        />
      </section>

      <section className="wm-worldcup-panel-matrix">
        {orderedPanels.map((panelId) => (
          <WorldCupPanelSlot
            draggingId={draggingPanelId}
            key={panelId}
            panelId={panelId}
            onDragStateChange={setDraggingPanelId}
            onMovePanel={movePanel}
          >
            {worldCupPanels[panelId]}
          </WorldCupPanelSlot>
        ))}
      </section>
    </main>
  );
}
