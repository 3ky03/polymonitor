import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupNewsItem } from '../types';
import { newsTags } from './newsUtils';

export function NewsPanel({ items }: { items: WorldCupNewsItem[] }) {
  return (
    <Panel title="NEWS" count={items.length} className="wm-worldcup-panel wm-worldcup-news-panel">
      <div className="wm-worldcup-feed">
        {items.map((item) => (
          <a className="wm-worldcup-feed-row" href={item.url || '#'} key={item.id} target={item.url === '#' ? undefined : '_blank'} rel="noreferrer">
            <div className="wm-worldcup-feed-meta">
              <span>{item.source}</span>
              {newsTags(item).map((tag) => (
                <b className={`wm-worldcup-feed-tag ${tag.tone}`} key={`${item.id}-${tag.label}`}>{tag.label}</b>
              ))}
            </div>
            <strong>{item.title}</strong>
            <em>{item.summary || 'Monitoring World Cup-linked market context.'}</em>
          </a>
        ))}
      </div>
    </Panel>
  );
}
