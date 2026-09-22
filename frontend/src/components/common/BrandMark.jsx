import { brandMarks } from '../../config/brand';

/** 24-spoke wheel, drawn originally (not a copy of any official mark). */
export function Wheel({ size = 64, className = '' }) {
  const spokes = Array.from({ length: 24 }, (_, i) => i * 15);
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
      <circle cx="32" cy="32" r="29" />
      <circle cx="32" cy="32" r="5" />
      {spokes.map((a) => (
        <line key={a} x1="32" y1="37" x2="32" y2="61" transform={`rotate(${a} 32 32)`} />
      ))}
    </svg>
  );
}

/**
 * The reserved official-marks slot. Empty by default: renders nothing, so the
 * layout never depends on whether a mark is configured. Marks are rendered as
 * plain images at a fixed height -- no filter, no transform, no animation.
 */
export default function BrandMark({ height = 36 }) {
  const marks = [brandMarks.emblem, brandMarks.cag].filter(Boolean);
  if (marks.length === 0) return null;
  return (
    <span className="brandmarks" style={{ display: 'inline-flex', alignItems: 'center', gap: 14 }}>
      {marks.map((src) => (
        <img key={src} src={src} alt="" style={{ height, width: 'auto', display: 'block' }} />
      ))}
    </span>
  );
}
