// Turns the raw /api/admin/insights payload into a short list of plain-language
// observations -- "what should we look at first to make the system better?".
//
// Pure, so it is unit-tested without React (test/insightsLogic.test.mjs).
//
// THE THRESHOLDS BELOW ARE RULES OF THUMB, not measured limits. They exist to
// rank attention, and the UI says so. Every observation names the number it
// came from, and none is produced from a section that was unavailable or from
// too few queries to mean anything.

import { fmtNum, fmtPct, fmtSecs, pct } from './format.js';

export const MIN_QUERIES = 5; // below this, percentages are noise

const T = {
  slowP90: 30,          // seconds
  bigPrompt: 20000,     // average prompt tokens per answered query
  errorRate: 5,         // % of queries failing
  unsourcedRate: 10,    // % answered with no tool call
  lowConfidenceRate: 25, // % rated Low
  handEditedRate: 20,   // % of uploads a user had to correct by hand
  unreadableAvg: 3,     // average unreadable cells per uploaded document
  repeatedTimes: 3,     // times the same question was asked
};

export function deriveObservations(ins) {
  if (!ins || !ins.telemetry_available) return [];
  const out = [];
  const add = (tone, title, detail) => out.push({ tone, title, detail });

  const total = ins.usage?.available ? Number(ins.usage.queries || 0) : 0;
  const enough = total >= MIN_QUERIES;

  // Performance
  const perf = ins.performance;
  if (perf?.available && enough && perf.percentiles?.n >= MIN_QUERIES) {
    const { p50, p90 } = perf.percentiles;
    if (p90 > T.slowP90) {
      add('warn', `Slow tail: 1 in 10 answers takes over ${fmtSecs(p90)}`,
        `Median is ${fmtSecs(p50)}. The slowest queries below usually share a heavy tool or an oversized context.`);
    }
    const slow = (perf.latency_by_tool || [])[0];
    if (slow && perf.percentiles.mean && slow.avg_elapsed > perf.percentiles.mean * 1.3) {
      add('info', `Answers that call ${slow.tool} are the slowest (avg ${fmtSecs(slow.avg_elapsed)})`,
        `Across ${fmtNum(slow.calls)} answers this window, against an overall mean of ${fmtSecs(perf.percentiles.mean)}. Worth profiling that tool first.`);
    }
  }

  // Cost
  const cost = ins.cost;
  if (cost?.available && enough && cost.averages?.avg_prompt > T.bigPrompt) {
    add('warn', `Prompts are large: ${fmtNum(cost.averages.avg_prompt)} tokens on average`,
      'Most of it is retrieved context and tool output. Trimming what is passed to the model is the biggest lever on both cost and latency.');
  }

  // Reliability
  const rel = ins.reliability;
  if (rel?.available && enough) {
    const failed = (rel.status || []).filter((s) => s.status !== 'ok')
      .reduce((n, s) => n + Number(s.n || 0), 0);
    const rate = pct(failed, total);
    if (rate !== null && rate > T.errorRate) {
      const top = (rel.by_stage || [])[0];
      add('err', `${fmtPct(rate, 1)} of queries failed (${fmtNum(failed)} of ${fmtNum(total)})`,
        top ? `Most common cause: ${top.stage} (${fmtNum(top.n)}). These failures were previously invisible: a failed turn is never saved to history.` : '');
    }
  }

  // Answer quality
  const q = ins.quality;
  if (q?.available && enough) {
    const unsRate = pct(q.unsourced?.unsourced, q.unsourced?.total);
    if (unsRate !== null && unsRate > T.unsourcedRate) {
      add('warn', `${fmtPct(unsRate)} of answers used no tool (unsourced)`,
        'These are answered from the model\'s general knowledge, not from a document, which is the main hallucination risk. Review the examples below.');
    }
    const conf = q.confidence || [];
    const confTotal = conf.reduce((n, c) => n + Number(c.n || 0), 0);
    const low = conf.find((c) => String(c.confidence).toLowerCase() === 'low');
    const lowRate = pct(low?.n, confTotal);
    if (lowRate !== null && lowRate > T.lowConfidenceRate) {
      add('warn', `${fmtPct(lowRate)} of answers are rated Low confidence`,
        (q.lowered_reasons || [])[0]?.reason
          ? `Most common reason confidence was lowered: "${q.lowered_reasons[0].reason}".`
          : 'Check which questions and tools these come from.');
    }
    if ((q.no_data_answers || []).length >= 3) {
      add('info', `${q.no_data_answers.length}+ answers reported that nothing was found`,
        'Questions the corpus or tools could not answer. Each is a candidate for a new tool, better retrieval, or an ingestion gap. (Detected by phrase match.)');
    }
  }

  // Tools
  const tools = ins.tools;
  if (tools?.available && enough && (tools.unused_in_window || []).length > 0) {
    add('info', `${tools.unused_in_window.length} tool(s) seen before but unused this window`,
      `${tools.unused_in_window.slice(0, 4).join(', ')}${tools.unused_in_window.length > 4 ? '…' : ''}. Unused tools still cost prompt tokens on every query. (Only tools seen at least once can be listed.)`);
  }

  // Repeats
  const top = (ins.repeats?.questions || [])[0];
  if (ins.repeats?.available && top && Number(top.asked) >= T.repeatedTimes) {
    add('info', `One question was asked ${fmtNum(top.asked)} times`,
      `"${top.question.slice(0, 80)}". Repeated questions are candidates for a suggested prompt, a cached answer or a dedicated tool.`);
  }

  // Live ingestion
  const ing = ins.ingestion;
  if (ing?.available && Number(ing.documents) >= 3) {
    const edited = pct(ing.hand_edited, ing.documents);
    if (edited !== null && edited > T.handEditedRate) {
      add('warn', `${fmtPct(edited)} of uploaded documents needed hand corrections`,
        'Users are fixing extraction by hand. The documents listed under Live ingestion show where the reader needs work.');
    }
    if (Number(ing.avg_unreadable) > T.unreadableAvg) {
      add('warn', `Uploads average ${Number(ing.avg_unreadable).toFixed(1)} unreadable cells each`,
        'Withheld figures are safe but reduce what can be analysed. Compare ingest versions below to see whether it is improving.');
    }
  }

  if (out.length === 0 && enough) {
    add('ok', 'Nothing stands out in this window',
      'Latency, cost, failures, sourcing and extraction quality are all within the usual ranges.');
  }
  if (!enough) {
    add('info', 'Not enough data yet',
      `Only ${fmtNum(total)} queries recorded in this window (need ${MIN_QUERIES}+). Telemetry started when this feature shipped, so earlier history is not included.`);
  }
  return out;
}
