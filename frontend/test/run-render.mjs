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
  logLevel: 'warning',
});

try {
  await import(pathToFileURL(out).href);
} finally {
  await rm(out, { force: true });
}
