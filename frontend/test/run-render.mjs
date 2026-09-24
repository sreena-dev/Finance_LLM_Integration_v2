/**
 * Bundle and run the render harness.
 *
 * esbuild rather than a test runner because neither vitest nor jest is a
 * dependency here and this needs no watch mode, no assertions library and no
 * config — it renders components and reports exceptions. CSS is loaded as text
 * so the imports resolve without a browser.
 */
import { build } from 'esbuild';
import { pathToFileURL } from 'node:url';
import { rm } from 'node:fs/promises';
import path from 'node:path';

const out = path.resolve('test/.render-bundle.mjs');

await build({
  entryPoints: ['test/render.jsx'],
  bundle: true,
  format: 'esm',
  platform: 'node',
  outfile: out,
  jsx: 'automatic',
  loader: { '.css': 'text', '.svg': 'text' },
  external: ['react', 'react-dom', 'react-dom/server', 'framer-motion',
             'react-markdown', 'remark-gfm'],
  // A bundled CJS dependency (recharts, added for TrendChart) still calls the
  // bare `require('react')` its own build emitted, to reach the packages
  // above that stay external. esbuild's own output has no `require` in ESM
  // scope to satisfy that call — this is esbuild's documented fix: a real
  // `require`, backed by Node's own resolver, so those calls work exactly as
  // they would under CommonJS. Without it: "Dynamic require of react is not
  // supported", thrown from inside recharts, not from anything this repo owns.
  banner: {
    js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
  },
  logLevel: 'warning',
});

try {
  await import(pathToFileURL(out).href);
} finally {
  await rm(out, { force: true });
}
