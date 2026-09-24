import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import './styles.css';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type TabId = 'tariffs' | 'flows' | 'barriers' | 'revenue' | 'strategic';
type Severity = 'red' | 'amber' | 'green';

type Metric = {
  label: string;
  value: string;
  tone?: Severity;
};

type TradeAlert = {
  id: string;
  tab: TabId;
  title: string;
  source: string;
  jurisdiction: string;
  score: number;
  severity: Severity;
  status: string;
  action: string;
  eventCode: string;
  metricA: Metric;
  metricB: Metric;
  metricC: Metric;
  impact: string;
  description: string;
  affected: string;
};

const TABS: Array<{ id: TabId; label: string; hint: string }> = [
  { id: 'tariffs', label: 'Tariff', hint: 'Effective burden, not headline MFN rates' },
  { id: 'flows', label: 'Flow', hint: 'Trade-flow breaks and supplier substitution' },
  { id: 'barriers', label: 'Barrier', hint: 'TBT, SPS, licensing, CBAM, quotas' },
  { id: 'revenue', label: 'Duty', hint: 'Customs duties as paid-tariff evidence' },
  { id: 'strategic', label: 'Controls', hint: 'Export controls, sanctions, critical minerals' },
];

const DEFAULT_TAB_META = TABS[0] as { id: TabId; label: string; hint: string };

