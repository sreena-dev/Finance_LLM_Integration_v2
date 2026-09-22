/** Inline 20px stroke icons — keeps the app dependency-free of an icon package. */

const PATHS = {
  ledger: 'M4 4.5A1.5 1.5 0 0 1 5.5 3H16l4 4v12.5A1.5 1.5 0 0 1 18.5 21h-13A1.5 1.5 0 0 1 4 19.5zM15 3v5h5M8 12h8M8 16h5',
  seal: 'M12 3l2.2 1.6 2.7-.3 1 2.5 2.3 1.4-.7 2.6.7 2.6-2.3 1.4-1 2.5-2.7-.3L12 18.6 9.8 17l-2.7.3-1-2.5L3.8 13.4l.7-2.6-.7-2.6 2.3-1.4 1-2.5 2.7.3zM9.5 11.2l1.8 1.8 3.4-3.4',
  scales: 'M12 4v16M7 20h10M5 8h14M5 8l-2.5 6a3 3 0 0 0 5 0zM19 8l2.5 6a3 3 0 0 1-5 0zM12 4.5a1 1 0 1 0 0-.1',
  pulse: 'M3 12h3.5L9 5.5l3.5 13L15.5 12H21',
  send: 'M4.5 12l15.5-7.5-4 15.5-3.9-5.4zM12.1 14.6L20 4.5',
  copy: 'M9 9V5.5A1.5 1.5 0 0 1 10.5 4h8A1.5 1.5 0 0 1 20 5.5v8a1.5 1.5 0 0 1-1.5 1.5H15M4 10.5A1.5 1.5 0 0 1 5.5 9h8a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 18.5z',
  check: 'M4.5 12.5l5 5 10-11',
  chevron: 'M6 9l6 6 6-6',
  alert: 'M12 8.5v5M12 17h.01M10.3 3.9 2.6 17.4a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  info: 'M12 16v-5M12 8h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
  refresh: 'M20 11a8 8 0 1 0-.6 4M20 4v7h-7',
  doc: 'M6 3h8l5 5v13H6zM14 3v5h5M9 13h7M9 17h7',
  search: 'M20 20l-4-4M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0z',
  sparkle: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z',
  download: 'M12 3v12M7.5 10.5L12 15l4.5-4.5M4 20h16',
  upload: 'M12 21V9M7.5 13.5L12 9l4.5 4.5M4 4h16',
  trash: 'M5 7h14M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7M7 7l1 13a1.5 1.5 0 0 0 1.5 1.4h5a1.5 1.5 0 0 0 1.5-1.4l1-13',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5',
  scale: 'M12 4v16M7 20h10M5 8h14M5 8l-2.5 6a3 3 0 0 0 5 0zM19 8l2.5 6a3 3 0 0 1-5 0z',
  shield: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
  // Added for the sign-in / chat-history UI. Additive only — every
  // existing key and path is untouched.
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0',
  logout: 'M15 17l5-5-5-5M20 12H9M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h6',
  plus: 'M12 5v14M5 12h14',
  // Added for the document pane's "view the processed pages" action on a
  // document chip. Additive only — every existing key and path is untouched.
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
};

export default function Icon({ name, size = 20, className = '', strokeWidth = 1.6 }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}
