import { Component } from 'react';
import Notice from './Notice';

/**
 * Contains a render error to one pane instead of blanking the application.
 *
 * There was no boundary anywhere in this app, so any exception thrown during
 * render unmounted the whole tree and left a white page with the cause visible
 * only in the console. That is exactly what happened here: `AnswerCard`
 * referenced an identifier that a bad patch had removed from its destructuring,
 * every assistant message threw `ReferenceError`, and the symptom reported was
 * "blank screen" — which says nothing about where to look.
 *
 * This does NOT hide the error. It shows it, keeps the rest of the app usable,
 * and still logs to the console with the component stack. A boundary that
 * swallowed the message would be worse than the blank page, because at least a
 * blank page is obviously broken.
 *
 * Class component because `componentDidCatch` has no hook equivalent; React
 * offers no other way to catch a render error.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', this.props.label || 'render error', error, info);
    this.setState({ info });
  }

  componentDidUpdate(prev) {
    // Recover when the caller swaps what is being rendered — switching
    // conversation or mode should not leave a stale error on screen forever.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null, info: null });
    }
  }

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <Notice tone="error" title={this.props.label || 'Something failed to render'}>
        <p style={{ margin: '0 0 6px' }}>
          {String(error.message || error)}
        </p>
        <p style={{ margin: 0, fontSize: '11.5px', opacity: 0.8 }}>
          The rest of the app is still usable. The full stack is in the browser
          console.
        </p>
        {info?.componentStack && (
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: 'pointer', fontSize: '11.5px' }}>
              Component stack
            </summary>
            <pre style={{
              margin: '6px 0 0',
              maxHeight: 180,
              overflow: 'auto',
              fontSize: '10.5px',
              whiteSpace: 'pre-wrap',
            }}>
              {info.componentStack.trim()}
            </pre>
          </details>
        )}
      </Notice>
    );
  }
}