const ALERTS: TradeAlert[] = [
  {
    id: 'tariff-clean-energy',
    tab: 'tariffs',
    title: 'US clean-energy import stack',
    source: 'WTO baseline + US tariff actions + trade remedies',
    jurisdiction: 'US vs China / Southeast Asia',
    score: 86,
    severity: 'red',
    status: 'red',
    action: 'TARIFF STACK',
    eventCode: 'HS8541/8507',
    metricA: { label: 'Baseline', value: 'MFN + Section 301', tone: 'amber' },
    metricB: { label: 'Products', value: 'Solar, EVs, batteries', tone: 'red' },
    metricC: { label: 'Transmission', value: 'Input cost -> margin', tone: 'red' },
    impact: 'Domestic module makers and upstream metals gain leverage; installers, EV assemblers, and import-heavy retailers absorb cost pressure.',
    description: 'Useful signal is the gap between WTO MFN baseline and the real paid burden after Section 301, AD/CVD, safeguard actions, origin rules, and exclusions.',
    affected: 'Solar tariffs, EV import-duty markets, lithium battery costs, clean-energy subsidy odds',
  },
  {
    id: 'tariff-eu-ev',
    tab: 'tariffs',
    title: 'EU China EV duty channel',
    source: 'EU trade defence / WTO tariff baseline',
    jurisdiction: 'EU vs China',
    score: 78,
    severity: 'amber',
    status: 'amber',
    action: 'ANTI-SUBSIDY',
    eventCode: 'HS8703',
    metricA: { label: 'Policy', value: 'Countervailing duty', tone: 'amber' },
    metricB: { label: 'Products', value: 'EVs, cells, packs', tone: 'amber' },
    metricC: { label: 'Watch', value: 'Retaliation risk', tone: 'red' },
    impact: 'European OEMs with local production benefit; China-export exposure and battery-pack importers face demand and margin risk.',
    description: 'This is not just a tariff story. It changes where vehicles are assembled, which battery suppliers win share, and whether China responds against EU exporters.',
    affected: 'EU auto margins, China EV exports, battery supply chain, retaliation headline markets',
  },
  {
    id: 'flow-indonesia-nickel',
    tab: 'flows',
    title: 'Indonesia nickel rerouting risk',
    source: 'WITS / UN Comtrade / OECD export restrictions',
    jurisdiction: 'Indonesia -> China / Korea / Japan',
    score: 82,
    severity: 'red',
    status: 'red',
    action: 'SUPPLY SHOCK',
    eventCode: 'NICKEL-26',
    metricA: { label: 'Flow', value: 'Ore -> matte -> precursor', tone: 'red' },
    metricB: { label: 'Policy', value: 'Export ban / processing mandate', tone: 'red' },
    metricC: { label: 'Market', value: 'Battery input inflation', tone: 'amber' },
    impact: 'Miners and refiners gain pricing power; battery, storage, and EV producers lose margin unless they pass through cost.',
    description: 'Trade-flow alerts should flag concentration and sudden YoY drops. Nickel is high value because policy can move the upstream price before downstream earnings react.',
    affected: 'Nickel price, battery cost, EV margin, storage deployment, Indonesia resource nationalism',
  },
  {
    id: 'flow-steel-aluminum',
    tab: 'flows',
    title: 'Steel and aluminum substitution map',
    source: 'UN Comtrade / WTO trade flows',
    jurisdiction: 'US / EU / China / Turkey / India',
    score: 69,
    severity: 'amber',
    status: 'amber',
    action: 'SUBSTITUTION',
    eventCode: 'HS72/76',
    metricA: { label: 'Upstream', value: 'Domestic producers', tone: 'green' },
    metricB: { label: 'Downstream', value: 'Autos, machinery, packaging', tone: 'amber' },
    metricC: { label: 'Signal', value: 'Partner switch', tone: 'amber' },
    impact: 'Protection helps local steel and aluminum producers while raising costs for autos, machinery, construction, and packaging.',
    description: 'The useful view is not only import volume. It is importer dependence, alternative supplier capacity, and whether trade remedies force rerouting.',
    affected: 'Steel prices, industrial margins, auto input costs, machinery and construction exposure',
  },
  {
    id: 'barrier-cbam',
    tab: 'barriers',
    title: 'EU carbon border compliance drag',
    source: 'WTO I-TIP / EU CBAM notices',
    jurisdiction: 'EU import regime',
    score: 74,
    severity: 'amber',
    status: 'amber',
    action: 'COMPLIANCE WALL',
    eventCode: 'CBAM-26',
    metricA: { label: 'Sectors', value: 'Steel, aluminum, cement, fertilizer', tone: 'amber' },
    metricB: { label: 'Cost', value: 'Reporting + carbon price', tone: 'amber' },
    metricC: { label: 'Risk', value: 'Supplier exclusion', tone: 'red' },
    impact: 'Low-carbon exporters gain access premium; high-emission suppliers face documentation cost, price discount, or lost EU demand.',
    description: 'Non-tariff barriers can be more actionable than tariff rates because they affect delivery eligibility, compliance cost, and supplier selection.',
    affected: 'EU steel imports, fertilizer margins, aluminum supply, carbon-credit and industrial policy markets',
  },
  {
    id: 'barrier-food-pharma',
    tab: 'barriers',
    title: 'Food and pharma TBT/SPS delay channel',
    source: 'WTO TBT/SPS notifications / I-TIP',
    jurisdiction: 'EU / US / China / ASEAN',
    score: 63,
    severity: 'amber',
    status: 'watch',
    action: 'PORT DELAY',
    eventCode: 'TBT/SPS',
    metricA: { label: 'Products', value: 'Food, APIs, chemicals', tone: 'amber' },
    metricB: { label: 'Mechanism', value: 'Testing, licensing, inspection', tone: 'amber' },
    metricC: { label: 'Market', value: 'Inventory rebuild', tone: 'green' },
    impact: 'Delays can lift inventories, widen regional price gaps, and create shortage risk without any tariff headline.',
    description: 'Track notification volume, product scope, objective, and affected exporters. The signal is customs delay and compliance cost, not headline rate.',
    affected: 'Food inflation, API shortages, chemicals supply, auto parts delivery risk',
  },
  {
    id: 'revenue-customs-duty',
    tab: 'revenue',
    title: 'US customs-duty receipt spike',
    source: 'US Treasury Monthly Treasury Statement',
    jurisdiction: 'United States',
    score: 71,
    severity: 'amber',
    status: 'watch',
    action: 'PAID BURDEN',
    eventCode: 'MTS-DUTY',
    metricA: { label: 'Signal', value: 'Monthly customs duties', tone: 'amber' },
    metricB: { label: 'Compare', value: 'FYTD vs prior FY', tone: 'amber' },
    metricC: { label: 'Use', value: 'Effective tariff proxy', tone: 'green' },
    impact: 'If duty revenue jumps, tariff burden is showing up in paid imports rather than staying at announcement level.',
    description: 'Revenue is useful because it confirms real payment pressure. Pair it with import-heavy sectors to infer who absorbs the tax.',
    affected: 'Retail margins, industrial importers, CPI pass-through, fiscal revenue, effective-rate estimates',
  },
  {
    id: 'revenue-pass-through',
    tab: 'revenue',
    title: 'Importer pass-through watch',
    source: 'Treasury receipts / company guidance',
    jurisdiction: 'US-listed importers',
    score: 66,
    severity: 'amber',
    status: 'watch',
    action: 'MARGIN TEST',
    eventCode: 'PASS-THRU',
    metricA: { label: 'Absorber', value: 'Supplier / importer / consumer', tone: 'amber' },
    metricB: { label: 'Sector', value: 'Retail, autos, electronics', tone: 'red' },
    metricC: { label: 'Trigger', value: 'Guidance cuts', tone: 'red' },
    impact: 'The same tariff can be bullish or bearish depending on pricing power. Weak pass-through means margin compression.',
    description: 'Customs revenue becomes actionable only when linked to import share, gross margin, inventory cycle, and company guidance language.',
    affected: 'Retail earnings, auto margins, electronics pricing, CPI goods inflation',
  },
  {
    id: 'strategic-graphite-rare-earths',
    tab: 'strategic',
    title: 'China graphite and rare-earth licensing',
    source: 'Official export-control notices / OECD / USGS',
    jurisdiction: 'China -> US / EU / Japan / Korea',
    score: 88,
    severity: 'red',
    status: 'red',
    action: 'EXPORT CONTROL',
    eventCode: 'CRIT-MIN',
    metricA: { label: 'Products', value: 'Graphite, magnets, gallium, germanium', tone: 'red' },
    metricB: { label: 'End use', value: 'EV, defense, chips, wind', tone: 'red' },
    metricC: { label: 'Market', value: 'Binary flow risk', tone: 'red' },
    impact: 'Defense, EV, wind, electronics, and semiconductor supply chains can reprice on export-license headlines alone.',
    description: 'Strategic controls block physical flows without needing a tariff. The key signal is license approval, end-use screening, and alternative stockpile capacity.',
    affected: 'Critical-mineral controls, defense supply chain, battery materials, semiconductor input risk',
  },
  {
    id: 'strategic-sanctions-dual-use',
    tab: 'strategic',
    title: 'Dual-use sanctions and shipping chokepoints',
    source: 'Official sanctions lists / Global Trade Alert',
    jurisdiction: 'US / EU / UK / China / Russia / Middle East',
    score: 79,
    severity: 'red',
    status: 'red',
    action: 'FLOW BLOCK',
    eventCode: 'SANCTION',
    metricA: { label: 'Products', value: 'Energy, chips, aircraft parts', tone: 'red' },
    metricB: { label: 'Mechanism', value: 'Entity list / shipping ban', tone: 'red' },
    metricC: { label: 'Market', value: 'Legal flow vs blocked flow', tone: 'red' },
    impact: 'This is often binary: insured cargo vs stranded cargo, licensed sale vs blocked sale, bankable transaction vs compliance stop.',
    description: 'Sanctions belong in trade policy because they change flow probability even when tariff rates are unchanged.',
    affected: 'Oil flows, shipping insurance, dual-use exports, sanctions markets, aircraft and chip supply',
  },
];

