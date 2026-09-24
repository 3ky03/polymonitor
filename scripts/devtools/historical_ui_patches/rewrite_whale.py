"""Historical one-off patch: rewrite only whaleTrackerList."""

import re

with open('webpage/src/panels/shared/renderers.tsx', 'r') as f:
    text = f.read()

start_idx = text.find('function whaleTrackerList')
# Assume it goes until the end of the file or tradeSignalList
end_idx = text.find('\n\nfunction tradeSignalList')
if end_idx == -1:
    end_idx = len(text)

if start_idx != -1:
    new_func = """function whaleTrackerList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div style={{ fontFamily: 'monospace', width: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Tabs Header */}
      <div style={{ 
        display: 'flex', 
        gap: '16px', 
        fontSize: '13px', 
        color: '#888', 
        borderBottom: '1px solid rgba(255,255,255,0.1)', 
        padding: '0 12px',
        marginBottom: '4px'
      }}>
        <div style={{ color: '#fff', borderBottom: '2px solid #22c55e', paddingBottom: '8px', marginBottom: '-1px', fontWeight: 'bold' }}>
          Trades
        </div>
        <div style={{ cursor: 'pointer', paddingBottom: '8px' }}>Flow</div>
        <div style={{ cursor: 'pointer', paddingBottom: '8px' }}>Signals</div>
      </div>

      {/* Trades List */}
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {items.map((item, index) => {
           let timeStr = formatRelative(item.timestamp || null);
           // clean up time format to look like '14m ago'
           timeStr = timeStr.replace(' minutes ago', 'm ago').replace(' minutes', 'm ago')
                            .replace(' hours ago', 'h ago').replace(' hours', 'h ago')
                            .replace(' seconds ago', 's ago').replace(' seconds', 's ago');
           if (timeStr.includes('just now')) timeStr = '1m ago';
           if (!timeStr.includes('ago')) timeStr += ' ago'; // safe fallback
           
           const side = String(item.side || 'BUY').toUpperCase();
           const isBuy = side === 'BUY';
           const color = isBuy ? '#22c55e' : '#ef4444';
           const addressFull = item.txHash || item.addresses?.[0]?.address || 'unknown';
           const address = shortHash(addressFull, 5, 0).replace('...', '');
           
           return (
             <div
               key={`${item.txHash || 'trade'}-${index}`}
               style={{ 
                 borderLeft: `3px solid ${color}`, 
                 borderBottom: '1px solid rgba(255,255,255,0.05)',
                 padding: '10px 12px',
                 cursor: 'pointer',
                 display: 'flex',
                 flexDirection: 'column',
                 gap: '8px',
                 background: 'transparent',
                 transition: 'background 0.2s',
               }}
               onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
             >
               {/* Meta Row */}
               <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'rgba(255,255,255,0.5)' }}>
                 <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: color }} />
                 <span style={{ fontFamily: 'monospace' }}>{address}</span>
                 <span style={{ color: 'rgba(255,255,255,0.2)' }}>·</span>
                 <span>{timeStr}</span>
                 <span style={{ color: 'rgba(255,255,255,0.2)' }}>·</span>
                 <strong style={{ color: color }}>{side}</strong>
                 <strong style={{ marginLeft: 'auto', color: '#fff', fontSize: '13px' }}>
                   ${formatCompact(item.notional || 0)}
                 </strong>
               </div>

               {/* Title Row */}
               <strong style={{ 
                 fontSize: '14px', 
                 lineHeight: 1.3, 
                 color: 'rgba(255,255,255,0.9)',
                 fontFamily: 'sans-serif',
                 display: '-webkit-box', 
                 WebkitBoxOrient: 'vertical', 
                 WebkitLineClamp: 2, 
                 overflow: 'hidden' 
               }}>
                 {item.marketTitle || 'Unknown Market'}
               </strong>
             </div>
           );
        })}
      </div>
    </div>
  );
}"""
    text = text[:start_idx] + new_func + text[end_idx:]
    with open('webpage/src/panels/shared/renderers.tsx', 'w') as f:
        f.write(text)
    print("Success")
else:
    print("Failed")
