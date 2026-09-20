import { useCallback, useEffect, useRef, useState } from 'react';
import Notice from '../common/Notice';
import DocumentChips from './DocumentChips';
import IngestProgress from './IngestProgress';
import {
  fsDeleteDocument,
  fsDocuments,
  fsUpload,
  fsUploadHealth,
  fsWatchUpload,
} from '../../api/client';
import './UploadPanel.css';

/**
 * Owns the upload queue and the documents attached to this conversation.
 *
 * Renders only the *result* — a row of chips, conversion progress, errors. The
 * control that starts an upload lives in the composer and the drop target is
 * the whole chat pane; a permanent full-width dropzone used to sit between the
 * thread and the input, occupying conversation space whether or not anything
 * had ever been uploaded and pushing the composer down the page.
 *
 * `onReady` hands the parent an `accept(files)` function so those two surfaces
 * can push files in without this component rendering either of them.
 *
 * Uploads are converted ONE AT A TIME even when several are dropped together:
 * the ingestion service caps concurrency anyway, and a serial queue means the
 * progress panel describes one document rather than interleaving stages from
 * three and explaining none of them.
 */
export default function UploadPanel({
  mode, conversationId, onConversationChange, onDocumentsChange, onReady, onView,
}) {
  const [health, setHealth] = useState(null);
  const [docs, setDocs] = useState([]);
  const [waiting, setWaiting] = useState(0);
  const [active, setActive] = useState(null);
  const [error, setError] = useState(null);

  // The conversation these uploads belong to, which may not exist yet.
  //
  // Uploading with no conversation is the normal opening move — you attach a
  // statement and then ask about it — so the gateway mints an id and returns
  // it. Held in a ref as well as lifted to the parent because the queue drains
  // file-by-file: the second file must reach the SAME conversation as the
  // first, and a prop update has not necessarily arrived by then.
  const convoRef = useRef(conversationId || null);

  // Switching conversations (including to a brand-new, id-less one) must drop
  // whatever this panel was showing before. This used to be a render-time
  // guard that only updated convoRef.current when the new id was truthy, so
  // navigating to a new chat left convoRef.current — and the docs on screen —
  // pointed at the PREVIOUS conversation: the next upload silently attached
  // itself there too, and that conversation's documents kept showing in a
  // chat that never had them.
  //
  // Guarded against convoRef.current already matching conversationId so this
  // does not fire on the mint-echo: drain() below sets convoRef.current the
  // moment the gateway mints an id, and onConversationChange then feeds that
  // same id back down as a prop a tick later — that echo must not wipe the
  // docs refresh() just populated.
  useEffect(() => {
    const next = conversationId || null;
    if (convoRef.current === next) return;
    convoRef.current = next;
    setDocs([]);
    setError(null);
    onDocumentsChange?.([]);
  }, [conversationId, onDocumentsChange]);

  // Whether this panel is still mounted. Reset to true at the START of the
  // effect body, not merely cleared in cleanup, because StrictMode runs
  // mount → cleanup → mount: a flag only ever set false would stay false after
  // the second mount and silence the panel permanently.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    fsUploadHealth(mode).then((h) => { if (alive) setHealth(h); });
    return () => { alive = false; };
  }, [mode]);

  const refresh = useCallback(async () => {
    const convo = convoRef.current;
    if (!convo) return;
    try {
      const { documents } = await fsDocuments(mode, convo);
      if (!mountedRef.current) return;
      setDocs(documents || []);
      onDocumentsChange?.(documents || []);
    } catch {
      // A listing failure is not worth an error banner: the panel shows what it
      // last knew, and the next upload refreshes it.
    }
  }, [mode, conversationId, onDocumentsChange]);

  useEffect(() => { refresh(); }, [refresh]);

  // ---- the upload queue --------------------------------------------------
  //
  // Driven from the attach/drop handler, NOT from an effect. This was an effect
  // whose cleanup set `cancelled = true`, and under StrictMode's
  // mount → cleanup → mount that cleanup fired on the work it had just started:
  // the upload ran and converted while every progress update and the final
  // refresh were discarded, leaving the panel at "Queued… 0%" forever.
  //
  // An upload is an event, not derived state. Event handlers are not
  // double-invoked, so the class of bug cannot recur here.
  const pendingRef = useRef([]);
  const drainingRef = useRef(false);

  const drain = useCallback(async () => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    try {
      while (pendingRef.current.length > 0) {
        const file = pendingRef.current.shift();
        if (mountedRef.current) {
          setWaiting(pendingRef.current.length);
          setActive({ filename: file.name, stage: 'queued', message: 'Queued…', fraction: 0 });
        }
        try {
          const { job_id: jobId, conversation_id: minted } =
            await fsUpload(mode, file, convoRef.current || '');

          // Adopt the minted conversation BEFORE the next file goes up, or each
          // upload lands in a conversation of its own and they never form one
          // package.
          if (minted && minted !== convoRef.current) {
            convoRef.current = minted;
            onConversationChange?.(minted);
          }

          await fsWatchUpload(mode, jobId, (p) => {
            if (mountedRef.current) {
              setActive({
                filename: file.name, stage: p.stage,
                message: p.message, fraction: p.fraction,
              });
            }
          });
          await refresh();
        } catch (e) {
          if (mountedRef.current) setError(`${file.name}: ${e.message}`);
        } finally {
          if (mountedRef.current) setActive(null);
        }
      }
    } finally {
      drainingRef.current = false;
      if (mountedRef.current) setWaiting(0);
    }
  }, [mode, refresh, onConversationChange]);

  const accept = useCallback((files) => {
    const all = Array.from(files || []);
    const pdfs = all.filter((f) => (f.name || '').toLowerCase().endsWith('.pdf'));
    const rejected = all.length - pdfs.length;
    // Said rather than silently ignored: a dropped .docx that simply vanishes
    // reads as the upload being broken.
    setError(rejected > 0
      ? `${rejected} file(s) skipped — only PDF is supported.`
      : null);
    if (pdfs.length === 0) return;
    pendingRef.current.push(...pdfs);
    setWaiting(pendingRef.current.length);
    drain();
  }, [drain]);

  const remove = useCallback(async (docId) => {
    try {
      await fsDeleteDocument(mode, convoRef.current, docId);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }, [mode, refresh]);

  // Publish the entry point so the composer's attach button and the pane's drop
  // handler can push files in. `busy` lets the composer show its own state.
  const busy = Boolean(active);
  const available = health ? Boolean(health.available) : true;
  useEffect(() => {
    onReady?.({ accept, busy, available });
  }, [onReady, accept, busy, available]);

  if (health && !health.available) {
    return (
      <div className="uploadpanel">
        <Notice tone="warn" title="Document upload is unavailable">
          {health.reason}
        </Notice>
      </div>
    );
  }

  // Nothing uploaded and nothing happening: render nothing at all. The attach
  // control lives in the composer, so there is no reason to occupy the
  // conversation area with an empty panel.
  if (docs.length === 0 && !active && !error && waiting === 0) return null;

  return (
    <div className="uploadpanel">
      {active && <IngestProgress {...active} />}

      {waiting > 0 && (
        <p className="uploadpanel__queued">
          {waiting} more waiting — documents are converted one at a time.
        </p>
      )}

      {error && <Notice tone="error" title="Upload failed">{error}</Notice>}

      <DocumentChips docs={docs} onDelete={remove} onView={onView} />
    </div>
  );
}
