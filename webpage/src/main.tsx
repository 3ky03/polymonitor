import { render } from 'preact';
import { App } from './App';
import { LocaleProvider } from '@/services/i18n';
import { registerPwa } from '@/services/pwa';
import './styles/fonts.css';
import './styles/base-layer.css';
import './styles/panel-layout-stability.css';

registerPwa();
render(<LocaleProvider><App /></LocaleProvider>, document.getElementById('app')!);