function severityClass(severity: Severity) {
  return `severity-${severity}`;
}

function TradePolicyRadarPanel() {
  const { copy } = useSpecialistCopy('trade-policy-radar');
  const [activeTab, setActiveTab] = useState<TabId>('tariffs');
  const [showHelp, setShowHelp] = useState(false);
  const activeTabMeta = TABS.find((tab) => tab.id === activeTab) || DEFAULT_TAB_META;
  const tabCopy = (tab: typeof DEFAULT_TAB_META) => ({
    label: copy(`tab.${tab.id}.label`, tab.label),
    hint: copy(`tab.${tab.id}.hint`, tab.hint),
  });
  const activeTabCopy = tabCopy(activeTabMeta);
  const activeItems = useMemo(
    () => ALERTS.filter((item) => item.tab === activeTab),
    [activeTab],
  );

  return (
    <Panel
      title={copy('title', 'Trade Policy')}
      badge="LIVE"
      status="live"
      count={activeItems.length}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain trade policy panel')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Trade Policy')}</strong>
          <p>{copy('helpText', 'High-signal watchlist for tariff burden, trade-flow breaks, non-tariff barriers, customs revenue, and strategic export controls.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-trade-policy-radar-panel"
      dataPanelId="trade-policy-radar"
    >
      <div className="wm-trade-terminal">
        <div className="wm-trade-tabs" role="tablist" aria-label={copy('viewsAria', 'Trade policy views')}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={activeTab === tab.id ? 'active' : ''}
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
            >
              {tabCopy(tab).label}
            </button>
          ))}
        </div>

        <div className="wm-trade-section-head">
          <span>{activeTabCopy.label}</span>
          <em>{activeTabCopy.hint}</em>
        </div>

        <div className="wm-trade-alert-list">
          {activeItems.map((item) => (
            <TradeAlertCard item={item} key={item.id} />
          ))}
        </div>
      </div>
    </Panel>
  );
}

function TradeAlertCard({ item }: { item: TradeAlert }) {
  return (
    <article className={`wm-trade-alert-card ${severityClass(item.severity)}`}>
      <div className="wm-trade-alert-head">
        <strong>{item.title}</strong>
        <span className={`wm-trade-dot-status ${severityClass(item.severity)}`} />
        <em>{item.score}/100</em>
        <b>{item.status}</b>
      </div>

      <div className="wm-trade-alert-sub">
        <span>{item.source}</span>
        <i>{item.jurisdiction}</i>
      </div>

      <div className="wm-trade-metric-row">
        <MetricCell metric={item.metricA} />
        <MetricCell metric={item.metricB} />
        <MetricCell metric={item.metricC} />
      </div>

      <div className="wm-trade-action-row">
        <span>{item.action}</span>
        <b>{item.eventCode}</b>
      </div>

      <p className="wm-trade-impact">{item.impact}</p>
      <p className="wm-trade-description">{item.description}</p>
      <div className="wm-trade-affected">{item.affected}</div>
    </article>
  );
}

function MetricCell({ metric }: { metric: Metric }) {
  return (
    <div className={metric.tone ? severityClass(metric.tone) : ''}>
      <span>{metric.label}</span>
      <b>{metric.value}</b>
    </div>
  );
}

const renderers: PanelRenderMap = {
  'trade-policy-radar': {
    render: () => <TradePolicyRadarPanel />,
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'trade-policy-radar',
  title: 'Trade Policy',
  eyebrow: 'global',
  description: 'High-signal trade policy watchlist mapped to tariff burden, flows, barriers, revenue, and strategic controls.',
  defaultEnabled: true,
});
