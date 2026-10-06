/**
 * Scoped styles for the FDR mode, mirroring src/styles/index.css tokens.
 * Split out of `FdrAnalysis.jsx` purely to shrink that file; pure string, no behavior.
 */
export const CSS = `
.fdr-root{
  /* Matched to the app's shared design tokens (src/styles/index.css) so this
     module's colors, borders and type read as one continuous surface with
     the shared header/sidebar rather than a visibly different sub-app —
     values are copied in, not var()-inherited, so FDR stays a self-contained
     stylesheet that only this file needs to be edited to keep in step. */
  --navy-900:#0b1836; --navy-800:#0f2049; --navy-700:#14285a; --navy-600:#1f3a7a;
  --navy-100:#e9edf7; --navy-50:#f3f5fb;
  --ink-900:#1b2333; --ink-800:#232c3f; --ink-700:#465063; --ink-600:#556079;
  --ink-500:#5f6883; --ink-400:#69708a; --ink-300:#d3ccbb; --ink-200:#e5e0d3;
  --ink-100:#f3f0e7; --ink-50:#f8f6ef; --white:#fff;
  --bg:#faf8f3; --surface:#fff; --border:#e5e0d3; --border-strong:#d3ccbb;
  --amber-50:#fcf1da; --amber-600:#8f5200; --amber-border:#e8d19b;
  --red-50:#fbe9e7; --red-600:#b42318; --red-border:#f0c4be;
  --ok-50:#e3f3ea; --ok-600:#1e7a4c; --ok-border:#bfe0cd;
  --indigo-50:#e9edf7; --indigo-600:#14285a; --indigo-border:#c7d0e6;
  --font-sans:"Noto Sans","Noto Sans Devanagari",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --font-mono:"IBM Plex Mono","SF Mono","Cascadia Code",Consolas,monospace;
  --shadow-xs:0 1px 2px rgba(27,35,51,.06);
  --shadow-sm:0 1px 2px rgba(27,35,51,.06);
  --shadow-md:0 4px 14px rgba(27,35,51,.08);
  --radius-sm:6px; --radius:8px; --radius-lg:10px; --radius-xl:14px;
  --ease:cubic-bezier(.22,1,.36,1);
  position:absolute; inset:0; background:var(--bg); color:var(--ink-800);
  font-family:var(--font-sans); font-size:14px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.fdr-root *{box-sizing:border-box;}
.fdr-root button,.fdr-root select,.fdr-root textarea{font:inherit;color:inherit;}
.fdr-root :focus-visible{outline:2px solid var(--navy-600); outline-offset:2px; border-radius:4px;}

.fdr-page{position:absolute; inset:0; display:flex; flex-direction:column; min-height:0;}

/* Fixed height, no collapse, no transition. Nothing in this bar may move in
   response to scrolling or to a selection — see the note in the component. */
.fdr-topbar{
  /* Background matches the shared .head bar in App.css (--bg is now the same
     warm paper tone) so this bar's border-bottom reads as a continuation of
     that header's rule, not a second, slightly offset hairline under it. */
  flex:none; background:var(--bg); border-bottom:1px solid var(--border);
}
.fdr-topbar__inner{display:flex; align-items:flex-start; gap:20px; flex-wrap:wrap; padding:10px 32px 12px;}

/* One control height across the whole bar (segmented toggle, picker, Thresholds, Download). */
.fdr-root{--fdr-ctl-h:40px;}
.fdr-seg{display:inline-flex; align-items:stretch; gap:3px; padding:3px; height:var(--fdr-ctl-h); background:var(--ink-100); border-radius:var(--radius);}
/* Compounded with the parent .fdr-seg on purpose: the generic
   ".fdr-root button{color:inherit}" reset below is a type+class selector,
   which outranks a bare ".fdr-seg__btn" class selector on specificity alone
   — the inherited ink-800 would win over this color regardless of source
   order. Same reasoning applies to every other button/select color rule in
   this sheet, which is why each one is compounded with its parent's class. */
.fdr-seg .fdr-seg__btn{position:relative; display:inline-flex; align-items:center; padding:0 20px; background:none; border:none;
  border-radius:var(--radius-sm); font-size:13px; font-weight:550; color:var(--ink-600);
  cursor:pointer; transition:color .18s var(--ease);}
.fdr-seg__btn.is-on{color:var(--navy-900);}
.fdr-seg__bg{position:absolute; inset:0; background:var(--surface); border-radius:var(--radius-sm); box-shadow:var(--shadow-sm);}
.fdr-seg__text{position:relative; z-index:1;}

.fdr-entitybar{display:flex; align-items:flex-end; gap:12px; flex:0 1 420px; min-width:260px; max-width:460px;}
.fdr-entitybar--error{align-items:center;}
.fdr-entitybar__err{flex:1; font-size:12.5px; color:var(--red-600);}
.fdr-banner{padding:8px 24px; background:var(--amber-50); color:var(--amber-600);
  font-size:12.5px; border-top:1px solid var(--border);}

.fdr-field{display:flex; flex-direction:column; gap:5px; flex:1 1 240px; min-width:200px;}
.fdr-field--seg{flex:0 0 auto; min-width:0;}
.fdr-field__label{font-size:11.5px; font-weight:600; letter-spacing:.045em; text-transform:uppercase; color:var(--ink-500);}
/* Fixed height and always present: the hint appearing on selection is what used
   to push the header down and knock the Query/Report control out of line. */
.fdr-field__hint{display:block; min-height:16px; font-size:11.5px; line-height:16px;
  color:var(--ink-400); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.fdr-field .fdr-select{appearance:none; -webkit-appearance:none; width:100%; height:var(--fdr-ctl-h); padding:0 32px 0 12px;
  background:var(--surface)
    url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2397a3b0' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><path d='M6 9l6 6 6-6'/></svg>")
    no-repeat right 10px center;
  border:1px solid var(--border-strong); border-radius:var(--radius);
  font-size:13.5px; color:var(--ink-900); cursor:pointer;}
.fdr-select:disabled{background-color:var(--ink-50); color:var(--ink-400); opacity:1; cursor:not-allowed;}

.fdr-btn{display:inline-flex; align-items:center; justify-content:center; gap:8px;
  padding:9px 18px; border:1px solid transparent; border-radius:var(--radius);
  font-size:13.5px; font-weight:550; cursor:pointer;
  transition:background .18s var(--ease),box-shadow .18s var(--ease);}
.fdr-btn:disabled{cursor:not-allowed; opacity:.55;}
/* Weight and colour carry the emphasis here, NOT size. The stack sets the
   height of the whole controls card, so buying prominence with padding would
   cost vertical space on every screen the report is read on. */
.fdr-btn.fdr-btn--primary{background:var(--navy-700); color:var(--white); box-shadow:var(--shadow-sm);
  padding:8px 18px; font-size:13.5px; font-weight:650; letter-spacing:.01em;}
.fdr-btn.fdr-btn--primary:hover:not(:disabled){background:var(--navy-800); box-shadow:var(--shadow-md);}
.fdr-btn.fdr-btn--ghost{background:var(--surface); border-color:var(--border-strong); color:var(--ink-700);}
/* Download is the follow-up to a build, not a peer of it — smaller, so the
   pair reads as one primary action with a secondary underneath. */
.fdr-report__actions .fdr-btn--ghost{padding:6px 18px; font-size:12.5px;}

/* The XBRL tab's own download control, docked to the right of the topbar row
   rather than stretched full-width by the shared .fdr-field column layout. */
.fdr-field.fdr-xbrl-dl{flex:0 0 auto; min-width:0; margin-left:auto; align-items:flex-end;}
.fdr-xbrl-dl .fdr-btn--primary{padding:0 20px; font-size:13.5px; height:var(--fdr-ctl-h);}
.fdr-xbrl-dl__err{color:var(--red-600); text-align:right;}

.fdr-scroll{flex:1; min-height:0; overflow-y:auto;}
.fdr-panel{min-height:100%; display:flex; flex-direction:column; padding:14px 24px 20px;}

/* Conversation */
.fdr-log{display:flex; flex-direction:column; gap:16px; width:100%; max-width:820px; margin:0 auto;}
.fdr-turn{display:flex;}
.fdr-turn--user{justify-content:flex-end;}
.fdr-bubble{max-width:100%; padding:14px 18px; background:var(--surface);
  border:1px solid var(--border); border-radius:var(--radius-lg); box-shadow:var(--shadow-xs);}
.fdr-bubble--user{max-width:78%; background:var(--navy-700); color:var(--white); border-color:transparent;}
.fdr-bubble--note{background:var(--amber-50); border-color:var(--amber-border);}
.fdr-bubble--error{background:var(--red-50); border-color:var(--red-border);}
.fdr-bubble--pending{display:flex; flex-direction:column; gap:3px; color:var(--ink-500); font-size:12.5px;}
/* Stage lines fade back as they are superseded, so the newest reads as current
   without the bubble needing to move or re-order. */
.fdr-stage{opacity:.45; font-family:var(--font-mono); font-size:11.5px; line-height:1.7;}
.fdr-stage.is-current{opacity:1; color:var(--ink-700);}
.fdr-md--draft{color:var(--ink-800); font-size:14px;}
.fdr-md--draft::after{content:"▌"; color:var(--navy-600); animation:fdr-blink 1s steps(2) infinite;}
@keyframes fdr-blink{50%{opacity:0;}}

/* Sources drawer */
.fdr-src{margin-top:12px; border-top:1px solid var(--border); padding-top:9px;}
.fdr-src__summary{cursor:pointer; font-size:11.5px; font-weight:600; color:var(--navy-700); list-style:none;}
.fdr-src__summary::-webkit-details-marker{display:none;}
.fdr-src__summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-src[open] .fdr-src__summary::before{content:"▾ ";}
.fdr-src__list{margin:10px 0 0; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px;}
.fdr-src__item{padding:9px 11px; background:var(--ink-50); border:1px solid var(--border);
  border-left:3px solid var(--ink-300); border-radius:var(--radius-sm);}
.fdr-src__item.is-cited{border-left-color:var(--navy-600); background:var(--navy-50);}
.fdr-src__head{display:flex; align-items:baseline; gap:7px;}
.fdr-src__n{font-family:var(--font-mono); font-size:11.5px; font-weight:700; color:var(--navy-700);}
.fdr-src__title{flex:1; font-size:12.5px; font-weight:600; color:var(--ink-800);}
.fdr-src__badge{padding:1px 7px; border-radius:100px; background:var(--navy-600); color:var(--white);
  font-size:9.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;}
.fdr-src__meta{margin-top:3px; font-family:var(--font-mono); font-size:10.5px; color:var(--ink-500);}
.fdr-src__excerpt{margin:6px 0 0; font-size:11.5px; line-height:1.55; color:var(--ink-600);
  max-height:76px; overflow:hidden;}

.fdr-kind{display:inline-block; margin-bottom:8px; padding:2px 8px; border-radius:100px;
  background:var(--ink-100); color:var(--ink-600); font-size:10.5px; font-weight:650;
  letter-spacing:.05em; text-transform:uppercase;}

.fdr-md > :first-child{margin-top:0;}
.fdr-md > :last-child{margin-bottom:0;}
.fdr-md p{margin:0 0 10px;}
.fdr-md ul,.fdr-md ol{margin:0 0 10px; padding-left:20px;}
.fdr-md li{margin:3px 0;}
.fdr-md code{padding:1px 5px; background:var(--ink-100); border-radius:4px;
  font-family:var(--font-mono); font-size:12.5px;}
.fdr-md strong{color:var(--ink-900); font-weight:650;}
.fdr-md em{color:var(--ink-600);}
.fdr-md table{border-collapse:collapse; width:100%; margin:0 0 10px; font-size:13px;}
.fdr-md th,.fdr-md td{border:1px solid var(--border); padding:6px 9px; text-align:left;}

/* Provenance */
.fdr-prov{margin-top:12px; border-top:1px solid var(--border); padding-top:9px;}
.fdr-prov__summary{cursor:pointer; font-size:11.5px; color:var(--ink-500); list-style:none;}
.fdr-prov__summary::-webkit-details-marker{display:none;}
.fdr-prov__summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-prov[open] .fdr-prov__summary::before{content:"▾ ";}
.fdr-prov__grid{display:grid; grid-template-columns:auto 1fr; gap:4px 14px; margin:10px 0 0;
  font-size:11.5px; color:var(--ink-600);}
.fdr-prov__grid dt{color:var(--ink-400); font-weight:600;}
.fdr-prov__grid dd{margin:0; font-family:var(--font-mono); font-size:11px; word-break:break-word;}

/* Intro — the suggestions are buttons that ask the question, not decoration. */
.fdr-intro{width:100%; max-width:820px; margin:0 auto; padding:8px 0;}
.fdr-intro__lead{margin:0 0 12px; font-size:11.5px; font-weight:600;
  letter-spacing:.045em; text-transform:uppercase; color:var(--ink-500);}
.fdr-intro__lead strong{color:var(--ink-700); font-weight:700;}
.fdr-intro__list{display:flex; flex-wrap:wrap; gap:8px;}
.fdr-chip{padding:8px 14px; background:var(--surface); border:1px solid var(--border-strong);
  border-radius:100px; font-size:12.5px; color:var(--ink-700); cursor:pointer; text-align:left;
  transition:background .16s var(--ease), border-color .16s var(--ease), color .16s var(--ease);}
.fdr-chip:hover{background:var(--navy-50); border-color:var(--navy-600); color:var(--navy-800);}
.fdr-chip:active{background:var(--navy-100);}

/* Report blocks — the Report view fills the page rather than sitting in a
   centred column: the audit-planning matrix (block 7) and the two-column
   health/business-profile grids read far better with the width a report is
   actually given than squeezed into a chat-width column. The Query view's
   conversation log keeps its own narrower, chat-appropriate width below. */
.fdr-report__blocks{display:flex; flex-direction:column; gap:14px; width:100%;
  padding-bottom:8px;}
.fdr-report__error{width:100%; padding:12px 16px;
  background:var(--red-50); border:1px solid var(--red-border); border-radius:var(--radius);
  font-size:12.5px; color:var(--red-600);}
.fdr-report__upcoming{width:100%; padding:14px 18px; border:1px dashed var(--border-strong);
  border-radius:var(--radius-lg); font-size:12.5px; color:var(--ink-500);}

.fdr-blk{background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-lg); box-shadow:var(--shadow-xs); overflow:hidden;}
/* A section still being built is drawn, not omitted: the reader has to be able
   to tell "still coming" from "finished, and this is all there is". */
.fdr-blk.is-pending{background:var(--ink-50); box-shadow:none;}
.fdr-blk__head{display:flex; align-items:center; gap:10px; padding:14px 18px;}
.fdr-blk:not(.is-pending) .fdr-blk__head{border-bottom:1px solid var(--border);}
.fdr-blk__num{font-family:var(--font-mono); font-size:11px; color:var(--ink-400);
  background:var(--ink-100); padding:2px 7px; border-radius:5px;}
.fdr-blk__title{flex:1; margin:0; font-size:14.5px; font-weight:650; color:var(--ink-900);}
.fdr-blk.is-pending .fdr-blk__title{color:var(--ink-400);}
.fdr-blk__state{font-size:11.5px; color:var(--ink-400);}
.fdr-blk__body{padding:16px 18px;}
.fdr-blk__error{margin:0; font-size:12.5px; color:var(--red-600);}
.fdr-blk__fallback{margin:0; font-size:12.5px; color:var(--ink-500);}

/* Block 1 — the classification leads, because it decides whether anything
   later in the report may be interpreted at all. */
.fdr-cov__lead{padding:14px 16px; border-radius:var(--radius); background:var(--navy-50);
  border:1px solid var(--navy-100);}
.fdr-cov__lead.is-open{background:var(--amber-50); border-color:var(--amber-border);}
/* Partial: the legal form was read (progress), the business model was not
   (still incomplete) — a third visual state between "resolved" and "empty". */
.fdr-cov__lead.is-partial{background:var(--navy-50); border-color:var(--navy-100);
  border-left:3px solid var(--navy-600);}
.fdr-cov__leadhead{display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
  margin-bottom:6px;}
.fdr-cov__leadlabel{font-size:11px; font-weight:650; letter-spacing:.05em;
  text-transform:uppercase; color:var(--ink-500);}
.fdr-cov__leadvalue{font-size:15px; font-weight:650; color:var(--ink-900);}
.fdr-cov__leaddetail{margin:0; font-size:13px; line-height:1.6; color:var(--ink-700);}
.fdr-cov__quote--onlead{margin-top:9px;}
.fdr-cov__quote--onlead blockquote{background:var(--surface);}

.fdr-cov__facts{margin:16px 0 0; display:flex; flex-direction:column; gap:0;}
.fdr-cov__fact{display:grid; grid-template-columns:150px 1fr; gap:14px; padding:11px 0;
  border-top:1px solid var(--ink-100);}
.fdr-cov__fact:first-child{border-top:none;}
.fdr-cov__fact dt{font-size:10.5px; font-weight:650; letter-spacing:.03em;
  text-transform:uppercase; color:var(--ink-500); padding-top:2px;}
.fdr-cov__fact dd{margin:0; display:flex; flex-direction:column; gap:3px;}
.fdr-cov__factvalue{font-size:12.5px; font-weight:600; color:var(--ink-900);}
.fdr-cov__factvalue--absent{color:var(--ink-400); font-weight:500;}
.fdr-cov__factnote{font-size:12px; line-height:1.55; color:var(--ink-600);}
.fdr-cov__factline{display:flex; align-items:baseline; gap:9px; flex-wrap:wrap;}
.fdr-cov__conf{padding:1px 7px; border-radius:100px; background:var(--ink-100);
  color:var(--ink-500); font-size:10px; font-weight:650; letter-spacing:.04em;
  text-transform:uppercase;}
.fdr-cov__mixed{font-size:12px; line-height:1.55; color:var(--amber-600);
  background:var(--amber-50); border:1px solid var(--amber-border); border-radius:var(--radius-sm);
  padding:7px 10px; margin-top:2px;}

.fdr-cov__quote{margin-top:4px;}
.fdr-cov__quote summary{cursor:pointer; font-size:11px; color:var(--navy-700);
  font-weight:600; list-style:none;}
.fdr-cov__quote summary::-webkit-details-marker{display:none;}
.fdr-cov__quote summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-cov__quote[open] summary::before{content:"▾ ";}
.fdr-cov__quote blockquote{margin:7px 0 0; padding:9px 12px; background:var(--ink-50);
  border-left:3px solid var(--border-strong); border-radius:0 var(--radius-sm) var(--radius-sm) 0;
  font-size:12px; line-height:1.6; color:var(--ink-700);}
.fdr-cov__quote cite{display:block; margin-top:5px; font-family:var(--font-mono);
  font-size:10.5px; font-style:normal; color:var(--ink-400);}

.fdr-cov__why{margin-top:16px; border-top:1px solid var(--border); padding-top:11px;}
.fdr-cov__why summary{cursor:pointer; font-size:11.5px; font-weight:600;
  color:var(--navy-700); list-style:none;}
.fdr-cov__why summary::-webkit-details-marker{display:none;}
.fdr-cov__why summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-cov__why[open] summary::before{content:"▾ ";}
.fdr-cov__why p{margin:9px 0 0; font-size:12.5px; line-height:1.65; color:var(--ink-600);}

/* Business profile — §5's interpretive lens, folded into block 1 */
.fdr-bp{margin-top:16px; padding:16px 18px; background:var(--ink-50);
  border:1px solid var(--border); border-radius:var(--radius);}
.fdr-bp__head{font-size:11px; font-weight:650; letter-spacing:.05em; text-transform:uppercase;
  color:var(--ink-500); margin-bottom:12px;}
.fdr-bp__grid{margin:0; display:grid; grid-template-columns:1fr 1fr; column-gap:24px;}
.fdr-bp__field{padding:10px 0; border-top:1px solid var(--border);
  display:grid; grid-template-columns:96px 1fr; gap:12px;}
.fdr-bp__field:nth-child(-n+2){border-top:none;}
.fdr-bp__field dt{font-family:var(--font-mono); font-size:10.5px; font-weight:650;
  letter-spacing:.04em; text-transform:uppercase; color:var(--navy-700); padding-top:1px;}
.fdr-bp__field dd{margin:0; font-size:12.5px; line-height:1.6; color:var(--ink-700);}
.fdr-bp__sources{margin:8px 0 0; padding:0; list-style:none; display:flex;
  flex-direction:column; gap:5px;}
.fdr-bp__sources li{font-size:11px; line-height:1.5; color:var(--ink-500);}
.fdr-bp__note{margin:12px 0 0; font-size:11.5px; line-height:1.55; color:var(--ink-500);
  font-style:italic;}

@media (max-width:640px){
  .fdr-bp__grid{grid-template-columns:1fr;}
  .fdr-bp__field:nth-child(2){border-top:1px solid var(--border);}
}

@media (max-width:560px){
  .fdr-cov__fact{grid-template-columns:1fr; gap:4px;}
}

/* Block 2 — executive dashboard */
.fdr-dash__lede{margin:0 0 16px; font-size:13px; line-height:1.7; color:var(--ink-600);
  width:100%; text-align:justify; text-justify:inter-word;}
.fdr-dash__grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
  gap:10px;}
.fdr-tile{background:var(--ink-50); border:1px solid var(--border); border-radius:var(--radius);
  padding:12px 14px; position:relative; min-width:0;}
.fdr-tile.is-flagged{background:var(--amber-50); border-color:var(--amber-border);}
.fdr-tile.is-uncomputed{background:var(--surface); border-style:dashed;}
.fdr-tile__head{display:flex; align-items:center; justify-content:space-between;}
.fdr-tile__id{font-family:var(--font-mono); font-size:10.5px; color:var(--ink-400);}
.fdr-tile__mark{width:16px; height:16px; border-radius:50%; background:var(--amber-600);
  color:var(--white); font-size:11px; font-weight:700; line-height:16px; text-align:center;}
.fdr-tile__label{margin:5px 0 8px; font-size:12px; line-height:1.4; color:var(--ink-600);
  min-height:2.6em;}
.fdr-tile__value{font-family:var(--font-mono); font-size:19px; font-weight:650;
  color:var(--ink-900); font-variant-numeric:tabular-nums;}
.fdr-tile__unit{font-family:var(--font-sans); font-size:11px; font-weight:500;
  color:var(--ink-400);}
.fdr-tile__move{margin-top:5px; font-size:11.5px; color:var(--ink-600);
  font-variant-numeric:tabular-nums;}
.fdr-tile.is-flagged .fdr-tile__move{color:var(--amber-600); font-weight:600;}
.fdr-tile__ctx{color:var(--ink-500); font-weight:400;}
.fdr-tile__suspect{margin-top:5px; font-size:11px; line-height:1.55; color:var(--amber-600);
  font-weight:600;}
.fdr-tile__reason{font-size:11.5px; line-height:1.55; color:var(--ink-500);}
.fdr-tile__reason-full{margin:6px 0 0; font-size:10.5px; line-height:1.55; color:var(--ink-600);
  overflow-wrap:anywhere; word-break:break-word;}

.fdr-tile__src{margin-top:8px;}
.fdr-tile__src summary{cursor:pointer; font-size:10.5px; font-weight:650; color:var(--navy-700);
  list-style:none; letter-spacing:.02em;}
.fdr-tile__src summary::-webkit-details-marker{display:none;}
.fdr-tile__src summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-tile__src[open] summary::before{content:"▾ ";}
.fdr-tile__formula{margin:6px 0 0; font-family:var(--font-mono); font-size:10.5px;
  line-height:1.5; color:var(--ink-700); overflow-wrap:anywhere; word-break:break-word;}
.fdr-tile__formulavals{margin:3px 0 0; font-family:var(--font-mono); font-size:10.5px;
  line-height:1.5; color:var(--ink-500); overflow-wrap:anywhere; word-break:break-word;}
.fdr-tile__src ul{margin:6px 0 0; padding:0; list-style:none; display:flex; flex-direction:column;
  gap:6px; min-width:0;}
.fdr-tile__src li{padding:6px 8px; background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-sm); font-size:10.5px; line-height:1.5; min-width:0;
  overflow-wrap:anywhere; word-break:break-word;}
.fdr-tile__srcrole{display:inline-block; margin-right:6px; padding:1px 5px; border-radius:100px;
  background:var(--ink-100); color:var(--ink-500); font-style:normal; font-size:9.5px;
  font-weight:650; text-transform:uppercase; letter-spacing:.03em;}
.fdr-tile__srcrow{display:block; color:var(--ink-700); font-style:italic;
  overflow-wrap:anywhere; word-break:break-word;}
.fdr-tile__srcloc{display:block; margin-top:2px; font-family:var(--font-mono); color:var(--ink-400);
  overflow-wrap:anywhere; word-break:break-word;}
.fdr-tile__srcmissing{color:var(--ink-400);}

/* A table figure that opens its own calculation panel on click, rather than
   leaving the reader to find it in a combined list further down the page. */
.fdr-root .fdr-numlink{background:none; border:none; padding:0; margin:0; font:inherit;
  color:inherit; cursor:pointer; border-bottom:1px dotted var(--navy-600);}
.fdr-root .fdr-numlink:hover{color:var(--navy-700); border-bottom-color:var(--navy-700);}
.fdr-tile__src--dupont{flex:1 1 220px; min-width:220px;}
.fdr-tile__src--dupont.is-jumped{outline:2px solid var(--navy-600); outline-offset:2px;
  transition:outline-color .2s var(--ease);}
.fdr-dash__note{margin:16px 0 0; padding:10px 14px; background:var(--ink-50);
  border-radius:var(--radius-sm); font-size:12px; line-height:1.6; color:var(--ink-600);}

/* Block 4 — financial health summary */
.fdr-health__grid{display:grid; grid-template-columns:1fr 1fr; gap:14px;}
.fdr-health__card{background:var(--ink-50); border:1px solid var(--border);
  border-radius:var(--radius); padding:16px 18px;}
.fdr-health__title{margin:0 0 8px; font-size:12.5px; font-weight:650; color:var(--ink-900);}
.fdr-health__text{margin:0; font-size:13px; line-height:1.65; color:var(--ink-600);}
.fdr-health__empty{margin:0; font-size:12.5px; color:var(--ink-400);}
@media (max-width:640px){ .fdr-health__grid{grid-template-columns:1fr;} }

/* Block 5 — key trends & structural drift */
.fdr-trend__grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  gap:10px;}
.fdr-trend__card{background:var(--ink-50); border:1px solid var(--border);
  border-radius:var(--radius); padding:12px 14px;}
.fdr-trend__card.is-fired{background:var(--amber-50); border-color:var(--amber-border);}
.fdr-trend__head{display:flex; align-items:center; justify-content:space-between; gap:8px;}
.fdr-trend__cluster{font-size:10px; letter-spacing:.03em; text-transform:uppercase;
  color:var(--ink-400); font-weight:650;}
.fdr-trend__mark{width:16px; height:16px; flex:none; border-radius:50%;
  background:var(--amber-600); color:var(--white); font-size:11px; font-weight:700;
  line-height:16px; text-align:center;}
.fdr-trend__title{margin:6px 0 6px; font-size:13px; font-weight:650; color:var(--ink-900);}
.fdr-trend__obs{margin:0; font-size:12.5px; line-height:1.6; color:var(--ink-600);}
.fdr-trend__meta{display:flex; gap:8px; margin-top:8px; flex-wrap:wrap;}
.fdr-trend__badge{font-size:10.5px; padding:2px 7px; background:var(--surface);
  border:1px solid var(--border); border-radius:999px; color:var(--ink-500);}
.fdr-trend__src{margin-top:8px;}
.fdr-trend__src summary{cursor:pointer; font-size:10.5px; font-weight:650; color:var(--navy-700);
  list-style:none; letter-spacing:.02em;}
.fdr-trend__src summary::-webkit-details-marker{display:none;}
.fdr-trend__src summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-trend__src[open] summary::before{content:"▾ ";}
.fdr-trend__trace{margin:6px 0 0; font-size:11px; line-height:1.55; color:var(--ink-500);
  font-family:var(--font-mono);}
.fdr-trend__src ul{margin:6px 0 0; padding-left:16px; font-size:11px; line-height:1.6;
  color:var(--ink-500);}

/* Block 6 — risk clusters with interactions */
.fdr-rc__list{display:flex; flex-direction:column; gap:10px;}
.fdr-rc{background:var(--ink-50); border:1px solid var(--border); border-radius:var(--radius);
  overflow:hidden;}
.fdr-rc.is-raised{border-color:var(--amber-border);}
.fdr-rc__summary{list-style:none; cursor:pointer; padding:12px 16px; display:flex;
  align-items:center; gap:16px;}
.fdr-rc__summary::-webkit-details-marker{display:none;}
.fdr-rc__summary::before{content:"▸ "; flex:none; color:var(--ink-400); margin-right:2px;}
.fdr-rc[open] .fdr-rc__summary::before{content:"▾ ";}
.fdr-rc__summary-main{flex:1 1 auto; min-width:0; display:flex; align-items:baseline; gap:8px;}
.fdr-rc__theme{font-size:13.5px; font-weight:650; color:var(--ink-900);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.fdr-rc__id{flex:none; font-family:var(--font-mono); font-size:10.5px; color:var(--ink-400);}
.fdr-rc__summary-badges{flex:none; display:flex; gap:6px; justify-content:flex-end;}
.fdr-rc__badge{flex:none; white-space:nowrap; font-size:10.5px; padding:2px 8px;
  background:var(--surface); border:1px solid var(--border); border-radius:999px;
  color:var(--ink-500);}
.fdr-rc__badge--muted{color:var(--ink-400);}
.fdr-rc__body{padding:0 16px 16px; border-top:1px solid var(--border);}
.fdr-rc__note{margin:12px 0 0; font-size:12px; line-height:1.6; color:var(--ink-500);}
.fdr-rc__section{margin-top:14px;}
.fdr-rc__section h5{margin:0 0 6px; font-size:11px; font-weight:650; letter-spacing:.03em;
  text-transform:uppercase; color:var(--ink-500);}
.fdr-rc__section h5 .fdr-rc__hint{text-transform:none; letter-spacing:0; font-weight:400;
  color:var(--ink-400); font-size:10.5px;}
.fdr-rc__section p{margin:0; font-size:12.5px; line-height:1.6; color:var(--ink-600);}
.fdr-rc__section ul{margin:0; padding-left:18px; font-size:12.5px; line-height:1.65;
  color:var(--ink-600); display:flex; flex-direction:column; gap:6px;}
.fdr-rc__signal--fired{color:var(--ink-700);}
.fdr-rc__fired-tag{display:inline-block; margin-left:6px; padding:1px 6px;
  background:var(--amber-600); color:var(--white); border-radius:999px;
  font-size:9px; font-weight:700; letter-spacing:.04em; vertical-align:middle;}
.fdr-rc__badge--high{color:var(--red-600); border-color:var(--red-border); background:var(--red-50); font-weight:650;}
.fdr-rc__badge--medium{color:var(--amber-600); border-color:var(--amber-border); background:var(--amber-50); font-weight:650;}
.fdr-rc__badge--low{color:var(--ok-600); border-color:var(--ok-border); background:var(--ok-50); font-weight:650;}
.fdr-rc__interaction-panel{margin:12px 0 16px; padding:12px 14px; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);}
.fdr-rc__interaction-title{font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--ink-500); margin-bottom:8px;}
.fdr-rc__interaction-list{display:flex; flex-direction:column; gap:8px;}
.fdr-rc__interaction-item{padding:10px 12px; border-radius:var(--radius-sm); border:1px solid var(--border);}
.fdr-rc__interaction-item.is-reinforcing{background:var(--red-50); border-color:var(--red-border);}
.fdr-rc__interaction-item.is-offsetting{background:var(--ok-50); border-color:var(--ok-border);}
.fdr-rc__interaction-head{display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:4px;}
.fdr-rc__interaction-pair{font-family:var(--font-mono); font-size:12px; color:var(--navy-800);}
.fdr-rc__interaction-rel-badge{font-size:10px; font-weight:700; padding:2px 7px; border-radius:999px; text-transform:uppercase;}
.fdr-rc__interaction-rel-badge.badge--reinforcing{background:var(--red-600); color:#ffffff;}
.fdr-rc__interaction-rel-badge.badge--offsetting{background:var(--ok-600); color:#ffffff;}
.fdr-rc__interaction-rationale{margin:0; font-size:12px; line-height:1.55; color:var(--ink-700);}
.fdr-rc__assertion-pills{display:flex; flex-wrap:wrap; gap:6px; margin-top:4px;}
.fdr-rc__assertion-pill{font-size:11px; font-weight:600; padding:2px 8px; border-radius:4px; background:var(--surface); border:1px solid var(--border); color:var(--navy-700);}
.fdr-rc__response-grid{display:flex; flex-direction:column; gap:6px; font-size:12px; margin-top:4px;}
.fdr-rc__response-col{background:var(--surface); padding:7px 10px; border-radius:var(--radius-sm); border:1px solid var(--border); color:var(--ink-700); line-height:1.5;}
.fdr-rc__source-tag{display:inline-block; margin-left:8px; font-family:var(--font-mono); font-size:10px; color:var(--ink-400); background:var(--surface); padding:1px 5px; border-radius:3px; border:1px solid var(--border);}
.fdr-rc__specialist-badge{display:inline-block; font-size:11px; font-weight:600; padding:3px 8px; border-radius:4px; background:var(--indigo-50); color:var(--indigo-600); border:1px solid var(--indigo-border);}


/* Block 7 — audit-planning matrix */
.fdr-mx__scroll{width:100%; overflow-x:auto; border:1px solid var(--border);
  border-radius:var(--radius);}
.fdr-mx{width:100%; border-collapse:collapse; font-size:12px;}
.fdr-mx th{text-align:left; padding:10px 12px; background:var(--ink-50);
  border-bottom:1px solid var(--border); font-size:10px; font-weight:650;
  letter-spacing:.04em; text-transform:uppercase; color:var(--ink-500); white-space:nowrap;}
.fdr-mx td{text-align:left; padding:10px 12px; border-bottom:1px solid var(--border);
  color:var(--ink-600); vertical-align:top;}
.fdr-mx tbody tr:last-child td{border-bottom:none;}
.fdr-mx tbody tr:hover{background:var(--ink-50);}
.fdr-mx__rank{font-family:var(--font-mono); font-weight:650; color:var(--ink-900);}
.fdr-mx__theme{font-weight:650; color:var(--ink-900); white-space:nowrap;}
.fdr-mx__id{font-family:var(--font-mono); font-size:10px; color:var(--ink-400);}
.fdr-mx__risk{display:inline-block; white-space:nowrap; padding:2px 9px;
  border-radius:999px; font-size:10.5px; font-weight:650; border:1px solid transparent;}
.fdr-mx__risk--significant{background:var(--red-50); color:var(--red-600); border-color:var(--red-border);}
.fdr-mx__risk--inherent{background:var(--amber-50); color:var(--amber-600); border-color:var(--amber-border);}
.fdr-mx__response{min-width:260px;}
.fdr-mx__response p{margin:0 0 6px; line-height:1.55;}
.fdr-mx__response p:last-child{margin-bottom:0;}
.fdr-mx__corrob{font-size:10.5px; padding:2px 8px; background:var(--surface);
  border:1px solid var(--border); border-radius:999px; color:var(--ink-500);}
.fdr-mx__closing{margin:14px 0 0; padding:12px 14px; background:var(--amber-50);
  border:1px solid var(--amber-border); border-radius:var(--radius); font-size:12.5px; line-height:1.65;
  color:var(--ink-700);}

/* Report shell */
.fdr-report{display:flex; flex-direction:column; flex:1; min-height:0; gap:12px;}
.fdr-report__controls{display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  padding:10px 16px; background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-lg); box-shadow:var(--shadow-xs);}
.fdr-report__note{flex:1 1 320px; margin:0; font-size:13px; color:var(--ink-500);}
/* The primary action and its follow-up (download) belong together as one
   unit, not spread across the row — stacked tightly, primary on top, sized
   to the button rather than to whatever space the row has left. */
.fdr-report__actions{display:flex; flex-direction:column; gap:6px; flex:none; width:160px;}
.fdr-report__actions .fdr-btn{width:100%;}

.fdr-empty{display:flex; align-items:center; justify-content:center; padding:32px 0;}
.fdr-empty--grow{flex:1;}
.fdr-empty__text{margin:0; font-size:13px; color:var(--ink-400);}

/* Composer */
.fdr-composer{flex:none; padding:12px 24px 16px; background:linear-gradient(to top,var(--bg) 62%,rgba(244,246,249,0));}
.fdr-composer__box{display:flex; align-items:flex-end; gap:10px; max-width:820px; margin:0 auto;
  padding:9px 9px 9px 16px; background:var(--surface); border:1px solid var(--border-strong);
  border-radius:var(--radius-xl); box-shadow:var(--shadow-sm);
  transition:border-color .16s var(--ease), box-shadow .16s var(--ease);}
/* The focus ring belongs to the BOX, never to the textarea inside it. The
   root's :focus-visible rule drew a rounded outline around the textarea, which
   rendered as a second rectangle floating inside the rounded composer. */
.fdr-composer__box:focus-within{border-color:var(--navy-600); box-shadow:var(--shadow-md);}
.fdr-composer__box.is-disabled{background:var(--ink-50); box-shadow:none;}
.fdr-composer__box .fdr-composer__input{flex:1; min-width:0; border:none; outline:none; resize:none; background:none;
  padding:8px 0; font-size:14px; line-height:1.6; color:var(--ink-900); max-height:120px;}
.fdr-composer__input:focus,
.fdr-composer__input:focus-visible{outline:none; border:none; box-shadow:none;}
.fdr-composer__input::placeholder{color:var(--ink-400);}
.fdr-composer__input:disabled{cursor:not-allowed;}
.fdr-composer__box .fdr-composer__send{display:flex; align-items:center; justify-content:center; width:36px; height:36px;
  flex:none; border:none; border-radius:50%; background:var(--navy-700); color:var(--white); cursor:pointer;}
.fdr-composer__send:disabled{background:var(--ink-300); cursor:not-allowed;}

@media (max-width:720px){
  .fdr-topbar__inner,.fdr-panel,.fdr-composer,.fdr-banner{padding-left:16px; padding-right:16px;}
  .fdr-report__controls{flex-direction:column; align-items:stretch;}
  .fdr-report__actions{width:100%;}
  .fdr-field{min-width:0;}
  .fdr-entitybar{max-width:none;}
}
/* ---------------------------------------------------------------------------
   Thresholds: topbar button + centred dialog. Tokens only - no new colours or fonts.
   --------------------------------------------------------------------------- */
.fdr-field.fdr-field--thr{flex:0 0 auto; min-width:0;}
.fdr-btn.fdr-thr__open{position:relative; padding:0 16px; gap:8px; height:var(--fdr-ctl-h); color:var(--navy-700);}
.fdr-btn.fdr-thr__open:hover:not(:disabled){background:var(--navy-50); border-color:var(--navy-600);}
.fdr-thr__badge{display:inline-flex; align-items:center; justify-content:center; min-width:20px; height:20px;
  padding:0 6px; border-radius:100px; background:var(--navy-700); color:var(--white);
  font-family:var(--font-mono); font-size:11px; font-weight:650; line-height:1;}
.fdr-thr__loaderr{color:var(--red-600);}

.fdr-thr__overlay{position:fixed; inset:0; z-index:1200; display:flex; align-items:center; justify-content:center;
  padding:48px; background:rgba(11,24,54,.38); backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);}
.fdr-thr{display:flex; flex-direction:column; width:min(880px, 100%); max-height:100%; min-height:0;
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-xl);
  box-shadow:0 24px 64px rgba(11,24,54,.28), 0 4px 14px rgba(11,24,54,.12); overflow:hidden;}

.fdr-thr__head{display:flex; align-items:flex-start; justify-content:space-between; gap:20px;
  padding:22px 26px 14px; border-bottom:1px solid var(--border);}
.fdr-thr__title{margin:0; font-size:19px; font-weight:650; color:var(--navy-900); letter-spacing:-.005em;}
.fdr-thr__sub{margin:5px 0 0; font-size:12.5px; line-height:1.5; color:var(--ink-500); max-width:62ch;}
.fdr-thr .fdr-thr__x{flex:none; display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
  background:none; border:1px solid transparent; border-radius:var(--radius); color:var(--ink-500); cursor:pointer;
  transition:background .18s var(--ease), color .18s var(--ease);}
.fdr-thr .fdr-thr__x:hover:not(:disabled){background:var(--ink-100); color:var(--ink-900);}

.fdr-thr__tools{display:flex; align-items:center; gap:10px; padding:12px 26px; background:var(--ink-50);
  border-bottom:1px solid var(--border);}
.fdr-thr__search{flex:1; display:flex; align-items:center; gap:8px; padding:0 12px; height:36px;
  background:var(--surface); border:1px solid var(--border-strong); border-radius:var(--radius); color:var(--ink-400);
  transition:border-color .18s var(--ease), box-shadow .18s var(--ease);}
.fdr-thr__search:focus-within{border-color:var(--navy-600);}
.fdr-thr__search input{flex:1; min-width:0; border:none; outline:none; background:transparent; font:inherit;
  font-size:13.5px; color:var(--ink-900);}
.fdr-thr__search input,.fdr-thr__search input:focus,.fdr-thr__search input:focus-visible{outline:none; box-shadow:none; -webkit-appearance:none; appearance:none;}
.fdr-thr__search input::-webkit-search-cancel-button,.fdr-thr__search input::-webkit-search-decoration{-webkit-appearance:none; display:none;}
.fdr-thr__search input::placeholder{color:var(--ink-400);}
.fdr-thr__count{font-family:var(--font-mono); font-size:11.5px; color:var(--ink-400); white-space:nowrap;}

/* About seven rows are visible; the rest scroll. */
.fdr-thr__list{flex:0 1 520px; min-height:0; overflow-y:auto; overscroll-behavior:contain;
  scroll-behavior:smooth; padding:0 26px 8px;}
.fdr-thr__empty{margin:36px 0; text-align:center; font-size:13px; color:var(--ink-500);}
.fdr-thr__ghead{position:sticky; top:0; z-index:2;
  margin:0; padding:14px 0 8px; background:var(--surface); font-size:11.5px; font-weight:650; letter-spacing:.05em;
  text-transform:uppercase; color:var(--navy-700); border-bottom:1px solid var(--border);}
.fdr-thr__rows{list-style:none; margin:0; padding:0;}

.fdr-thr__row{display:grid; grid-template-columns:minmax(0,1fr) 270px 108px; align-items:center; gap:22px;
  min-height:74px; padding:12px 10px 12px 12px; margin-left:-12px; border-bottom:1px solid var(--ink-100);
  border-left:3px solid transparent; transition:background .2s var(--ease), border-color .2s var(--ease);}
.fdr-thr__row:hover{background:var(--ink-50);}
.fdr-thr__row.is-pending{background:var(--navy-50); border-left-color:var(--navy-600);}
.fdr-thr__label{display:flex; align-items:center; flex-wrap:wrap; gap:8px; font-size:13.5px; font-weight:600; color:var(--ink-900);}
.fdr-thr__tag{padding:1px 8px; border-radius:100px; background:var(--indigo-50); border:1px solid var(--indigo-border);
  font-family:var(--font-mono); font-size:10.5px; font-weight:550; color:var(--indigo-600);}
.fdr-thr__hint{margin:3px 0 0; font-size:12px; line-height:1.45; color:var(--ink-500);
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;}

.fdr-thr__ctl{min-width:0;}
.fdr-thr__slider{display:flex; align-items:center; gap:14px;}
.fdr-thr__track{position:relative; flex:1; height:20px; display:flex; align-items:center;}
.fdr-thr__range{position:relative; z-index:1; -webkit-appearance:none; appearance:none; width:100%; height:20px; margin:0; background:transparent; cursor:pointer;}
.fdr-thr__range:focus-visible{outline:2px solid var(--navy-600); outline-offset:3px; border-radius:100px;}
.fdr-thr__range::-webkit-slider-runnable-track{height:6px; border-radius:100px;
  background:linear-gradient(to right, var(--navy-600) calc(9px + (100% - 18px) * var(--p)), var(--ink-200) calc(9px + (100% - 18px) * var(--p)));}
.fdr-thr__range::-moz-range-track{height:6px; border-radius:100px; background:var(--ink-200);}
.fdr-thr__range::-moz-range-progress{height:6px; border-radius:100px; background:var(--navy-600);}
.fdr-thr__range::-webkit-slider-thumb{-webkit-appearance:none; appearance:none; width:18px; height:18px; margin-top:-6px;
  border-radius:50%; background:var(--surface); border:2px solid var(--navy-600); box-shadow:var(--shadow-md);
  transition:transform .15s var(--ease), box-shadow .15s var(--ease);}
.fdr-thr__range::-moz-range-thumb{width:14px; height:14px; border-radius:50%; background:var(--surface);
  border:2px solid var(--navy-600); box-shadow:var(--shadow-md);}
.fdr-thr__range:hover::-webkit-slider-thumb{transform:scale(1.12);}
.fdr-thr__range:active::-webkit-slider-thumb{transform:scale(1.18); box-shadow:0 0 0 5px var(--navy-100);}
/* The shipped default, marked on the track so any change is read against it. */
.fdr-thr__tick{position:absolute; top:50%; left:calc(9px + (100% - 18px) * var(--pd)); width:2px; height:14px;
  background:var(--amber-600); border-radius:1px; transform:translate(-50%,-50%); pointer-events:none; opacity:.9; z-index:0;}
.fdr-thr__readout,.fdr-thr__stepval{font-family:var(--font-mono); font-size:13px; font-weight:650; color:var(--navy-900);
  font-variant-numeric:tabular-nums; white-space:nowrap;}
.fdr-thr__readout{min-width:64px; text-align:right;}
.fdr-thr__unit{margin-left:3px; font-family:var(--font-sans); font-size:11px; font-weight:500; color:var(--ink-500);}

.fdr-thr__stepper{display:inline-flex; align-items:stretch; height:34px; border:1px solid var(--border-strong);
  border-radius:var(--radius); background:var(--surface); overflow:hidden;}
.fdr-thr .fdr-thr__stepbtn{width:34px; border:none; background:var(--ink-50); font-size:17px; line-height:1; color:var(--navy-700);
  cursor:pointer; transition:background .15s var(--ease);}
.fdr-thr .fdr-thr__stepbtn:hover:not(:disabled){background:var(--navy-100);}
.fdr-thr .fdr-thr__stepbtn:disabled{color:var(--ink-300); cursor:not-allowed;}
.fdr-thr__stepval{display:flex; align-items:center; justify-content:center; min-width:104px; padding:0 10px;
  border-left:1px solid var(--border); border-right:1px solid var(--border);}

.fdr-thr__meta{display:flex; flex-direction:column; align-items:flex-end; gap:4px;}
.fdr-thr__default{font-size:11.5px; color:var(--ink-400); white-space:nowrap;}
.fdr-thr .fdr-thr__reset{display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border:none; border-radius:100px;
  background:none; font-size:11.5px; font-weight:600; color:var(--navy-700); cursor:pointer;}
.fdr-thr .fdr-thr__reset:hover{background:var(--navy-100);}

.fdr-thr__foot{display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 26px;
  border-top:1px solid var(--border); background:var(--ink-50);}
.fdr-thr__status{font-size:12.5px; color:var(--ink-600);}
.fdr-thr__who{color:var(--ink-400);}
.fdr-thr__err{color:var(--red-600); font-weight:550;}
.fdr-thr__actions{display:flex; align-items:center; gap:10px;}
.fdr-thr .fdr-btn--ghost{padding:8px 16px; font-size:13px;}

@media (max-width:900px){
  .fdr-thr__overlay{padding:20px;}
  .fdr-thr__row{grid-template-columns:minmax(0,1fr); gap:10px;}
  .fdr-thr__meta{flex-direction:row; align-items:center; justify-content:space-between;}
  .fdr-thr__foot{flex-direction:column; align-items:stretch;}
  .fdr-thr__actions{justify-content:flex-end; flex-wrap:wrap;}
}
@media (prefers-reduced-motion:reduce){
  .fdr-thr__list{scroll-behavior:auto;}
  .fdr-thr__row,.fdr-thr__range::-webkit-slider-thumb{transition:none;}
}
`;
