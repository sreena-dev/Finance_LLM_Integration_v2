import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import './Composer.css';

const MAX_HEIGHT = 190;

export default function Composer({ onSubmit, disabled, placeholder }) {
  const [value, setValue] = useState('');
  const ref = useRef(null);

  // Grow with the content, then scroll internally past MAX_HEIGHT.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);

  function send() {
    if (!value.trim() || disabled) return;
    onSubmit(value);
    setValue('');
  }

  function onKeyDown(e) {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const canSend = value.trim().length > 0 && !disabled;

  return (
    <div className="composer">
      <div className={`composer__box ${disabled ? 'is-disabled' : ''}`}>
        <textarea
          ref={ref}
          className="composer__input"
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          aria-label="Your question"
        />

        <motion.button
          type="button"
          className="composer__send"
          onClick={send}
          disabled={!canSend}
          whileHover={canSend ? { scale: 1.05 } : {}}
          whileTap={canSend ? { scale: 0.94 } : {}}
          transition={{ type: 'spring', stiffness: 500, damping: 26 }}
          aria-label="Send"
        >
          <Icon name="send" size={17} />
        </motion.button>
      </div>

      <p className="composer__hint">
        <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
      </p>
    </div>
  );
}
