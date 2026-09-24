import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';

function cleanPanelBadge(label: string) {
  if (!label) return '';
  if (/(seed|rss|primary|local db|remote|watch|pending|scheduled|live|实时)/i.test(label)) return '';
  return label;
}

export function SignalFeedPanel({
  title,
  badge,
  count,
  items,
  className,
}: {
  title: string;
  badge: string;
  count?: number;
  items: WorldCupSignalItem[];
  className: string;
}) {
  return (
    <Panel title={title} badge={cleanPanelBadge(badge) || undefined} count={count ?? items.length} className={`wm-worldcup-panel ${className}`}>
      <div className="wm-worldcup-signal-list">
        {items.map((item) => <SignalRow item={item} key={item.id} />)}
      </div>
    </Panel>
  );
}
