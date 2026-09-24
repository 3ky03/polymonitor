import type { PerformanceRow, PerformanceSortKey, SortDirection } from '../types';

type PerformanceSummaryTabProps = {
  rows: PerformanceRow[];
  search: string;
  sortKey: PerformanceSortKey;
  sortDirection: SortDirection;
  onSearchChange: (value: string) => void;
  onSortChange: (key: PerformanceSortKey) => void;
};

const COLUMN_LABELS: Array<[PerformanceSortKey, string]> = [
  ['metric', 'Metric'],
  ['all', 'All Trades'],
  ['long', 'Long / YES'],
  ['short', 'Short / NO'],
  ['description', 'Description'],
];

export function PerformanceSummaryTab({
  rows,
  search,
  sortKey,
  sortDirection,
  onSearchChange,
  onSortChange,
}: PerformanceSummaryTabProps) {
  return (
    <div className="qtv-table-wrap">
      <div className="qtv-table-controls">
        <input value={search} onInput={(event) => onSearchChange(event.currentTarget.value)} placeholder="Search metric" />
        <span>{rows.length} metrics</span>
      </div>
      <table className="qtv-table">
        <thead>
          <tr>
            {COLUMN_LABELS.map(([key, label]) => (
              <th key={key}>
                <button type="button" onClick={() => onSortChange(key)}>
                  {label}{sortKey === key ? ` ${sortDirection === 'asc' ? '↑' : '↓'}` : ''}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.metric}>
              <td>{row.metric}</td>
              <td>{row.all}</td>
              <td>{row.long}</td>
              <td>{row.short}</td>
              <td>{row.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
