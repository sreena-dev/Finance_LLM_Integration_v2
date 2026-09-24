/**
 * Financial Diagnostic Report — UI.
 *
 * SCOPE: this file plus `api.js` are the entire FDR UI; nothing else in the
 * codebase is touched by them. One page, one scroll surface. A segmented control
 * (Query / Report) sits in a slim bar that hides on scroll-down and reveals on
 * scroll-up, so the two views never need their own separate screens or headers.
 *
 * THE ENTITY PICKER IS SHARED, AND IT IS ABOVE BOTH VIEWS.
 * Every answer this mode gives is scoped to one entity, so the entity cannot be
 * a control that lives inside one tab: a question asked on the Query tab and a
 * report generated on the Report tab must be about the same company, and the
 * only way to make that obvious is to put the selection where both can see it.
 * It also means the question text never has to name the company, which is what
 * removes the possibility of a question being answered from the wrong filing.
 *
 * NOTHING HERE FABRICATES DATA. The Report segment is still an empty shell — its
 * generation pipeline is not built — and says so rather than showing a mock.
 * Styles are scoped to .fdr-root and mirror the app's own tokens
 * (src/styles/index.css) so this drops into the existing theme unchanged.
 *
 * This file wires up the top-level segmented view (Query / Report / XBRL
 * Direct) and the shared entity picker. The panes themselves — QueryPane,
 * ReportPane, XbrlPane — and their supporting pieces live in
 * `FdrQueryPane.jsx`, `FdrReportBlocks.jsx` and `FdrXbrlPane.jsx`
 * respectively; `FdrShared.jsx` holds what all three use, and `fdrStyles.js`
 * holds the CSS. Split out once this file passed 2,800 lines — no behavior
 * changed in the split.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { askQuestionStream, fetchEntities, fetchHealth, fetchXbrlEntities } from './api';
import { EntityBar } from './FdrShared';
import { QueryPane, QueryComposer } from './FdrQueryPane';
import { ReportPane } from './FdrReportBlocks';
import { XbrlDownloadButton, XbrlFilingBar, XbrlPane } from './FdrXbrlPane';
import { CSS } from './fdrStyles';

let turnSeq = 0;

export default function FdrAnalysis() {
  const [segment, setSegment] = useState('query'); // 'query' | 'report' | 'xbrl'

  const [entities, setEntities] = useState([]);
  const [entityState, setEntityState] = useState('loading'); // loading | ready | error
  const [entityError, setEntityError] = useState('');
  const [entityId, setEntityId] = useState('');

  const [unavailable, setUnavailable] = useState('');

  // The XBRL Direct tab's own picker, keyed by doc_id against as_db — a
  // separate database and identity from fs_db's entity_id above. Lifted up
  // here (rather than owned inside XbrlPane) so it renders in this same fixed
  // header row when that tab is active, instead of lower down inside the
  // scrollable panel — which is what previously made the picker appear to
  // jump position when switching to XBRL Direct.
  const [xbrlEntities, setXbrlEntities] = useState([]);
  const [xbrlEntityState, setXbrlEntityState] = useState('loading'); // loading | ready | error
  const [xbrlEntityError, setXbrlEntityError] = useState('');
  const [xbrlDocId, setXbrlDocId] = useState('');

  const loadXbrlEntities = useCallback(async () => {
    setXbrlEntityState('loading');
    setXbrlEntityError('');
    try {
      const rows = await fetchXbrlEntities();
      setXbrlEntities(rows);
      setXbrlEntityState('ready');
    } catch (err) {
      setXbrlEntityError(err.message || String(err));
      setXbrlEntityState('error');
    }
  }, []);

  useEffect(() => {
    loadXbrlEntities();
  }, [loadXbrlEntities]);

  // One thread per entity: switching companies must not carry another company's
  // answers into the new context, and switching back should not have lost them.
  const [threads, setThreads] = useState({});
  const [pending, setPending] = useState(false);
  // Live stage lines for the read currently in flight.
  const [progress, setProgress] = useState([]);
  // Text streamed back while a generated answer is still being written. Only
  // the retrieval path produces it; the deterministic path leaves it empty and
  // the pending bubble shows stages instead.
  const [draft, setDraft] = useState('');

  const turns = threads[entityId] || [];

  const loadEntities = useCallback(async () => {
    setEntityState('loading');
    setEntityError('');
    setUnavailable('');
    try {
      const [health, rows] = await Promise.all([
        fetchHealth().catch(() => null),
        fetchEntities(),
      ]);
      if (health && health.available === false && health.reason) setUnavailable(health.reason);
      setEntities(rows);
      setEntityState('ready');
      if (rows.length === 1) setEntityId(rows[0].entity_id);
    } catch (err) {
      setEntityError(err.message || String(err));
      setEntityState('error');
    }
  }, []);

  useEffect(() => {
    loadEntities();
  }, [loadEntities]);

  const append = useCallback((id, turn) => {
    setThreads((prev) => ({ ...prev, [id]: [...(prev[id] || []), turn] }));
  }, []);

  const send = useCallback(
    async (text) => {
      const target = entityId;
      if (!target) return;
      append(target, { id: `u${(turnSeq += 1)}`, role: 'user', text });
      setProgress([]);
      setDraft('');
      setPending(true);
      try {
        const res = await askQuestionStream(
          { entityId: target, query: text },
          {
            // Progress is dropped if the user has moved to another entity
            // mid-read: showing one company's read under another's thread would
            // be exactly the confusion the shared picker exists to prevent.
            onProgress: (message) =>
              setProgress((prev) => (prev.length > 40 ? prev : [...prev, message])),
            onToken: (piece) => setDraft((prev) => prev + piece),
          }
        );
        append(target, {
          id: `a${(turnSeq += 1)}`,
          role: 'assistant',
          text: res.answer,
          kind: res.kind,
          provenance: res.provenance,
          sources: res.sources,
          intent: res.intent,
        });
      } catch (err) {
        // Surfaced as a turn rather than a toast: the failure belongs next to
        // the question that caused it, and a 503 here carries the operator's
        // actual fix ("set FINANCE_DSN", "install psycopg").
        append(target, {
          id: `e${(turnSeq += 1)}`,
          role: 'assistant',
          kind: 'error',
          text: `**Could not answer.** ${err.message || String(err)}`,
        });
      } finally {
        setPending(false);
        setProgress([]);
        setDraft('');
      }
    },
    [append, entityId]
  );

  // The header is FIXED. It used to collapse on scroll-down and reveal on
  // scroll-up, which is a pattern for long reading surfaces and the wrong one
  // here: this pane scrolls every time an answer arrives, so the bar animated
  // its own height on almost every interaction and the content below it moved
  // with it. Combined with the entity picker growing on selection, that is what
  // read as the whole view flapping. The header also carries the entity in
  // force — the single most important piece of context on the screen — and
  // hiding that while the user reads an answer about it is wrong regardless of
  // the motion.
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [segment]);

  const composerDisabled = pending || !entityId || entityState !== 'ready';
  const placeholder = !entityId
    ? 'Select an entity above to ask a question…'
    : pending
      ? 'Reading the filings…'
      : `Ask about ${entityId.replace(/_/g, ' ')}…`;

  return (
    <div className="fdr-root">
      <style>{CSS}</style>

      <div className="fdr-page">
        <div className="fdr-topbar">
          <div className="fdr-topbar__inner">
            {/* Same label / control / hint structure as the entity field, so the
                two columns share one vertical rhythm and neither can drift when
                the other's contents change. */}
            <div className="fdr-field fdr-field--seg">
              <span className="fdr-field__label">View</span>
              <div className="fdr-seg" role="tablist" aria-label="Diagnostic report view">
                <button
                  type="button"
                  role="tab"
                  aria-selected={segment === 'query'}
                  className={`fdr-seg__btn ${segment === 'query' ? 'is-on' : ''}`}
                  onClick={() => setSegment('query')}
                >
                  {segment === 'query' && <span className="fdr-seg__bg" />}
                  <span className="fdr-seg__text">Query</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={segment === 'report'}
                  className={`fdr-seg__btn ${segment === 'report' ? 'is-on' : ''}`}
                  onClick={() => setSegment('report')}
                >
                  {segment === 'report' && <span className="fdr-seg__bg" />}
                  <span className="fdr-seg__text">Report</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={segment === 'xbrl'}
                  className={`fdr-seg__btn ${segment === 'xbrl' ? 'is-on' : ''}`}
                  onClick={() => setSegment('xbrl')}
                >
                  {segment === 'xbrl' && <span className="fdr-seg__bg" />}
                  <span className="fdr-seg__text">XBRL Direct</span>
                </button>
              </div>
              <span className="fdr-field__hint">&nbsp;</span>
            </div>

            {/* The XBRL tab has its own picker, keyed by doc_id against a different
                database (as_db) — this shared picker is about fs_db's entity_id and
                would be a false choice while looking at that tab, so it swaps for
                XbrlFilingBar instead of disappearing, keeping this row's shape
                the same across every tab. */}
            {segment === 'xbrl' ? (
              <>
                <XbrlFilingBar
                  entities={xbrlEntities}
                  docId={xbrlDocId}
                  onChange={setXbrlDocId}
                  state={xbrlEntityState}
                  error={xbrlEntityError}
                  onRetry={loadXbrlEntities}
                />
                <XbrlDownloadButton docId={xbrlDocId} />
              </>
            ) : (
              <EntityBar
                entities={entities}
                entityId={entityId}
                onChange={setEntityId}
                state={entityState}
                error={entityError}
                onRetry={loadEntities}
              />
            )}
          </div>

          {unavailable ? <div className="fdr-banner">{unavailable}</div> : null}
        </div>

        <div className="fdr-scroll" ref={scrollRef}>
          <div
            id="fdr-panel-query"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'query' ? 'flex' : 'none' }}
          >
            <QueryPane
              turns={turns}
              pending={pending}
              progress={progress}
              draft={draft}
              entityId={entityId}
              onAsk={send}
            />
          </div>

          <div
            id="fdr-panel-report"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'report' ? 'flex' : 'none' }}
          >
            <ReportPane entityId={entityId} />
          </div>

          <div
            id="fdr-panel-xbrl"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'xbrl' ? 'flex' : 'none' }}
          >
            <XbrlPane docId={xbrlDocId} />
          </div>
        </div>

        {segment === 'query' && (
          <QueryComposer onSend={send} disabled={composerDisabled} placeholder={placeholder} />
        )}
      </div>
    </div>
  );
}
