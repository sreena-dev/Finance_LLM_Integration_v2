import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Icon from './Icon';

export default function CopyButton({ text, label = 'Copy', className = '' }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text || '');
      setCopied(true);
    } catch {
      // Clipboard is unavailable over plain http on some hosts — fall back.
      const ta = document.createElement('textarea');
      ta.value = text || '';
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        setCopied(true);
      } catch {
        /* nothing more we can do */
      }
      document.body.removeChild(ta);
    }
  }

  return (
    <button
      type="button"
      className={`btn btn--ghost btn--sm ${className}`}
      onClick={copy}
      disabled={!text}
      aria-label={copied ? 'Copied' : label}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={copied ? 'done' : 'idle'}
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.14 }}
          style={{ display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <Icon name={copied ? 'check' : 'copy'} size={14} />
          {copied ? 'Copied' : label}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
