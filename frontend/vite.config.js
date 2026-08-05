import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Every /api call is proxied to the gateway, so the client uses same-origin
// relative URLs in dev and in production alike.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Port 8090, not the more conventional 8080: Apache/Tomcat and similar
  // commonly hold 8080, and because this is a proxy target the symptom of a
  // collision is a confusing 404 from the *other* service rather than a
  // connection error. Override with VITE_API_TARGET if 8090 is taken too.
  const target = env.VITE_API_TARGET || 'http://127.0.0.1:8090';

  const proxy = {
    '/api': {
      target,
      changeOrigin: true,
      // SAR's report generation runs 4 LLM calls; Trial Balance's Single-TB
      // pipeline runs ~20 sequential tool calls (several of them their own
      // LLM reasoning steps) and can run considerably longer. One shared,
      // generous timeout for every mode rather than special-casing per path.
      timeout: 30 * 60 * 1000,
      proxyTimeout: 30 * 60 * 1000,
    },
  };

  return {
    plugins: [react()],
    server: { port: 5173, proxy },
    // `vite preview` (serving the production dist/ build) does not inherit
    // server.proxy — it needs its own, or `npm run preview` 404s on every
    // /api call and the production build becomes untestable locally.
    preview: { port: 4173, proxy },
  };
});
