import { FIGURE_LABELS, parseFigureCell } from '../../lib/figures';

/**
 * One figure, in whichever of the four states it is in. Used by read-only
 * tables (answer cards, the document pane with editing off) so a withheld figure
 * never reads as a blank or a zero, and a figure a person typed is always
 * labelled as theirs. A plain figure is returned exactly as printed.
 */
export default function FigureCell({ text }) {
  const { kind, value } = parseFigureCell(text);
  if (kind === 'plain') return <>{text}</>;
  return (
    <span className={`fig fig--${kind}`} title={
      kind === 'unreadable' ? 'Withheld: the scan could not be read'
        : kind === 'recovered' ? 'A second read of the scan; not confirmed by the arithmetic'
          : 'Entered by a person from the scan'
    }>
      <span className="fig__label">{FIGURE_LABELS[kind]}</span>
      {value && <span>{value}</span>}
    </span>
  );
}
