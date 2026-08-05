import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { tbListDocuments } from '../../api/client';
import Icon from '../common/Icon';
import DocumentPicker from './DocumentPicker';
import ColumnMapper from './ColumnMapper';
import AskPanel from './AskPanel';
import AuditPanel from './AuditPanel';
import ValidatePanel from './ValidatePanel';
import './TrialBalanceView.css';

const TABS = [
  { id: 'ask', label: 'Ask', icon: 'sparkle' },
  { id: 'audit', label: 'Audit', icon: 'shield' },
  { id: 'validate', label: 'Validate', icon: 'scale' },
];

/**
 * Trial Balance mode: upload once, then run any of three independent
 * sub-features (ask / audit / validate) against the resulting doc_id(s).
 *
 * A session id is minted once per mount and reused for every `ask` call —
 * the backend keys conversation memory by (session_id, doc_id), so one id is
 * enough to keep follow-ups coherent per document without any extra state.
 */
export default function TrialBalanceView({ mode, state, setState }) {
  const { documents, docsError, currentId, priorId, tab, threads } = state;
  const [loadingDocs, setLoadingDocs] = useState(documents === null);
  const [pendingUpload, setPendingUpload] = useState(null); // needs-mapping payload
  const sessionId = useRef(
    (crypto.randomUUID && crypto.randomUUID()) || `sess-${Date.now()}-${Math.random()}`
  ).current;

  const patch = (fields) => setState((prev) => ({ ...prev, ...fields }));

  useEffect(() => {
    if (documents !== null) return;
    let cancelled = false;
    (async () => {
      setLoadingDocs(true);
      try {
        const docs = await tbListDocuments(mode);
        if (!cancelled) patch({ documents: docs, docsError: null });
      } catch (err) {
        if (!cancelled) patch({ documents: [], docsError: err.message });
      } finally {
        if (!cancelled) setLoadingDocs(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.id]);

  const currentDoc = useMemo(
    () => (documents || []).find((d) => d.doc_id === currentId) || null,
    [documents, currentId]
  );
  const priorDoc = useMemo(
    () => (documents || []).find((d) => d.doc_id === priorId) || null,
    [documents, priorId]
  );

  function refreshAfterUpload(info, selectAsCurrent = true) {
    patch({
      documents: [
        { doc_id: info.doc_id, filename: info.filename, sheet: info.sheet, periods: info.periods, uploaded_at: new Date().toISOString() },
        ...(documents || []).filter((d) => d.doc_id !== info.doc_id),
      ],
      ...(selectAsCurrent ? { currentId: info.doc_id } : {}),
    });
    setPendingUpload(null);
  }

  function handleDeleted(docId) {
    patch({
      documents: (documents || []).filter((d) => d.doc_id !== docId),
      currentId: currentId === docId ? null : currentId,
      priorId: priorId === docId ? null : priorId,
    });
  }

  const threadFor = (docId) => threads?.[docId] || [];
  const setThreadFor = (docId) => (updater) =>
    setState((prev) => {
      const cur = prev.threads?.[docId] || [];
      const next = typeof updater === 'function' ? updater(cur) : updater;
      return { ...prev, threads: { ...prev.threads, [docId]: next } };
    });

  return (
    <div className="tb">
      <div className="tb__top">
        <div className="tb__top-inner">
          <DocumentPicker
            mode={mode}
            documents={documents}
            loading={loadingDocs}
            error={docsError}
            currentId={currentId}
            priorId={priorId}
            onSelectCurrent={(id) => patch({ currentId: id, priorId: priorId === id ? null : priorId })}
            onSelectPrior={(id) => patch({ priorId: id })}
            onUploaded={(info) => refreshAfterUpload(info)}
            onDeleted={handleDeleted}
            onNeedsMapping={(payload) => setPendingUpload(payload)}
          />

          {currentDoc && (
            <div className="tb__tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  className={`tb__tab ${tab === t.id ? 'is-on' : ''}`}
                  onClick={() => patch({ tab: t.id })}
                >
                  {tab === t.id && (
                    <motion.span
                      className="tb__tab-bg"
                      layoutId="tb-tab-active"
                      transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                    />
                  )}
                  <Icon name={t.icon} size={14} className="tb__tab-icon" />
                  <span className="tb__tab-text">{t.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {currentDoc && (
        <div className="tb__panel">
          {tab === 'ask' && (
            <AskPanel
              mode={mode}
              doc={currentDoc}
              sessionId={sessionId}
              thread={threadFor(currentDoc.doc_id)}
              setThread={setThreadFor(currentDoc.doc_id)}
            />
          )}
          {tab === 'audit' && <AuditPanel mode={mode} doc={currentDoc} priorDoc={priorDoc} />}
          {tab === 'validate' && <ValidatePanel mode={mode} doc={currentDoc} priorDoc={priorDoc} />}
        </div>
      )}

      {pendingUpload && (
        <ColumnMapper
          mode={mode}
          pending={pendingUpload}
          onDone={(info) => refreshAfterUpload(info)}
          onCancel={() => setPendingUpload(null)}
        />
      )}
    </div>
  );
}
