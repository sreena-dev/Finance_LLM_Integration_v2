/**
 * Render every FS upload component and fail on any exception.
 *
 * This exists because a bad patch removed `uploaded`, `legend`, `citation`,
 * `mode` and `conversationId` from AnswerCard's destructuring while leaving the
 * JSX that used them. Every assistant message then threw
 * `ReferenceError: uploaded is not defined`, React unmounted the tree, and the
 * whole application went blank — with nothing in any test, build or type check
 * to catch it. `vite build` does not catch it either: an undefined identifier is
 * a runtime error, not a build error.
 *
 * `renderToString` executes each component function for real, which is exactly
 * what a build cannot do. Effects do not run, so no network calls are made.
 *
 *   node test/run-render.mjs
 */

import { renderToString } from 'react-dom/server';

import AnswerCard from '../src/components/chat/AnswerCard';
import CitationViewer from '../src/components/chat/CitationViewer';
import Dropzone from '../src/components/common/Dropzone';
import ErrorBoundary from '../src/components/common/ErrorBoundary';
import IngestProgress from '../src/components/ingestion/IngestProgress';
import QualityReport from '../src/components/ingestion/QualityReport';
import UploadPanel from '../src/components/ingestion/UploadPanel';
import DocumentChips from '../src/components/ingestion/DocumentChips';
import Composer from '../src/components/chat/Composer';
import ChatView from '../src/components/chat/ChatView';
import DocumentPane, { EditableTable } from '../src/components/ingestion/DocumentPane';
import Markdown from '../src/components/common/Markdown';
import AuthScreen from '../src/auth/AuthScreen';
import { AuthProvider } from '../src/auth/AuthContext';
import Sidebar from '../src/components/Sidebar';
import AdminDashboard from '../src/components/admin/AdminDashboard';
import InsightsPanel from '../src/components/admin/InsightsPanel';
import UsersTable from '../src/components/admin/UsersTable';
import UserDetail from '../src/components/admin/UserDetail';
import ConversationViewer from '../src/components/admin/ConversationViewer';
import { parseFigureCell } from '../src/lib/figures';
import { groupIndian, inWords } from '../src/lib/indian';
import { friendlyError, stageIndex } from '../src/components/ingestion/IngestProgress';
import { keptUntil, reasonText, tablesWithProblems } from '../src/components/ingestion/QualityReport';

const MODE = { id: 'financial-statement', base_path: '/api/financial-statement', short_label: 'FS' };

/** An answer from before the upload feature existed — rehydrated from storage. */
const LEGACY_RESULT = {
  summary: 'Legacy summary',
  final_answer: 'Legacy answer',
  evidences_md: '- evidence',
  chunks: [{ index: 1, source: 'Ind AS 36, para 12', content: 'text', rerank_score: 0.9 }],
  num_tables_searched: 3,
  num_chunks_retrieved: 8,
  elapsed_seconds: 2.5,
};

/** A current answer, with everything the upload path adds. */
const UPLOAD_RESULT = {
  ...LEGACY_RESULT,
  materiality_legend: {
    markdown: '**Materiality legend**\n\n- Threshold applied: **22,800.20**',
    provisional: true,
    amount: 22800.2,
  },
  uploaded_documents: [
    { doc_id: 'up_a', filename: 'SFS.pdf', financial_year: '2022-23', unreadable_cells: 6 },
    { doc_id: 'up_b', filename: 'IARSFS.pdf', financial_year: '2022-23', unreadable_cells: 0 },
  ],
  chunks: [{
    index: 1, source: 'SFS.pdf, page 5', content: 'table text',
    rerank_score: 0.8, doc_id: 'up_a', table_id: 'up_a_t1',
  }],
};

/** Exercises TrendChart — a render path a build alone would not catch (see this file's own
 * header comment on why renderToString exists here at all). */
const FS_FEATURE_RESULT = {
  ...LEGACY_RESULT,
  final_answer: 'Revenue was broadly flat while trade receivables rose sharply.',
  trend_data: [{
    statement_label: 'Balance Sheet',
    years_sorted: [2023, 2024],
    rows: [
      { label: 'Revenue from Operations', cells: [4150, 4200], yoy: [[50, 1.2]], cagr: 1.2, significant: false },
      { label: 'Trade Receivables', cells: [850, 1360], yoy: [[510, 60.0]], cagr: 60.0, significant: true },
    ],
    divergences: [{
      year: 2024, line_a: 'Revenue from Operations', line_b: 'Trade Receivables',
      pct_a: 1.2, pct_b: 60.0, delta_pct: 58.8,
      label: 'cut-off, collectability, fictitious revenue or delayed collections',
    }],
  }],
};

