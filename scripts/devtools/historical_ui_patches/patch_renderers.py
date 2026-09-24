"""Historical one-off patch: early renderer rewrite experiment."""

import re

with open('webpage/src/panels/shared/renderers.tsx', 'r') as f:
    content = f.read()

# Replace alphaSignalList
alpha_pattern = re.compile(
    r'function alphaSignalList.*?^}(?=\n\nfunction tradeSignalList)',
    re.MULTILINE | re.DOTALL
)

new_alpha = """function alphaSignalList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', width: '100%', fontFamily: 'monospace', padding: '12px' }}>
      {items.map((item, index) => {
        const isCluster = (item.addresses?.length || 0) > 1 || String(item.sourceLabel || '').toLowerCase().includes('cluster');
        const icon = isCluster ? '👥' : '🐳';
        const sourceName = item.sourceLabel ? item.sourceLabel.toUpperCase() : (isCluster ? 'CLUSTER' : 'WHALE');
        const bias = signalBias(item);
        const isBull = bias === 'bullish';
        const color = isBull ? '#39ff73' : '#ff6464';
        const triangle = isBull ? '▲' : '▼';
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
          <article key={`${item.title || item.marketTitle || 'signal'}-${index}`} style={{ display: 'flex', flexDirection: 'column', paddingBottom: '16px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '13px' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '15px' }}>{icon}</span>
                <span style={{ color: '#aaa', letterSpacing: '0.05em' }}>{sourceName}</span>
                <span style={{ color: color, fontWeight: 'bold' }}>{triangle} {bias}</span>
              </div>
              <div style={{ color: '#888' }}>{timeStr}</div>
            </div>
            
            <div style={{ display: 'flex', gap: '12px' }}>
              <div style={{ color: '#ff6464', fontSize: '11px', fontWeight: 'bold', background: 'rgba(255,100,100,0.1)', padding: '2px 4px', borderRadius: '4px', height: 'fit-content' }}>STR</div>
              <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
                <p style={{ color: '#eee', fontSize: '14px', lineHeight: '1.4', margin: '0 0 12px 0', fontFamily: 'sans-serif' }}>
                  {item.headline || item.summary || item.title || 'Signal activity detected'}
                </p>
                
                <div style={{ background: 'rgba(57,255,115,0.05)', border: `1px solid rgba(57,255,115,0.2)`, borderRadius: '4px', padding: '8px 12px', marginBottom: '12px', cursor: 'pointer' }} onClick={() => item.marketId && onMarketSelect?.(item.marketId)}>
                  <div style={{ display: 'flex', gap: '8px', color: '#39ff73', fontSize: '13px', fontWeight: 'bold' }}>
                    <span>{triangle}</span>
                    <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.marketTitle || item.title || 'Market signal'}</span>
                  </div>
                </div>
                
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', color: '#aaa', fontFamily: 'monospace' }}>
                  <div style={{ display: 'flex', gap: '16px' }}>
                     <div style={{ display: 'flex', gap: '4px' }}>
                       <span style={{ color: '#eee' }}>${formatCompact(volume)}</span>
                       <span>vol</span>
                     </div>
                     <div style={{ display: 'flex', gap: '4px' }}>
                       <span style={{ color: '#eee' }}>{count}</span>
                       <span>trades</span>
                     </div>
                     <div style={{ display: 'flex', gap: '4px' }}>
                       <span style={{ color: '#eee' }}>{wallets}</span>
                       <span>wallet(s)</span>
                     </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                    <span style={{ color: '#eee' }}>@{prob}</span>
                    <button style={{ background: 'rgba(57,255,115,0.1)', color: '#39ff73', border: '1px solid rgba(57,255,115,0.3)', padding: '6px 12px', borderRadius: '4px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 'bold' }}>
                      {action.side} {action.outcome}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}"""

content = alpha_pattern.sub(new_alpha, content)

# Add whaleTrackerList
new_whale = """
function whaleTrackerList(items: RuntimeTradeSignal[], emptyMessage: string) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', fontSize: '13px', fontFamily: 'monospace' }}>
      <div style={{ display: 'flex', gap: '16px', marginBottom: '12px', fontSize: '12px', color: '#888', fontWeight: 'bold', padding: '0 8px' }}>
        <span style={{ color: '#fff', borderBottom: '1px solid #39ff73', paddingBottom: '4px' }}>Trades</span>
        <span style={{ cursor: 'pointer' }}>Flow</span>
        <span style={{ cursor: 'pointer' }}>Signals</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {items.map((item, index) => {
           let timeStr = formatRelative(item.timestamp || null);
           timeStr = timeStr.replace(' minutes ago', 'm').replace(' minutes', 'm').replace(' hours ago', 'h').replace(' hours', 'h').replace(' seconds ago', 's').replace(' seconds', 's');
           if (timeStr.includes('just now')) timeStr = '1m';
           
           const side = String(item.side || 'BUY').toUpperCase();
           const isBuy = side === 'BUY';
           const color = isBuy ? '#39ff73' : '#ff6464';
           const addressFull = item.txHash || item.addresses?.[0]?.address || 'unknown';
           const address = shortHash(addressFull, 5, 0).replace('...', '');
           return (
             <div key={`${item.txHash || 'trade'}-${index}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px', borderRadius: '4px', background: 'rgba(255,255,255,0.02)' }}>
               <span style={{ color: '#aaa', width: '35px' }}>{timeStr}</span>
               <span style={{ color: '#888', width: '65px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{address}</span>
               <span style={{ color: '#eee', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', margin: '0 12px' }}>{item.marketTitle || 'Unknown Market'}</span>
               <span style={{ color: color, fontWeight: 'bold', width: '40px', textAlign: 'right' }}>{side}</span>
               <span style={{ color: '#fff', width: '55px', textAlign: 'right' }}>${formatCompact(item.notional || 0)}</span>
             </div>
           );
        })}
      </div>
    </div>
  );
}

function tradeSignalList"""

content = content.replace('function tradeSignalList', new_whale)

# Export the new function
content = content.replace('alphaSignalList,\n  tradeSignalList,', 'alphaSignalList,\n  tradeSignalList,\n  whaleTrackerList,')

with open('webpage/src/panels/shared/renderers.tsx', 'w') as f:
    f.write(content)
