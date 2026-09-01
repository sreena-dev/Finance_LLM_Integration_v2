import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { AuthProvider } from './auth/AuthContext';
import AuthGate from './auth/AuthGate';
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
