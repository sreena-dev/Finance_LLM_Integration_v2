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

const CASES = [
  // The regression: a stored payload with none of the upload fields.
  ['AnswerCard (legacy payload)', <AnswerCard result={LEGACY_RESULT} mode={MODE} conversationId="c1" />],
  ['AnswerCard (upload payload)', <AnswerCard result={UPLOAD_RESULT} mode={MODE} conversationId="c1" />],
  ['AnswerCard (no mode/convo)', <AnswerCard result={UPLOAD_RESULT} />],
  ['AnswerCard (empty result)', <AnswerCard result={{}} />],
  ['AnswerCard (null result)', <AnswerCard result={null} />],

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
    if (!html.includes('docpane__cellbtn--unreadable')) throw new Error('no state badge class');
    if (!html.includes('<button')) throw new Error('the flagged cell did not render as a button');
    if (!html.includes('Unreadable figure, row Revenue, column Amount')) {
      throw new Error('the ARIA label did not name the row/column');
    }
  }],
];

let failed = 0;
for (const [name, element, check] of CASES) {
  try {
    const html = renderToString(element);
    if (typeof html !== 'string') throw new Error('did not produce markup');
    check?.(html);
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
