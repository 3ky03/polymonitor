import { useEffect, useMemo, useRef } from 'preact/hooks';
import type { Trade, TradeFilter } from '../types';
import { fmtCurrency, fmtPrice, statusClass } from '../utils/formatters';

type TradesTableTabProps = {
  trades: Trade[];
  filters: Set<TradeFilter>;
  selectedTradeId: string | null;
  onToggleFilter: (filter: TradeFilter) => void;
  onSelectTrade: (tradeId: string) => void;
};

const FILTERS: Array<[TradeFilter, string]> = [
  ['profitable', 'Profitable only'],
  ['losing', 'Losing only'],
  ['yes', 'YES only'],
  ['no', 'NO only'],
  ['longHolding', 'Long holding'],
  ['shortHolding', 'Short holding'],
];

export function TradesTableTab({ trades, filters, selectedTradeId, onToggleFilter, onSelectTrade }: TradesTableTabProps) {
  const selectedTrade = useMemo(() => (
    trades.find((trade) => trade.id === selectedTradeId) || trades[0] || null
  ), [selectedTradeId, trades]);
  const selectedIndex = useMemo(() => (
    selectedTrade ? trades.findIndex((trade) => trade.id === selectedTrade.id) : -1
  ), [selectedTrade, trades]);
  const selectedRowRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    selectedRowRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [selectedTradeId]);

  const selectOffset = (offset: number) => {
    if (!trades.length) return;
    const base = selectedIndex >= 0 ? selectedIndex : 0;
    const nextIndex = Math.min(trades.length - 1, Math.max(0, base + offset));
    const nextTrade = trades[nextIndex];
    if (nextTrade) onSelectTrade(nextTrade.id);
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (!trades.length) return;
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      event.preventDefault();
      selectOffset(-1);
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      event.preventDefault();
      selectOffset(1);
    }
    if (event.key === 'Home') {
      event.preventDefault();
      const firstTrade = trades[0];
      if (firstTrade) onSelectTrade(firstTrade.id);
    }
    if (event.key === 'End') {
      event.preventDefault();
      const lastTrade = trades[trades.length - 1];
      if (lastTrade) onSelectTrade(lastTrade.id);
    }
  };

  return (
    <div
      className="qtv-table-wrap qtv-trades-table-wrap"
      tabIndex={0}
      role="region"
      aria-label="Backtest trades. Use arrow keys to move the focused trade."
      onKeyDown={handleKeyDown}
    >
      <div className="qtv-filter-strip">
        {FILTERS.map(([id, label]) => (
          <button key={id} className={filters.has(id) ? 'active' : ''} type="button" onClick={() => onToggleFilter(id)}>{label}</button>
        ))}
        <span>{trades.length} trades</span>
      </div>
      <div className="qtv-trades-focus-strip">
        <div>
          <span>Selected trade</span>
          <strong>{selectedTrade ? `${selectedTrade.id} · ${selectedTrade.outcome}` : 'No trade selected'}</strong>
        </div>
        <div>
          <span>Entry block</span>
          <strong>{selectedTrade?.entryX ? selectedTrade.entryX.toLocaleString('en-US') : selectedTrade?.entryTime || '-'}</strong>
        </div>
        <div>
          <span>Exit block</span>
          <strong>{selectedTrade?.exitX ? selectedTrade.exitX.toLocaleString('en-US') : selectedTrade?.exitTime || '-'}</strong>
        </div>
        <div>
          <span>PnL / holding</span>
          <strong className={selectedTrade && selectedTrade.pnl >= 0 ? 'positive' : 'negative'}>
            {selectedTrade ? `${selectedTrade.pnl >= 0 ? '+' : ''}${selectedTrade.pnl.toFixed(2)} · ${selectedTrade.holdingBars} bars` : '-'}
          </strong>
        </div>
        <div className="qtv-trades-focus-actions">
          <button type="button" disabled={selectedIndex <= 0} onClick={() => selectOffset(-1)}>Prev</button>
          <button type="button" disabled={!selectedTrade} onClick={() => selectedTrade && onSelectTrade(selectedTrade.id)}>Focus chart</button>
          <button type="button" disabled={selectedIndex < 0 || selectedIndex >= trades.length - 1} onClick={() => selectOffset(1)}>Next</button>
        </div>
      </div>
      <table className="qtv-table">
        <thead>
          <tr><th>Trade #</th><th>Entry Block</th><th>Exit Block</th><th>Market</th><th>Outcome</th><th>Side</th><th>Entry</th><th>Exit</th><th>Size</th><th>Notional</th><th>PnL</th><th>PnL %</th><th>Holding</th><th>Exit Reason</th></tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr
              key={trade.id}
              ref={selectedTradeId === trade.id ? selectedRowRef : null}
              className={selectedTradeId === trade.id ? 'selected' : ''}
              title="Select and focus chart on this trade"
              onClick={() => onSelectTrade(trade.id)}
            >
              <td>{trade.id}</td>
              <td>{trade.entryX ? trade.entryX.toLocaleString('en-US') : trade.entryTime}</td>
              <td>{trade.exitX ? trade.exitX.toLocaleString('en-US') : trade.exitTime}</td>
              <td>{trade.market}</td>
              <td>{trade.outcome}</td>
              <td>{trade.side}</td>
              <td>{fmtPrice(trade.entryPrice)}</td>
              <td>{fmtPrice(trade.exitPrice)}</td>
              <td>{trade.size}</td>
              <td>{fmtCurrency(trade.notional)}</td>
              <td className={statusClass(trade.pnl)}>{fmtCurrency(trade.pnl)}</td>
              <td className={statusClass(trade.pnlPct)}>{trade.pnlPct.toFixed(2)}%</td>
              <td>{trade.holdingTime}</td>
              <td>{trade.exitReason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
