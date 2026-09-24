"""Historical one-off patch: rewrite only alphaSignalList."""

with open('webpage/src/panels/shared/renderers.tsx', 'r') as f:
    text = f.read()

start_idx = text.find('function alphaSignalList')
end_idx = text.find('\n\nfunction whaleTrackerList')

if start_idx != -1 and end_idx != -1:
    new_func = """function alphaSignalList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div style={{ fontFamily: 'monospace', width: '100%' }}>
      {items.map((item, index) => {
        const isCluster = (item.addresses?.length || 0) > 1 || String(item.sourceLabel || '').toLowerCase().includes('cluster');
        const icon = isCluster ? '👥' : '🐳';
        const sourceName = item.sourceLabel ? item.sourceLabel.toUpperCase() : (isCluster ? 'CLUSTER' : 'WHALE');
        const bias = signalBias(item);
        const isBull = bias === 'bullish';
        
        // Exact PolyWorld colors
        const dirColor = isBull ? '#22c55e' : '#ef4444';
        const dirArrow = isBull ? '▲' : '▼';
        
        // Assume all signals are 'STR' for now as in the original mock if not provided
        const sColor = '#ff4444';
        const sBg = 'rgba(255,68,68,0.12)';
        const strengthLabel = 'STR';
        
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
          <div 
            key={`${item.title || item.marketTitle || 'signal'}-${index}`}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '6px',
              padding: '6px',
              borderBottom: '1px solid rgba(255,255,255,0.05)',
              cursor: 'pointer',
              background: 'rgba(255,68,68,0.03)'
            }}
            onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
          >
            {/* Left Column: Icon & Strength Badge */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0, width: '28px', paddingTop: '2px' }}>
              <span style={{ fontSize: '13px', lineHeight: 1 }}>{icon}</span>
              <span style={{
                fontSize: '8px',
                fontWeight: 'bold',
                borderRadius: '2px',
                padding: '0 2px',
                marginTop: '2px',
                lineHeight: '14px',
                background: sBg,
                color: sColor
              }}>
                {strengthLabel}
              </span>
            </div>

            {/* Right Column */}
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '2px' }}>
              
              {/* Row 1: Header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '2px' }}>
                <span style={{ fontSize: '10px', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'rgba(255,255,255,0.5)' }}>
                  {sourceName}
                </span>
                <span style={{ fontSize: '10px', fontWeight: 'bold', color: dirColor }}>
                  {dirArrow} {bias.toUpperCase()}
                </span>
                <span style={{ fontSize: '10px', color: 'rgba(255,255,255,0.3)', marginLeft: 'auto', flexShrink: 0 }}>
                  {timeStr}
                </span>
              </div>

              {/* Row 2: Summary */}
              <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.85)', lineHeight: 1.2, display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 3, overflow: 'hidden' }}>
                {item.headline || item.summary || item.title || 'Signal activity detected'}
              </div>

              {/* Row 3: Market/Outcome Box */}
              {(item.marketTitle || (action.outcome && action.outcome !== 'Yes' && action.outcome !== 'No')) && (
                <div style={{
                  fontSize: '10px',
                  fontWeight: 'bold',
                  marginTop: '2px',
                  padding: '2px 4px',
                  display: 'inline-block',
                  borderRadius: '2px',
                  width: 'fit-content',
                  background: isBull ? 'rgba(34,197,94,0.1)' : 'rgba(255,68,68,0.1)',
                  color: dirColor,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: '100%'
                }}>
                  {dirArrow} {item.marketTitle || 'Market'} {action.outcome && action.outcome !== 'Yes' && action.outcome !== 'No' ? ` · ${action.outcome}` : ''}
                </div>
              )}

              {/* Row 4: Metrics & Action */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '2px', flexWrap: 'wrap' }}>
                {volume > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)' }}>${formatCompact(volume)}</span>
                    <span style={{ marginTop: '-2px' }}>vol</span>
                  </div>
                )}
                {count > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)' }}>{count}</span>
                    <span style={{ marginTop: '-2px' }}>trades</span>
                  </div>
                )}
                {wallets > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)' }}>{wallets}</span>
                    <span style={{ marginTop: '-2px' }}>wallet(s)</span>
                  </div>
                )}
                
                <span style={{ fontSize: '10px', color: 'rgba(255,255,255,0.5)', alignSelf: 'center', margin: 'auto 0' }}>
                  @{prob}
                </span>

                <button style={{
                  marginLeft: 'auto',
                  fontSize: '9px',
                  fontWeight: 'bold',
                  padding: '4px 6px',
                  borderRadius: '2px',
                  cursor: 'pointer',
                  border: 'none',
                  whiteSpace: 'nowrap',
                  background: isBull ? 'rgba(34,197,94,0.15)' : 'rgba(255,68,68,0.15)',
                  color: dirColor
                }}>
                  {action.side === 'buy' ? 'Buy' : 'Sell'} {action.outcome || (isBull ? 'YES' : 'NO')}
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}"""
    text = text[:start_idx] + new_func + text[end_idx:]
    with open('webpage/src/panels/shared/renderers.tsx', 'w') as f:
        f.write(text)
    print("Success")
else:
    print("Failed")
