import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherMapPayload, RuntimeWeatherQuoteBin } from '@/types';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import {
  bestBookQuoteBin,
  bestQuoteBin,
  bookMidCoverage,
  bookMidPrice,
  bookCoverage,
  marketSourceLabel,
  midCoverage,
  num,
  panelStatus,
  priceLabel,
  selectedWeatherCity,
  statusBadge,
  tempLabel,
  useLiveWeatherQuoteBins,
} from '../weather-detail-utils';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function sourceLabel(value?: string | null) {
  if (value === 'clob-book') return 'CLOB';
  if (value === 'db-latest') return 'LAST';
  if (value === 'gamma-outcome') return 'GAMMA';
  return '--';
}

function bookLabel(value: string | null | undefined, shared: ReturnType<typeof useSpecialistCopy>['shared']) {
  if (!value) return '--';
  if (value === 'not-queried') return '--';
  if (value === 'no-book') return shared('noBook', 'NO BOOK');
  if (value === 'missing-token') return shared('noToken', 'NO TOKEN');
  return String(value).toUpperCase();
}

function quoteState(bin: RuntimeWeatherQuoteBin) {
  if (bookMidPrice(bin) !== null) return 'CLOB MID';
  if (num(bin.bestBidYes) !== null || num(bin.bestAskYes) !== null) return 'CLOB 1-SIDED';
  if (num(bin.midPriceYes) !== null) return sourceLabel(bin.priceSource) === '--' ? 'QUOTED' : sourceLabel(bin.priceSource);
  return 'MISSING';
}

function QuoteRow({ bin, active }: { bin: RuntimeWeatherQuoteBin; active: boolean }) {
  const { shared } = useSpecialistCopy('weather-quote-table');
  const state = quoteState(bin);
  const stateLabel = state === 'QUOTED' ? shared('quoted', 'QUOTED') : state === 'MISSING' ? shared('missing', 'MISSING') : state;
  return (
    <tr className={active ? 'active' : ''}>
      <td>{bin.label || tempLabel(bin.minTemp, bin.unit)}</td>
      <td>{priceLabel(bin.bestBidYes)}</td>
      <td>{priceLabel(bin.bestAskYes)}</td>
      <td>{priceLabel(bin.midPriceYes)}</td>
      <td><span className={`wm-weather-quote-state ${state.toLowerCase().replace(/\s+/g, '-')}`}>{stateLabel}</span></td>
      <td>{bookLabel(bin.bookStatus, shared)}</td>
    </tr>
  );
}

function WeatherQuoteTablePanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy, shared } = useSpecialistCopy('weather-quote-table');
  const city = selectedWeatherCity(payload, selectedCityId);
  const { bins, loading } = useLiveWeatherQuoteBins(city);
  const liveCity = city ? { ...city, bins } : null;
  const topBookBin = bestBookQuoteBin(liveCity);
  const topBin = topBookBin || bestQuoteBin(liveCity);
  const topBookPrice = bookMidPrice(topBookBin);
  const topLabel = topBin?.label || bins[Math.floor(bins.length / 2)]?.label || '--';
  return (
    <Panel
      title={copy('title', 'WEATHER QUOTE TABLE')}
      badge={loading ? 'BOOK' : statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-weather-quote-table-only-panel"
      dataPanelId="weather-quote-table"
    >
      {city ? (
        <section className="wm-weather-quote-table-panel">
          <div className="wm-weather-quote-table-head">
            <div>
              <span>{copy('tableTitle', '{city} Quote Table', { city: city.city || '--' })}</span>
              <strong>{topLabel}</strong>
            </div>
            <b>{priceLabel(topBookPrice)}</b>
          </div>
          <div className="wm-weather-quote-meta">
            <span><i>{shared('book', 'Book')}</i><strong>{bookCoverage(liveCity)}</strong></span>
            <span><i>{shared('bidAskMid', 'Bid/Ask Mid')}</i><strong>{bookMidCoverage(liveCity)}</strong></span>
            <span><i>{shared('last', 'Last')}</i><strong>{midCoverage(liveCity)}</strong></span>
            <span><i>{shared('market', 'Market')}</i><strong>{marketSourceLabel(city)}</strong></span>
            <span><i>{shared('bid', 'Bid')}</i><strong>{priceLabel(topBookBin?.bestBidYes)}</strong></span>
            <span><i>{shared('ask', 'Ask')}</i><strong>{priceLabel(topBookBin?.bestAskYes)}</strong></span>
          </div>
          <div className="wm-weather-quote-table-wrap">
            <table className="wm-weather-quote-table">
              <thead>
                <tr>
                  <th>{shared('bin', 'Bin')}</th>
                  <th>{shared('bid', 'Bid')}</th>
                  <th>{shared('ask', 'Ask')}</th>
                  <th>{shared('lastMid', 'Last/Mid')}</th>
                  <th>{shared('source', 'Source')}</th>
                  <th>{shared('book', 'Book')}</th>
                </tr>
              </thead>
              <tbody>
                {bins.map((bin) => (
                  <QuoteRow key={String(bin.marketSlug || bin.label)} bin={bin} active={String(bin.label || '') === String(topBin?.label || '')} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <div className="wm-weather-detail-empty">{copy('empty', 'Select a city to inspect quote bins.')}</div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-quote-table': {
    render: (ctx) => (
      <WeatherQuoteTablePanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'weather-quote-table',
  title: 'Weather Quote Table',
  eyebrow: 'weather',
  description: 'Selected city temperature market quote bins in a compact table.',
  defaultEnabled: true,
});
