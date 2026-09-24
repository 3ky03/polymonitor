import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeJin10Item } from '@/types';
import type { PanelRenderMap } from './types';
import { emptyState } from './shared/renderers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type Jin10Filter = 'impact' | 'latest' | 'vip';
type Jin10Tag = {
  label: string;
  tone: string;
};

function filterItems(items: RuntimeJin10Item[], filter: Jin10Filter) {
  const sorted = [...items].sort(
    (left, right) =>
      new Date(right.timestamp || 0).getTime() - new Date(left.timestamp || 0).getTime(),
  );
  if (filter === 'vip') return sorted.filter((item) => item.locked);
  if (filter === 'impact') {
    return sorted.filter((item) => item.important || (item.assetHints || []).length > 0 || !item.locked);
  }
  return sorted;
}

function cardTopic(item: RuntimeJin10Item) {
  return item.source || 'Jin10';
}

function cardMode(item: RuntimeJin10Item) {
  if (item.locked) return 'vip';
  if (item.important) return 'impact';
  return 'live';
}

function cardMarker(item: RuntimeJin10Item) {
  if (item.locked) return 'VIP';
  return item.important ? 'HOT' : 'LIVE';
}

function tagTone(label: string) {
  const normalized = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  if (['hot', 'live', 'vip'].includes(normalized)) return normalized;
  if (['oil', 'energy'].includes(normalized)) return 'energy';
  if (['gold', 'metals', 'commodity'].includes(normalized)) return 'metals';
  if (['hk', 'china'].includes(normalized)) return 'china';
  if (['fx', 'usd', 'cny'].includes(normalized)) return 'fx';
  if (['crypto', 'btc', 'eth'].includes(normalized)) return 'crypto';
  if (['equity', 'stock'].includes(normalized)) return 'equity';
  if (['economic', 'macro'].includes(normalized)) return 'economic';
  if (['tech', 'ai'].includes(normalized)) return 'tech';
  if (['policy', 'central-bank'].includes(normalized)) return 'policy';
  if (['credit', 'bond'].includes(normalized)) return 'credit';
  return 'default';
}

function addTag(tags: Jin10Tag[], label: string) {
  const clean = label.trim();
  if (!clean) return;
  if (tags.some((tag) => tag.label.toLowerCase() === clean.toLowerCase())) return;
  tags.push({ label: clean, tone: tagTone(clean) });
}

function inferJin10Tags(item: RuntimeJin10Item) {
  const tags: Jin10Tag[] = [];
  addTag(tags, cardMarker(item));
  const text = [item.headline, item.summary, ...(item.assetHints || [])].filter(Boolean).join(' ');
  if (/原油|油价|石油|布伦特|brent|wti|opec|欧佩克|天然气|能源|汽油|柴油|eia|库存/i.test(text)) addTag(tags, 'ENERGY');
  if (/黄金|白银|铜|贵金属|金价|有色|铝|镍|锂|铁矿/i.test(text)) addTag(tags, 'METALS');
  if (/中国|a股|港股|恒指|人民币|央行|上交所|深交所|香港|沪|深|中概/i.test(text)) addTag(tags, 'CHINA');
  if (/美元|人民币|日元|欧元|英镑|汇率|外汇|dxy|usd|cny/i.test(text)) addTag(tags, 'FX');
  if (/比特币|以太坊|加密|btc|eth|sol|etf/i.test(text)) addTag(tags, 'CRYPTO');
  if (/美股|纳指|标普|道指|股票|股市|公司|财报|恒指|港股/i.test(text)) addTag(tags, 'EQUITY');
  if (/cpi|ppi|gdp|pmi|非农|就业|通胀|利率|美联储|国债|债券|收益率|财政|关税|贸易/i.test(text)) addTag(tags, 'ECONOMIC');
  if (/ai|人工智能|算力|芯片|半导体|英伟达|nvidia|openai|数据中心|云|科技/i.test(text)) addTag(tags, 'TECH');
  if (/央行|监管|证监会|财政部|商务部|白宫|国务院|制裁|政策|关税|立法|法院|政府/i.test(text)) addTag(tags, 'POLICY');
  if (/债务|债券|美债|评级|违约|融资|信贷|贷款|收益率/i.test(text)) addTag(tags, 'CREDIT');
  return tags.slice(0, 3);
}

