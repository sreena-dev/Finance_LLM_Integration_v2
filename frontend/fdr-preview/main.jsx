import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import FdrAnalysis from '../src/components/financial-diagnostic-report/FdrAnalysis.jsx';

// The component is fully self-contained (its own scoped styles + mocked data),
// so the preview needs no global stylesheet — that keeps it honest about being
// drop-in. A base reset for the page shell only:
document.body.style.margin = '0';
document.documentElement.style.height = '100%';
document.body.style.height = '100%';

createRoot(document.getElementById('fdr-preview-root')).render(
  <StrictMode>
    <FdrAnalysis />
  </StrictMode>
);
