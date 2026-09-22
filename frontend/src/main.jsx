import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { AuthProvider } from './auth/AuthContext';
import AuthGate from './auth/AuthGate';
// Self-hosted (bundled by Vite): nothing reaches a font CDN, so this works on
// the same air-gapped hosts the ingestion service is built for. Latin covers the
// UI and figures; the Devanagari subset covers Hindi labels.
import '@fontsource/source-serif-4/400.css';
import '@fontsource/source-serif-4/600.css';
import '@fontsource/source-serif-4/700.css';
import '@fontsource/noto-sans/400.css';
import '@fontsource/noto-sans/500.css';
import '@fontsource/noto-sans/600.css';
import '@fontsource/noto-sans/700.css';
import '@fontsource/noto-sans/devanagari-400.css';
import '@fontsource/noto-sans/devanagari-600.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import './styles/index.css';

// The gate wraps <App/> rather than living inside it: App's boot effect calls
// /api/modes the moment it mounts, and that route now needs a token. Gating here
// means App is never mounted signed-out, so its boot logic is unchanged.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate>
        <App />
      </AuthGate>
    </AuthProvider>
  </StrictMode>
);
