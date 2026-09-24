"""Historical one-off patch: PolyWorld-style renderer rewrite experiment."""

import re

with open('webpage/src/panels/shared/renderers.tsx', 'r') as f:
    content = f.read()

# Replace alphaSignalList
alpha_pattern = re.compile(
    r'function alphaSignalList.*?^}(?=\n\nfunction whaleTrackerList)',
    re.MULTILINE | re.DOTALL
)

new_alpha = """function alphaSignalList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-poly-market-list">
      {items.map((item, index) => {
        const isCluster = (item.addresses?.length || 0) > 1 || String(item.sourceLabel || '').toLowerCase().includes('cluster');
        const icon = isCluster ? '👥' : '🐳';
        const sourceName = item.sourceLabel ? item.sourceLabel.toUpperCase() : (isCluster ? 'CLUSTER' : 'WHALE');
        const bias = signalBias(item);
        const isBull = bias === 'bullish';
        const color = isBull ? '#22c55e' : '#ef4444'; // Using Tailwind green-500 / red-500 equivalent used in markets
        const action = signalAction(item);
        
        let timeStr = formatRelative(item.timestamp || null);
        timeStr = timeStr.replace(' minutes ago', 'm').replace(' minutes', 'm').replace(' hours ago', 'h').replace(' hours', 'h').replace(' seconds ago', 's').replace(' seconds', 's');
        if (timeStr.includes('just now')) timeStr = '1m';

        const metrics = item.metrics || null;
        const volume = metrics?.totalNotional || item.notional || 0;
        const count = metrics?.tradeCount || 1;
        const wallets = metrics?.accountCount || item.addresses?.length || 1;
        const prob = formatPercent(metrics?.currentProbability || item.price || 0);

        return (
          <button
            key={`${item.title || item.marketTitle || 'signal'}-${index}`}
            type="button"
            className="wm-poly-market-card"
            style={{ borderLeftColor: color }}
            onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
            disabled={!item.marketId}
          >
            <div className="wm-poly-market-card-main">
              <div className="wm-poly-market-meta">
                <span className="wm-poly-market-dot" style={{ backgroundColor: color }} />
                <span>{icon} {sourceName}</span>
                <span>·</span>
                <span style={{ color }}>{bias.toUpperCase()}</span>
                <span>·</span>
                <span>{timeStr}</span>
              </div>
              <strong className="wm-poly-market-title" style={{ display: 'flex', gap: '6px', alignItems: 'flex-start' }}>
                <span style={{ color: '#ef4444', fontSize: '9px', background: 'rgba(239,68,68,0.15)', padding: '1px 3px', borderRadius: '3px', marginTop: '3px' }}>STR</span>
                <span>{item.headline || item.summary || item.title || 'Signal activity detected'}</span>
              </strong>
              
              {/* Fake market strip */}
              <div style={{ color: '#aaa', fontSize: '11px', fontFamily: 'monospace', marginBottom: '8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                 ↳ {item.marketTitle || 'Market signal'}
              </div>

              <div className="wm-poly-market-bottom">
                <span className="wm-poly-market-prob">{prob}</span>
                <span className="wm-poly-market-outcome">{action.side.toUpperCase()} {action.outcome.toUpperCase()}</span>
                <span className="wm-poly-market-change" style={{ color: '#22c55e' }}>{volume > 0 ? `vol $${formatCompact(volume)}` : ''}</span>
                <span className="wm-poly-market-trades">{count} tx · {wallets} wallet(s)</span>
              </div>
            </div>
            {item.marketId ? <span className="wm-poly-market-star" aria-hidden="true">☆</span> : null}
          </button>
        );
      })}
    </div>
  );
}"""

content = alpha_pattern.sub(new_alpha, content)

# Replace whaleTrackerList
whale_pattern = re.compile(
    r'function whaleTrackerList.*?^}(?=\n\nfunction tradeSignalList)',
    re.MULTILINE | re.DOTALL
)

new_whale = """function whaleTrackerList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-poly-market-list">
      <div style={{ display: 'flex', gap: '16px', margin: '4px 12px 8px', fontSize: '12px', color: '#888', fontWeight: 'bold', fontFamily: 'monospace' }}>
        <span style={{ color: '#fff', borderBottom: '1px solid #22c55e', paddingBottom: '4px' }}>Trades</span>
        <span style={{ cursor: 'pointer' }}>Flow</span>
        <span style={{ cursor: 'pointer' }}>Signals</span>
      </div>
      {items.map((item, index) => {
         let timeStr = formatRelative(item.timestamp || null);
         timeStr = timeStr.replace(' minutes ago', 'm').replace(' minutes', 'm').replace(' hours ago', 'h').replace(' hours', 'h').replace(' seconds ago', 's').replace(' seconds', 's');
         if (timeStr.includes('just now')) timeStr = '1m';
         
         const side = String(item.side || 'BUY').toUpperCase();
         const isBuy = side === 'BUY';
         const color = isBuy ? '#22c55e' : '#ef4444';
         const addressFull = item.txHash || item.addresses?.[0]?.address || 'unknown';
         const address = shortHash(addressFull, 5, 0).replace('...', '');
         
         return (
           <button
             key={`${item.txHash || 'trade'}-${index}`}
             type="button"
             className="wm-poly-market-card"
             style={{ borderLeftColor: color, paddingTop: '10px', paddingBottom: '10px' }}
             onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
             disabled={!item.marketId}
           >
             <div className="wm-poly-market-card-main">
               <div className="wm-poly-market-meta">
                 <span className="wm-poly-market-dot" style={{ backgroundColor: color }} />
                 <span>{address}</span>
                 <span>·</span>
                 <span>{timeStr}</span>
                 <span>·</span>
                 <strong style={{ color }}>{side}</strong>
                 <strong style={{ marginLeft: 'auto', color: '#fff', fontSize: '12px' }}>${formatCompact(item.notional || 0)}</strong>
               </div>
               <strong className="wm-poly-market-title" style={{ fontSize: '13px', marginTop: '6px' }}>
                 {item.marketTitle || 'Unknown Market'}
               </strong>
             </div>
           </button>
         );
      })}
    </div>
  );
}"""

content = whale_pattern.sub(new_whale, content)

with open('webpage/src/panels/shared/renderers.tsx', 'w') as f:
    f.write(content)
