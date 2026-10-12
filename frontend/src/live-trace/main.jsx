import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import TracePage from './TracePage';

// Standalone entry for the live run trace (frontend/trace/index.html). It is a separate page from
// the app, so it never runs the app's boot logic; it reuses the app's stored session token.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <TracePage />
  </StrictMode>
);
