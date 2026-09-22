import { useCallback, useRef, useState } from 'react';
import Icon from './Icon';
import './Dropzone.css';

/**
 * A file drop target with a click-to-browse fallback.
 *
 * There is no drag-and-drop anywhere else in this app — Trial Balance's four
 * upload sites are all hidden `<input type="file">` behind a label — so this is
 * new rather than extracted. It is in `common/` because those four would all be
 * better for it.
 *
 * The internals follow trial-balance/GroupingUpload.jsx: local `busy`/`error`
 * state, and `event.target.value = ''` after every pick so that choosing the
 * SAME file twice still fires `onChange`. Without that reset a user who fixes a
 * file and re-picks it sees nothing happen at all.
 *
 * `dragDepth` is a counter, not a boolean. `dragenter`/`dragleave` fire for
 * every child element the pointer crosses, so a boolean flickers off the moment
 * the cursor moves over the icon inside the zone.
 */
export default function Dropzone({
  onFiles,
  accept = '.pdf',
  multiple = true,
  disabled = false,
  busy = false,
  hint = 'PDF only',
  label = 'Drop financial statements here',
}) {
  const inputRef = useRef(null);
  const [dragDepth, setDragDepth] = useState(0);
  const [error, setError] = useState(null);

  const accepts = useCallback(
    (file) => {
      const patterns = accept.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
      if (patterns.length === 0) return true;
      const name = (file.name || '').toLowerCase();
      return patterns.some((p) => (p.startsWith('.') ? name.endsWith(p) : file.type === p));
    },
    [accept],
  );

  const hand = useCallback(
    (fileList) => {
      const files = Array.from(fileList || []);
      if (files.length === 0) return;
      const ok = files.filter(accepts);
      const rejected = files.filter((f) => !accepts(f));
      setError(
        rejected.length
          ? `Not accepted: ${rejected.map((f) => f.name).join(', ')}. ${hint}.`
          : null,
      );
      if (ok.length) onFiles?.(multiple ? ok : [ok[0]]);
    },
    [accepts, hint, multiple, onFiles],
  );

  const inert = disabled || busy;

  return (
    <div className="dropzone-wrap">
      <div
        className={`dropzone ${dragDepth > 0 ? 'is-over' : ''} ${inert ? 'is-disabled' : ''}`}
        role="button"
        tabIndex={inert ? -1 : 0}
        aria-disabled={inert}
        onClick={() => !inert && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (inert) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          if (!inert) setDragDepth((d) => d + 1);
        }}
        onDragOver={(e) => {
          // Without preventDefault the browser navigates to the dropped file
          // instead of handing it over, which looks like the app crashing.
          e.preventDefault();
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setDragDepth((d) => Math.max(0, d - 1));
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragDepth(0);
          if (!inert) hand(e.dataTransfer?.files);
        }}
      >
        <Icon name="upload" size={20} className="dropzone__icon" />
        <span className="dropzone__label">{busy ? 'Reading…' : label}</span>
        <span className="dropzone__hint">
          {busy ? 'One document at a time' : `${hint} · click or drop`}
        </span>

        <input
          ref={inputRef}
          type="file"
          className="dropzone__input"
          accept={accept}
          multiple={multiple}
          disabled={inert}
          onChange={(e) => {
            hand(e.target.files);
            // Reset so re-picking the same filename fires onChange again.
            e.target.value = '';
          }}
        />
      </div>

      {error && <p className="dropzone__error">{error}</p>}
    </div>
  );
}
