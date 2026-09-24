import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupNewsItem } from '../types';
import { newsTags } from './newsUtils';

export function NewsImpactPanel({ news }: { news: WorldCupNewsItem[] }) {
  const rows = news.slice(0, 10).map((item) => {
    const tags = newsTags(item);
    const market = tags.find((tag) => tag.label === 'MARKET') ? 'market' : tags.find((tag) => tag.label === 'TEAM') ? 'lineup' : tags.find((tag) => tag.label === 'WEATHER') ? 'venue' : 'match';
    return { item, tags, market };
  });
  return (
    <Panel title="NEWS IMPACT" count={rows.length} className="wm-worldcup-panel wm-worldcup-news-impact-panel">
      {rows.length ? (
        <div className="wm-worldcup-impact-list">
          {rows.map(({ item, tags, market }) => (
            <article key={item.id}>
              <div><span>{item.source}</span>{tags.map((tag) => <b className={`wm-worldcup-feed-tag ${tag.tone}`} key={`${item.id}-${tag.label}`}>{tag.label}</b>)}</div>
              <strong>{item.title}</strong>
              <footer><em>{market}</em><span><b>{new Date(item.publishedAt).toLocaleString('en-US', { hour12: false })}</b></span></footer>
            </article>
          ))}
        </div>
      ) : (
        <SourceRequired
          detail="News impact scoring is disabled until a real classifier/model service is connected. The panel will not hash titles into fake scores."
          rows={[{ source: 'News classifier / market reaction model', status: 'required', detail: 'impact score must be computed server-side with provenance' }]}
        />
      )}
    </Panel>
  );
}
