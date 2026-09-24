import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupNewsItem } from '../types';
import { newsTags } from './newsUtils';
import { coerceSignalTone } from './signalBuilders';

export function MediaWirePanel({
  news,
  matchSignals,
  localMedia,
}: {
  news: WorldCupNewsItem[];
  matchSignals: WorldCupSignalItem[];
  localMedia: WorldCupSignalItem[];
}) {
  const newsSignals: WorldCupSignalItem[] = news.slice(0, 8).map((item) => {
    const tags = newsTags(item);
    const firstTone = tags[0]?.tone;
    return {
      id: `news-wire-${item.id}`,
      source: item.source,
      title: item.title,
      summary: item.summary || 'World Cup desk monitors source, tag, match and market context.',
      age: item.publishedAt,
      tags: tags.map((tag) => ({ label: tag.label, tone: coerceSignalTone(tag.tone) })),
      accent: firstTone ? coerceSignalTone(firstTone) : 'blue',
    };
  });
  return (
    <Panel title="MEDIA / INTEL WIRE" count={news.length + localMedia.length} className="wm-worldcup-panel wm-worldcup-media-wire-panel">
      <div className="wm-worldcup-wire-columns">
        <section>
          <header><strong>GLOBAL</strong><span>{newsSignals.length}</span></header>
          {newsSignals.slice(0, 4).map((item) => <SignalRow item={item} key={item.id} />)}
        </section>
        <section>
          <header><strong>LOCAL / MATCH</strong><span>{localMedia.length + matchSignals.length}</span></header>
          {[...localMedia.slice(0, 3), ...matchSignals.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
        </section>
      </div>
    </Panel>
  );
}
