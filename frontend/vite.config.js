import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Every /api call is proxied to the gateway, so the client uses same-origin
// relative URLs in dev and in production alike. In production the proxy is
// nginx (see frontend/nginx/default.conf.template); here it is Vite.
export default defineConfig(({ mode }) => {
  // Read the repo-root .env as well as frontend/.env, so ports and the gateway
  // address live in the same single file the backend and Docker Compose read.
  // The prefix argument is '' rather than 'VITE_' because these are dev-server
  // settings, not values inlined into the client bundle — the app itself reads
  // no import.meta.env, which is what keeps one built image deployable to any
  // environment.
  const env = {
    ...loadEnv(mode, process.cwd() + '/..', ''),
    ...loadEnv(mode, process.cwd(), ''),
  };

  // Ports come from .env (the ARTHA_* 121xx series) so the dev server,
  // `vite preview` and the container stack cannot drift apart. This host runs
  // many other services; 121xx is reserved for Artha.AI end to end. Note the
  // API port is a *proxy target* — the symptom of a collision there is a
  // confusing 404 from the other service rather than a connection error.
  const devPort = Number(env.ARTHA_DEV_PORT || 12103);
  const previewPort = Number(env.ARTHA_PREVIEW_PORT || 12104);
  const backendPort = Number(env.ARTHA_BACKEND_PORT || 12101);

  // VITE_API_TARGET still wins when set, for pointing a local UI at a gateway
  // running somewhere else entirely.
  const target = env.VITE_API_TARGET || `http://127.0.0.1:${backendPort}`;

  // SAR's report generation runs 4 LLM calls; Trial Balance's Single-TB
  // pipeline runs ~20 sequential tool calls (several of them their own LLM
  // reasoning steps) and can run considerably longer. One shared, generous
  // timeout for every mode rather than special-casing per path — and the same
  // value nginx uses in production, so a request that survives dev does not
  // 504 only once deployed.
  const timeout = Number(env.ARTHA_PROXY_TIMEOUT_MS || 30 * 60 * 1000);

  const proxy = {
    '/api': {
      target,
      changeOrigin: true,
      timeout,
      proxyTimeout: timeout,
    },
  };

  return {
    plugins: [react()],
    server: { port: devPort, proxy },
    // `vite preview` (serving the production dist/ build) does not inherit
    // server.proxy — it needs its own, or `npm run preview` 404s on every
    // /api call and the production build becomes untestable locally.
    preview: { port: previewPort, proxy },
  };
});