const DOC = {
  doc_id: 'up_a', filename: 'SFS.pdf', company: 'IDBI Trusteeship Services Ltd',
  pages: 23, tables: 9, grade: 'fair', low_grade: 'poor',
  identification: {
    financial_year: '2022-23', fy_confidence: 'high',
    framework: 'Ind AS', framework_division: 'II', unresolved_conflicts: ['a conflict'],
  },
  quality: {
    grade: 'fair', low_grade: 'poor', vlm_used: false, notes: ['a note'],
    unreadable_cells: [{ raw: '(1,757', page_no: 5, row_label: 'Disposals', column: 'Total', reasons: ['sign_uncertain'] }],
    failed_footings: [{ page_no: 5, subtotal_label: 'Balance', printed: 64777, recomputed: null }],
    pages: [{ page_no: 5, grade: 'fair', defects: [{ code: 'skew_corrected', detail: 'rotated 1.7 degrees' }] }],
  },
  coverage: {
    paths: [
      { name: 'accounting-policy lookup', state: 'unavailable', detail: 'no headings', workaround: 'ask by note number' },
      { name: 'disclosure / notes search', state: 'viable', detail: '12 passages' },
    ],
    unavailable: ['accounting-policy lookup'],
    degraded: [],
  },
};

// ── Admin dashboard fixtures ────────────────────────────────────────────────
const ADMIN_USER = {
  user_id: 'u1', username: 'harish', email: 'h@x.io', display_name: 'Harish', is_super_admin: true,
  created_at: '2026-09-01T10:00:00Z', last_login_at: '2026-09-24T09:00:00Z',
  fs_conversations: 4, fs_messages: 18, tb_conversations: 1, tb_messages: 6,
  queries: 12, avg_elapsed: 14.2, errors: 1, uploads: 3, last_query_at: '2026-09-24T09:10:00Z',
};
const INSIGHTS = {
  days: 30, telemetry_available: true,
  usage: { available: true, queries: 120, active_users: 6,
    daily: [{ day: '2026-09-22', queries: 10, users: 3 }, { day: '2026-09-23', queries: 22, users: 4 }],
    top_users: [], modes: [] },
  performance: { available: true,
    percentiles: { n: 100, p50: 9, p90: 41, p99: 70, mean: 15 },
    daily: [{ day: '2026-09-22', p50: 8, p90: 30 }, { day: '2026-09-23', p50: 10, p90: 44 }],
    slowest: [{ event_id: 'e1', query_text: 'Assess going concern for X', username: 'harish',
      elapsed_seconds: 70, tool_calls: 4, prompt_tokens: 30000, conversation_id: 'c1', user_id: 'u1' }],
    latency_by_tool: [{ tool: 'assess_going_concern', calls: 9, avg_elapsed: 30 }] },
  cost: { available: true, averages: { avg_prompt: 24000, avg_completion: 700 },
    daily: [{ day: '2026-09-22', prompt_tokens: 100000, completion_tokens: 5000 }],
    top_users: [], heaviest_queries: [] },
  reliability: { available: true, status: [{ status: 'ok', n: 110 }, { status: 'error', n: 10 }],
    by_stage: [{ stage: 'RuntimeError', status: 'error', n: 10 }],
    recent_failures: [{ event_id: 'e2', created_at: '2026-09-23T10:00:00Z', query_text: 'q', status: 'error',
      error_stage: 'RuntimeError', error_message: 'boom', username: 'harish', user_id: 'u1' }] },
  quality: { available: true, confidence: [{ confidence: 'High', n: 80 }, { confidence: 'Low', n: 30 }],
    unsourced: { unsourced: 15, total: 110 }, unsourced_recent: [], lowered_reasons: [], no_data_answers: [] },
  tools: { available: true, used: [{ tool: 'run_tie_out_checks', calls: 12 }], unused_in_window: ['scan_psu_red_flags'] },
  repeats: { available: true, questions: [{ question: 'check caro for ntpc', asked: 4, users: 2, avg_elapsed: 20 }] },
  ingestion: { available: true, documents: 6, avg_unreadable: 4.2, avg_recovered: 1, hand_edited: 3,
    by_grade: [{ grade: 'fair', n: 4 }], by_version: [{ version: 'v1', n: 6, avg_unreadable: 4.2 }],
    worst_documents: [{ filename: 'SFS.pdf', company: 'X Ltd', financial_year: '2023-24', grade: 'fair', unreadable: 9, failed_footings: 2 }],
    daily: [] },
};
const FS_CONVERSATION = {
  conversation_id: 'c1', mode: 'fs', user_id: 'u1', username: 'harish',
  messages: [
    { seq: 1, role: 'user', content: 'Assess going concern', created_at: '2026-09-23T10:00:00Z' },
    { seq: 2, role: 'assistant', content: 'Answer text', created_at: '2026-09-23T10:00:20Z',
      payload: { final_answer: 'Going concern indicators warrant review.', summary: 'Summary', checks: { confidence: 'High' } } },
  ],
};

