export type WorldCupSignalTone = 'red' | 'gold' | 'blue' | 'purple' | 'gray' | 'green';

export type WorldCupSignalItem = {
  id: string;
  source: string;
  title: string;
  summary: string;
  age: string;
  tags: Array<{ label: string; tone: WorldCupSignalTone }>;
  accent?: WorldCupSignalTone;
};

const HIDDEN_SIGNAL_LABELS = new Set(['SEED', 'RSS', 'PRIMARY', 'LIVE', 'LOCAL DB', 'REMOTE', 'WATCH']);

function cleanSignalAge(age?: string | null) {
  const value = (age || '').trim();
  if (!value) return '';
  if (/(seed|rss|primary|policy|pool|reserve|pending|pre-match|scheduled|local|model|watch|live)$/i.test(value)) return '';
  const hourMatch = value.match(/^(\d+)h(?:\s+ago)?$/i);
  if (hourMatch) return `${hourMatch[1]}小时前`;
  const minuteMatch = value.match(/^(\d+)m(?:\s+ago)?$/i);
  if (minuteMatch) return `${minuteMatch[1]}分钟前`;
  const dayMatch = value.match(/^(\d+)d(?:\s+ago)?$/i);
  if (dayMatch) return `${dayMatch[1]}天前`;
  if (/^(now|live)$/i.test(value)) return '';
  return value;
}

export function cleanSignalSource(source?: string | null) {
  const value = (source || '').trim();
  if (!value) return 'WORLD CUP DESK';
  if (/^(seed|rss|primary|inferred|manual|fallback)$/i.test(value)) return 'WORLD CUP DESK';
  if (/local\s*db/i.test(value)) return 'MARKET DESK';
  if (/remote/i.test(value)) return 'DATA DESK';
  return String(value || 'source').replace(/[-_]/g, ' ').toUpperCase();
}

export function cleanSignalTags(tags: WorldCupSignalItem['tags']) {
  return tags.filter((tag) => {
    const label = String(tag.label || '').trim().toUpperCase();
    return label && !HIDDEN_SIGNAL_LABELS.has(label) && !/(^|\s)(SEED|RSS|PRIMARY|LIVE|WATCH)(\s|$)/i.test(label);
  });
}

export function SignalTags({ tags }: { tags: WorldCupSignalItem['tags'] }) {
  return (
    <>
      {cleanSignalTags(tags).slice(0, 3).map((tag) => (
        <b className={`wm-worldcup-feed-tag ${tag.tone}`} key={`${tag.label}-${tag.tone}`}>{tag.label}</b>
      ))}
    </>
  );
}

export function SignalRow({ item }: { item: WorldCupSignalItem }) {
  const displayAge = cleanSignalAge(item.age);
  return (
    <article className={`wm-worldcup-signal-row ${item.accent || item.tags[0]?.tone || 'gray'}`}>
      <div className="wm-worldcup-feed-meta">
        <span>{cleanSignalSource(item.source)}</span>
        <SignalTags tags={item.tags} />
      </div>
      <strong>{item.title}</strong>
      <em>{item.summary}</em>
      <div className="wm-worldcup-signal-foot">
        {displayAge ? <span>{displayAge}</span> : <span aria-hidden="true" />}
        <button type="button" tabIndex={-1}>文</button>
      </div>
    </article>
  );
}
