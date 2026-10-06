/**
 * Financial Diagnostic Report — UI.
 *
 * SCOPE: this file plus `api.js` are the entire FDR UI; nothing else in the
 * codebase is touched by them. One page, one scroll surface. A segmented
 * control (Query / XBRL Direct) sits in a fixed bar above the scrollable
 * panel.
 *
 * This mode answers only from as_db (parsed MCA XBRL filings) — see
 * `XbrlPane` in `FdrXbrlPane.jsx`, which has its own filing picker keyed by
 * `doc_id` since as_db is a different database with a different identity.
 * The Query tab is a stated placeholder: free-text question answering over
 * this data is not built yet, and the tab says so rather than failing
 * silently or against a removed backend path.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchXbrlEntities } from './api';
import { XbrlDownloadButton, XbrlFilingBar, XbrlPane } from './FdrXbrlPane';
import { XbrlThresholdButton } from './FdrThresholds';
import { CSS } from './fdrStyles';

function QueryComingSoon() {
  return (
    <div className="fdr-empty fdr-empty--grow">
      <p className="fdr-empty__text">
        Free-text question answering for this mode is still under development
        and is not yet available. In the meantime, use the <strong>XBRL
        Direct</strong> tab to review a filing&rsquo;s figures, business
        profile and risk diagnostics.
      </p>
    </div>
  );
}

export default function FdrAnalysis() {
  const [segment, setSegment] = useState('query'); // 'query' | 'xbrl'

  // The XBRL Direct tab's own picker, keyed by doc_id against as_db.
  const [xbrlEntities, setXbrlEntities] = useState([]);
  const [xbrlEntityState, setXbrlEntityState] = useState('loading'); // loading | ready | error
  const [xbrlEntityError, setXbrlEntityError] = useState('');
  const [xbrlDocId, setXbrlDocId] = useState('');
  // Bumped when thresholds are saved: the blocks refetch so the report reflects them.
  const [thresholdsVersion, setThresholdsVersion] = useState(0);

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

  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [segment]);

  return (
    <div className="fdr-root">
      <style>{CSS}</style>

      <div className="fdr-page">
        <div className="fdr-topbar">
          <div className="fdr-topbar__inner">
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
                <XbrlThresholdButton onApplied={() => setThresholdsVersion((n) => n + 1)} />
                <XbrlDownloadButton docId={xbrlDocId} />
              </>
            ) : null}
          </div>
        </div>

        <div className="fdr-scroll" ref={scrollRef}>
          <div
            id="fdr-panel-query"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'query' ? 'flex' : 'none' }}
          >
            <QueryComingSoon />
          </div>

          <div
            id="fdr-panel-xbrl"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'xbrl' ? 'flex' : 'none' }}
          >
            <XbrlPane docId={xbrlDocId} thresholdsVersion={thresholdsVersion} />
          </div>
        </div>
      </div>
    </div>
  );
}
