"""Historical one-off patch: remove an unused helper from renderers.tsx."""

import re
with open('webpage/src/panels/shared/renderers.tsx', 'r') as f:
    text = f.read()
text = re.sub(r'function signalMetric.*?}\n', '', text, flags=re.DOTALL)
with open('webpage/src/panels/shared/renderers.tsx', 'w') as f:
    f.write(text)
