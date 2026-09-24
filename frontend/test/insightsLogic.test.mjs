// Run: node --test test/insightsLogic.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deriveObservations, MIN_QUERIES } from '../src/components/admin/insightsLogic.js';

const base = (over = {}) => ({
  telemetry_available: true,
  usage: { available: true, queries: 100, active_users: 5 },
  performance: {
    available: true,
    percentiles: { n: 100, p50: 8, p90: 12, p99: 20, mean: 9 },
    latency_by_tool: [],
  },
  cost: { available: true, averages: { avg_prompt: 8000, avg_completion: 600 } },
  reliability: { available: true, status: [{ status: 'ok', n: 100 }], by_stage: [] },
  quality: {
    available: true,
    confidence: [{ confidence: 'High', n: 80 }, { confidence: 'Low', n: 5 }],
    unsourced: { unsourced: 2, total: 100 },
    lowered_reasons: [], no_data_answers: [],
  },
  tools: { available: true, used: [], unused_in_window: [] },
  repeats: { available: true, questions: [] },
  ingestion: { available: true, documents: 0 },
  ...over,
});

const titles = (obs) => obs.map((o) => o.title).join(' | ');

test('no telemetry -> no observations at all (never invents any)', () => {
  assert.deepEqual(deriveObservations(null), []);
  assert.deepEqual(deriveObservations({ telemetry_available: false }), []);
});

test('a healthy window says so instead of staying silent', () => {
  const obs = deriveObservations(base());
  assert.equal(obs.length, 1);
  assert.equal(obs[0].tone, 'ok');
});

test('too few queries -> "not enough data", and no percentages are read', () => {
  const obs = deriveObservations(base({
    usage: { available: true, queries: MIN_QUERIES - 1, active_users: 1 },
    performance: { available: true, percentiles: { n: 2, p50: 1, p90: 99, mean: 50 }, latency_by_tool: [] },
  }));
  assert.equal(obs.length, 1);
  assert.match(obs[0].title, /Not enough data/);
  assert.doesNotMatch(titles(obs), /Slow tail/);
});

test('a slow p90 is flagged with the real numbers', () => {
  const obs = deriveObservations(base({
    performance: { available: true, percentiles: { n: 100, p50: 12, p90: 47, p99: 80, mean: 20 }, latency_by_tool: [] },
  }));
  assert.match(titles(obs), /Slow tail/);
  assert.match(obs[0].title, /47s/);
});

test('a high failure rate is an error, and names the top stage', () => {
  const obs = deriveObservations(base({
    reliability: {
      available: true,
      status: [{ status: 'ok', n: 80 }, { status: 'error', n: 20 }],
      by_stage: [{ stage: 'ModeUnavailableError', status: 'unavailable', n: 15 }],
    },
  }));
  const err = obs.find((o) => o.tone === 'err');
  assert.ok(err);
  assert.match(err.title, /20\.0% of queries failed/);
  assert.match(err.detail, /ModeUnavailableError/);
});

test('unsourced answers above the threshold are flagged', () => {
  const obs = deriveObservations(base({
    quality: { ...base().quality, unsourced: { unsourced: 20, total: 100 } },
  }));
  assert.match(titles(obs), /unsourced/);
});

test('large prompts are flagged', () => {
  const obs = deriveObservations(base({
    cost: { available: true, averages: { avg_prompt: 32000 } },
  }));
  assert.match(titles(obs), /Prompts are large/);
});

test('a repeated question is surfaced', () => {
  const obs = deriveObservations(base({
    repeats: { available: true, questions: [{ question: 'what is the going concern position', asked: 6, users: 3 }] },
  }));
  assert.match(titles(obs), /asked 6 times/);
});

test('an unavailable section produces nothing rather than a guess', () => {
  const obs = deriveObservations(base({
    reliability: { available: false, reason: 'boom' },
    quality: { available: false, reason: 'boom' },
    cost: { available: false, reason: 'boom' },
  }));
  assert.equal(obs.length, 1);
  assert.equal(obs[0].tone, 'ok');
});

test('hand-edited uploads are flagged only with enough documents', () => {
  const few = deriveObservations(base({
    ingestion: { available: true, documents: 2, hand_edited: 2, avg_unreadable: 9 },
  }));
  assert.doesNotMatch(titles(few), /hand corrections/);
  const many = deriveObservations(base({
    ingestion: { available: true, documents: 10, hand_edited: 5, avg_unreadable: 1 },
  }));
  assert.match(titles(many), /hand corrections/);
});
