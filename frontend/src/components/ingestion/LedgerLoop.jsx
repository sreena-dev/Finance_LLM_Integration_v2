import './LedgerLoop.css';

/**
 * The waiting animation: a 14 s loop in four scenes -- the ledger opens and a
 * page turns, a rupee mark settles into the amount column, a pair of scales tips
 * and balances, and a tick confirms the subtotal foots. Drawn originally in
 * SVG; it carries no official mark (those live in the reserved slot beside the
 * wait bar and are never animated).
 *
 * Under `prefers-reduced-motion` it is a single static frame: the tick.
 */
export default function LedgerLoop({ size = 200 }) {
  return (
    <svg className="loop" width={size} height={size * 0.62} viewBox="0 0 200 124"
         role="img" aria-label="Checking the arithmetic of your statement" fill="none">
      <ellipse cx="100" cy="112" rx="70" ry="5" className="loop__shadow" />

      {/* Scene 1: ledger opens, a page turns */}
      <g className="loop__scene loop__s1">
        <rect x="34" y="18" width="132" height="84" rx="4" className="loop__paper" />
        <line x1="100" y1="18" x2="100" y2="102" className="loop__ink" />
        {[32, 44, 56, 68, 80].map((y) => (
          <line key={y} x1="42" y1={y} x2="92" y2={y} className="loop__rule" />
        ))}
        {[32, 56, 80].map((y) => (
          <line key={y} x1="146" y1={y} x2="158" y2={y} className="loop__figure" />
        ))}
        <g className="loop__flap">
          <rect x="100" y="18" width="66" height="84" rx="2" className="loop__flappaper" />
        </g>
      </g>

      {/* Scene 2: a rupee mark descends and settles */}
      <g className="loop__scene loop__s2">
        <rect x="34" y="18" width="132" height="84" rx="4" className="loop__paper" />
        <line x1="100" y1="18" x2="100" y2="102" className="loop__ink" />
        {[32, 44, 56, 68, 80].map((y) => (
          <line key={y} x1="42" y1={y} x2="92" y2={y} className="loop__rule" />
        ))}
        <line x1="150" y1="30" x2="150" y2="86" className="loop__gold" />
        <g className="loop__coin">
          <circle cx="132" cy="58" r="10" className="loop__coinface" />
          <text x="132" y="63" textAnchor="middle" className="loop__rupee">₹</text>
        </g>
      </g>

      {/* Scene 3: the scales tip, overshoot and balance */}
      <g className="loop__scene loop__s3">
        <line x1="100" y1="34" x2="100" y2="100" className="loop__ink" />
        <line x1="78" y1="102" x2="122" y2="102" className="loop__ink" />
        <g className="loop__beam">
          <line x1="56" y1="40" x2="144" y2="40" className="loop__ink" />
          <path d="M56 40l-12 26h24z" className="loop__pan" />
          <path d="M144 40l-12 26h24z" className="loop__pan" />
          <circle cx="56" cy="62" r="6" className="loop__coinface" />
          <text x="56" y="65" textAnchor="middle" className="loop__rupee loop__rupee--s">₹</text>
          <line x1="138" y1="60" x2="150" y2="60" className="loop__figure" />
        </g>
      </g>

      {/* Scene 4: the subtotal foots. Also the reduced-motion still. */}
      <g className="loop__scene loop__s4">
        <rect x="52" y="20" width="96" height="70" rx="4" className="loop__paper" />
        {[34, 46].map((y) => (
          <g key={y}>
            <line x1="62" y1={y} x2="96" y2={y} className="loop__rule" />
            <line x1="118" y1={y} x2="138" y2={y} className="loop__figure" />
          </g>
        ))}
        <line x1="62" y1="62" x2="138" y2="62" className="loop__ink" />
        <line x1="62" y1="66" x2="138" y2="66" className="loop__ink" />
        <line x1="62" y1="78" x2="92" y2="78" className="loop__ink loop__bold" />
        <line x1="112" y1="78" x2="138" y2="78" className="loop__ink loop__bold" />
        <g className="loop__tick">
          <circle cx="138" cy="86" r="14" className="loop__ok" />
          <path d="M131 86l5 5 9-11" className="loop__okmark" />
        </g>
      </g>
    </svg>
  );
}