const CASES = [
  // The regression: a stored payload with none of the upload fields.
  ['AnswerCard (legacy payload)', <AnswerCard result={LEGACY_RESULT} mode={MODE} conversationId="c1" />],
  ['AnswerCard (upload payload)', <AnswerCard result={UPLOAD_RESULT} mode={MODE} conversationId="c1" />],
  ['AnswerCard (no mode/convo)', <AnswerCard result={UPLOAD_RESULT} />],
  ['AnswerCard (empty result)', <AnswerCard result={{}} />],
  ['AnswerCard (null result)', <AnswerCard result={null} />],
  ['AnswerCard (trend chart)', <AnswerCard result={FS_FEATURE_RESULT} mode={MODE} conversationId="c1" />],

  ['QualityReport (full)', <QualityReport doc={DOC} onDelete={() => {}} />],
  ['QualityReport (no coverage)', <QualityReport doc={{ ...DOC, coverage: undefined }} />],
  ['QualityReport (bare doc)', <QualityReport doc={{ doc_id: 'x', filename: 'x.pdf' }} />],
  ['QualityReport (null doc)', <QualityReport doc={null} />],

  ['IngestProgress (queued)', <IngestProgress filename="a.pdf" stage="queued" message="Queued…" fraction={0} />],
  ['IngestProgress (convert)', <IngestProgress filename="a.pdf" stage="convert" message="Detecting" fraction={0.4} />],
  ['IngestProgress (done)', <IngestProgress filename="a.pdf" stage="done" message="Finished" fraction={1} />],
  ['IngestProgress (error)', <IngestProgress filename="a.pdf" error="it failed" />],
  ['IngestProgress (no props)', <IngestProgress />],

  ['Dropzone', <Dropzone onFiles={() => {}} />],
  ['Dropzone (busy)', <Dropzone onFiles={() => {}} busy />],

  ['CitationViewer', <CitationViewer mode={MODE} conversationId="c1" docId="up_a" tableId="t1" caption="Note 1" onClose={() => {}} />],

  ['UploadPanel', <UploadPanel mode={MODE} conversationId="c1" />],
  ['UploadPanel (no conversation)', <UploadPanel mode={MODE} conversationId={null} />],

  ['ErrorBoundary (passthrough)', <ErrorBoundary label="x"><span>ok</span></ErrorBoundary>],

  ['DocumentChips', <DocumentChips docs={[DOC]} onDelete={() => {}} />],
  ['DocumentChips (empty)', <DocumentChips docs={[]} />],
  ['DocumentChips (null)', <DocumentChips docs={null} />],

  ['Composer (no attach)', <Composer onSubmit={() => {}} placeholder="Ask…" />],
  ['Composer (with attach)', <Composer onSubmit={() => {}} placeholder="Ask…" onFiles={() => {}} />],
  ['Composer (attach busy)', <Composer onSubmit={() => {}} placeholder="Ask…" onFiles={() => {}} attachBusy />],

  // The whole FS chat surface, which is what actually went blank.
  ['ChatView (empty thread)',
    <ChatView mode={MODE} health={{ available: true }} thread={[]} setThread={() => {}}
              conversationId={null} onConversationChange={() => {}} />],
  ['ChatView (with answers)',
    <ChatView mode={MODE} health={{ available: true }} setThread={() => {}}
              conversationId="c1" onConversationChange={() => {}}
              thread={[
                { role: 'user', text: 'what are total assets?', id: 'u1' },
                { role: 'assistant', result: UPLOAD_RESULT, id: 'a1' },
                { role: 'assistant', result: LEGACY_RESULT, id: 'a2' },
              ]} />],

  ['DocumentPane', <DocumentPane mode={MODE} conversationId="c1" doc={DOC} onClose={() => {}} />],
  ['DocumentPane (no conversation)', <DocumentPane mode={MODE} conversationId={null} doc={DOC} onClose={() => {}} />],
  ['DocumentPane (null doc)', <DocumentPane mode={MODE} conversationId="c1" doc={null} onClose={() => {}} />],

  // A table with one flagged cell -- the exact shape `page_text` returns
  // when ARTHA_FS_UPLOAD_USER_EDITS is on (see edits.cells_for_table). Only
  // this shape switches a table off the plain <Markdown> path, so this is
  // the one render case that actually exercises the button/badge/ARIA label,
  // not just the "Loading…" placeholder every other DocumentPane case stops
  // at (renderToString runs no effects, so PageText's own fetch never fires).
  ['EditableTable (one flagged cell)', <EditableTable
    mode={MODE} conversationId="c1" docId="up_a" pageNo={5} onSaved={() => {}}
    table={{
      table_id: 'up_a_t1',
      table_md: '| Particulars | Amount |\n| --- | --- |\n'
        + '| Revenue | [unreadable: page 5, table t1, row "Revenue", col "Amount"] |',
      cells: [{
        row_index: 0, col_index: 1, state: 'unreadable',
        marker: '[unreadable: page 5, table t1, row "Revenue", col "Amount"]',
        recovered_text: null, confidence: null, row_label: 'Revenue', column: 'Amount',
      }],
    }}
  />, (html) => {
    if (!html.includes('fig--unreadable')) throw new Error('no state badge class');
    if (!html.includes('<button')) throw new Error('the flagged cell did not render as a button');
    if (!html.includes('Unreadable figure, row Revenue, column Amount')) {
      throw new Error('the ARIA label did not name the row/column');
    }
  }],

  // --- redesign: figure states, tag blocks, drawer, cards, wait bar --------
  ['Markdown (figure states + tags)', <Markdown>{[
    '| Particulars | FY 2023-24 | FY 2022-23 |',
    '| --- | --- | --- |',
    '| Revenue | 1,42,318.40 | 1,28,904.10 |',
    '| Finance costs | [unreadable: page 61, table t3, row "Finance costs", col "FY 2023-24"] | 2,318.90 |',
    '| Impairment losses | [recovered 1,757.00; second read, confidence medium, caveat: page 61] | 1,204.50 |',
    '| Other expenses | 21946.85 [user-entered] | 19,880.20 |',
    '| Total expenses | 1,17,559.45 | 1,04,724.55 |',
    '',
    '**RISK FLAG** The impairment figure is a second-reader value.',
  ].join(String.fromCharCode(10))}</Markdown>, (html) => {
    for (const needle of ['fig--unreadable', 'fig--recovered', 'fig--you', 'md__num', 'tagblock--risk', 'md__total']) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    // Extracted figures are shown exactly as printed -- never regrouped or padded.
    if (!html.includes('1,42,318.40')) throw new Error('a printed figure was reformatted');
    if (html.includes('142,318')) throw new Error('a printed figure was regrouped');
    if (html.includes('21,946.85')) throw new Error('should show the user figure as typed with Indian grouping only');
  }],
  ['QualityReport drawer (Enter / Review / kept until)', <QualityReport doc={{
    ...DOC, uploaded_at: 1790000000, user_entered_cells: 1, recovered_cells: 1,
    quality: { ...DOC.quality, unreadable_cells: [
      { raw: '', page_no: 61, table_id: 'up_a_t1_1', row_label: 'Finance costs', column: 'FY 2023-24', reasons: ['unreadable_text'], recovered_text: null },
      { raw: '', page_no: 61, table_id: 'up_a_t1_1', row_label: 'Impairment losses', column: 'FY 2023-24', reasons: ['readers_disagree'], recovered_text: '1,757.00', confidence: 'medium' },
    ] },
    periods: [{ label: 'FY 2023-24', is_comparative: false }, { label: 'FY 2022-23', is_comparative: true }],
  }} retentionDays={30} onEnter={() => {}} onOpenPages={() => {}} onClose={() => {}} />, (html) => {
    for (const needle of ['Enter', 'Review', 'Smudged or unreadable digits', 'kept until', 'prior-year column', 'Quality report']) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
  }],
  ['DocumentChips (counters)', <DocumentChips docs={[{
    ...DOC, recovered_cells: 1, user_entered_cells: 2,
    quality: { ...DOC.quality },
  }]} onView={() => {}} />, (html) => {
    for (const needle of ['figure withheld', '1 recovered', 'did not foot', '2 entered by you', 'Open pages']) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
  }],
  ['IngestProgress (queued ahead)', <IngestProgress filename="b.pdf" status="queued" ahead={1} />, (html) => {
    if (!html.includes('Queued · 1 ahead')) throw new Error('queue position missing');
  }],
  ['IngestProgress (password failure)', <IngestProgress filename="c.pdf" error="c.pdf: file is encrypted" onRetry={() => {}} />, (html) => {
    if (!html.includes('password-protected') || !html.includes('Retry')) throw new Error('failure not actionable');
  }],
  ['AuthScreen (two panes, no dead link)', <AuthProvider><AuthScreen /></AuthProvider>, (html) => {
    for (const needle of ['Welcome back.', 'Every figure, traced to its page.', 'Not an official government service']) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    if (/forgot/i.test(html)) throw new Error('there is no password reset, so no Forgot link');
  }],
  ['Sidebar (rail + retention)', <AuthProvider><Sidebar rail={false} retentionDays={45} activeId="financial-statement"
      onSelect={() => {}} health={{ 'trial-balance': { available: false } }}
      modes={[
        { id: 'financial-statement', short_label: 'Financial Statements', integrated: true },
        { id: 'trial-balance', short_label: 'Trial Balance', integrated: true },
      ]} /></AuthProvider>, (html) => {
    if (!html.includes('kept for 45 days')) throw new Error('retention note missing');
    if (!html.includes('Unavailable')) throw new Error('unavailable label missing');
  }],
  ['AnswerCard (How this was checked, collapsed)', <AnswerCard mode={MODE} conversationId="c1" result={{
    ...UPLOAD_RESULT, rewritten_query: 'Compare finance costs FY 2023-24 vs FY 2022-23',
    upload_store_notice: 'Your uploaded documents could not be reached.',
    checks: { confidence: 'Medium', reduced_from: 'High', reduced_reason: 'a figure is withheld', tools_used: ['search_corpus'], unsourced: false },
  }} />, (html) => {
    for (const needle of ['How this was checked', 'Read as:', 'could not be reached', 'answer']) {
      if (!html.includes(needle)) throw new Error(`missing ${needle}`);
    }
    // Collapsed: the detail must not be in the markup until it is opened.
    if (html.includes('Lowered from')) throw new Error('checks detail should be collapsed');
  }],
  ['pure helpers', <span />, () => {
    const eq = (a, b, m) => { if (a !== b) throw new Error(`${m}: got ${a}, want ${b}`); };
    eq(groupIndian('150000.5'), '1,50,000.5', 'lakh grouping');
    eq(groupIndian('12859'), '12,859', 'no decimals added');
    eq(groupIndian('(5000)'), '(5,000)', 'negative');
    eq(groupIndian('10000000'), '1,00,00,000', 'crore');
    eq(inWords(150000), '₹1.5 lakh', 'words');
    eq(parseFigureCell('12,859 [user-entered]').kind, 'you', 'user');
    eq(parseFigureCell('[recovered 1,757.00; second read, confidence low]').value, '1,757.00', 'recovered');
    eq(parseFigureCell('[unreadable: page 1]').kind, 'unreadable', 'unreadable');
    eq(parseFigureCell('460').kind, 'plain', 'plain');
    eq(stageIndex('convert', 0.5), 3, 'stage index');
    eq(stageIndex('identify', 0.97), 7, 'finishing');
    eq(friendlyError('x.pdf: PDF is password protected'), 'This file is password-protected. Remove the password and attach it again.', 'password');
    eq(reasonText('readers_disagree'), 'Read differently by the two readers', 'reason');
    eq(tablesWithProblems({ unreadable_cells: [{ table_id: 'a' }, { table_id: 'a' }], recovered_cells: [{ table_id: 'b' }] }), 2, 'tables');
    if (!String(keptUntil(86400 * 365, 30)).includes('1971')) throw new Error('kept until');
  }],
];