function topicHints(item: RuntimeJin10Item) {
  const text = [item.headline, item.summary].filter(Boolean).join(' ');
  const hints = [...(item.assetHints || [])];
  [
    ['原油', /原油|wti|brent|布伦特|opec|欧佩克/i],
    ['黄金', /黄金|金价|贵金属/i],
    ['港股', /港股|恒指|香港/i],
    ['美联储', /美联储|fed|fomc|鲍威尔/i],
    ['美元', /美元|dxy|usd|外汇/i],
    ['AI', /ai|人工智能|算力|芯片|半导体|英伟达|nvidia/i],
    ['债券', /债券|美债|收益率|评级|违约/i],
    ['加密', /比特币|以太坊|crypto|btc|eth/i],
  ].forEach(([label, pattern]) => {
    if ((pattern as RegExp).test(text) && !hints.includes(label as string)) hints.push(label as string);
  });
  return hints
    .map((hint) => hint.trim())
    .filter(Boolean)
    .slice(0, 3);
}

function splitFlashText(item: RuntimeJin10Item) {
  const headline = item.headline || 'Jin10 update';
  if (item.summary?.trim()) return { title: headline, summary: item.summary.trim() };
  if (headline.length < 58) return { title: headline, summary: '' };
  const sentenceBreak = headline.search(/[。；;]\s*/);
  const bracketBreak = headline.indexOf('】');
  const splitAt = bracketBreak > 8 && bracketBreak < 42
    ? bracketBreak + 1
    : sentenceBreak > 18 && sentenceBreak < 72
      ? sentenceBreak + 1
      : 46;
  return {
    title: `${headline.slice(0, splitAt).trim()}${splitAt === 46 ? '...' : ''}`,
    summary: headline.slice(splitAt).trim(),
  };
}

function jin10CardList(items: RuntimeJin10Item[], copy: ReturnType<typeof useSpecialistCopy>['copy'], formatRelativeTime: ReturnType<typeof useSpecialistCopy>['formatRelativeTime']) {
  if (!items.length) return emptyState(copy('empty', 'No Jin10 panel data loaded yet.'));
  return (
    <div className="wm-jin10-market-list">
      {items.map((item) => {
        const tags = inferJin10Tags(item);
        const hints = topicHints(item);
        const flashText = splitFlashText(item);
        return (
          <a
            className={`wm-jin10-card is-${cardMode(item)}`}
            href={item.url || '#'}
            target="_blank"
            rel="noreferrer"
            key={item.id}
            title={item.headline || copy('flash', 'Jin10 flash')}
          >
            <div className="wm-jin10-card-head">
              <div className="wm-jin10-card-meta">
                <span className="wm-jin10-card-dot" />
                <span>{cardTopic(item)}</span>
                <span>·</span>
                <span>{formatRelativeTime(item.timestamp || '')}</span>
              </div>
              <div className="wm-jin10-card-tags" aria-label={copy('labelsAria', 'Jin10 labels')}>
                {tags.map((tag) => (
                  <span className={`wm-jin10-tag ${tag.tone}`} key={`${item.id}-${tag.label}`}>{tag.label}</span>
                ))}
              </div>
            </div>
            <strong className="wm-jin10-card-title">{flashText.title}</strong>
            {flashText.summary ? <div className="wm-jin10-card-summary">{flashText.summary}</div> : null}
            {hints.length ? (
              <div className="wm-jin10-card-topics">
                {hints.map((hint) => <span key={`${item.id}-${hint}`}>#{hint}</span>)}
              </div>
            ) : null}
          </a>
        );
      })}
    </div>
  );
}

function Jin10FlashPanel({ items }: { items: RuntimeJin10Item[] }) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('jin10-flash');
  const [filter, setFilter] = useState<Jin10Filter>('impact');
  const visibleItems = useMemo(() => filterItems(items, filter), [items, filter]);

  return (
    <Panel
      title={copy('title', 'JIN10')}
      badge="LIVE"
      status="live"
      count={visibleItems.length}
      className="wm-market-panel wm-jin10-panel"
      controls={(
        <select
          className="wm-market-sort wm-jin10-sort"
          value={filter}
          onInput={(event) => setFilter(event.currentTarget.value as Jin10Filter)}
          aria-label={copy('filterAria', 'Filter Jin10 feed')}
        >
          <option value="impact">{shared('impact', 'Impact')}</option>
          <option value="latest">{shared('latest', 'Latest')}</option>
          <option value="vip">VIP</option>
        </select>
      )}
    >
      {jin10CardList(visibleItems, copy, formatRelativeTime)}
    </Panel>
  );
}

export const jin10PanelRenderers: PanelRenderMap = {
  'jin10-flash': {
    render: (ctx) => <Jin10FlashPanel items={ctx.jin10?.items || []} />,
  },
};