CASES.push(
  ['Sidebar (admin link shown to an admin)', <AuthProvider><Sidebar showAdmin activeId="financial-statement"
      onSelect={() => {}} health={{}} modes={[{ id: 'financial-statement', short_label: 'FS', integrated: true }]} /></AuthProvider>,
    (html) => { if (!html.includes('Users, chats')) throw new Error('admin link missing for an admin'); }],
  ['Sidebar (no admin link for everyone else)', <AuthProvider><Sidebar activeId="financial-statement"
      onSelect={() => {}} health={{}} modes={[{ id: 'financial-statement', short_label: 'FS', integrated: true }]} /></AuthProvider>,
    (html) => { if (/Users, chats|Admin/.test(html)) throw new Error('admin link leaked to a non-admin'); }],

  ['AdminDashboard (initial state)', <AdminDashboard onExit={() => {}} />,
    (html) => { if (!html.includes('Super administrator')) throw new Error('header missing'); }],
  ['InsightsPanel (populated)', <InsightsPanel insights={INSIGHTS} onOpenConversation={() => {}} />, (html) => {
    if (!html.includes('Where to look first')) throw new Error('observations missing');
    if (!html.includes('Slow tail')) throw new Error('slow-tail observation missing');
    if (!html.includes('Live ingestion quality')) throw new Error('ingestion section missing');
  }],
  ['InsightsPanel (telemetry unavailable)', <InsightsPanel insights={{ telemetry_available: false }} />,
    (html) => { if (!html.includes('artha_query_events')) throw new Error('unavailable notice missing'); }],
  ['InsightsPanel (a section unavailable)',
    <InsightsPanel insights={{ ...INSIGHTS, cost: { available: false, reason: 'boom' } }} />,
    (html) => { if (!html.includes('boom')) throw new Error('section reason missing'); }],
  ['InsightsPanel (null)', <InsightsPanel insights={null} />],
  ['UsersTable', <UsersTable users={[ADMIN_USER]} tb={{ available: false, reason: 'down' }} onOpen={() => {}} />, (html) => {
    if (!html.includes('Harish')) throw new Error('user missing');
    if (!html.includes('Trial Balance counts are unavailable')) throw new Error('TB degrade note missing');
  }],
  ['UsersTable (empty)', <UsersTable users={[]} />],
  ['UserDetail', <UserDetail user={ADMIN_USER} conversations={[{ conversation_id: 'c1', title: 'Assess going concern', n_messages: 4, last_at: '2026-09-23T10:00:00Z' }]}
      convosState={{ loading: false }} mode="fs" onMode={() => {}} events={[]} eventsState={{ loading: false, available: true }}
      statusFilter="" onStatusFilter={() => {}} onOpenConversation={() => {}} onBack={() => {}} />,
    (html) => { if (!html.includes('Assess going concern')) throw new Error('conversation missing'); }],
  ['UserDetail (null user)', <UserDetail user={null} />],
  ['ConversationViewer (FS, read-only)', <ConversationViewer conversation={FS_CONVERSATION} onBack={() => {}} />, (html) => {
    if (!html.includes('read-only')) throw new Error('read-only marker missing');
    if (!html.includes('Going concern indicators warrant review')) throw new Error('answer missing');
    if (/<textarea/.test(html)) throw new Error('a composer leaked into a read-only view');
  }],
  ['ConversationViewer (TB)', <ConversationViewer conversation={{ ...FS_CONVERSATION, mode: 'tb' }} onBack={() => {}} />],
  ['ConversationViewer (null)', <ConversationViewer conversation={null} />],
);

let failed = 0;
for (const [name, element, check] of CASES) {
  try {
    const html = renderToString(element);
    if (typeof html !== 'string') throw new Error('did not produce markup');
    check?.(html.replace(/<!-- -->/g, ''));
    console.log(`  ok    ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`  FAIL  ${name}: ${e.message}`);
  }
}

console.log('');
console.log(failed === 0
  ? `all ${CASES.length} render cases passed`
  : `${failed} of ${CASES.length} render cases FAILED`);

process.exit(failed === 0 ? 0 : 1);
