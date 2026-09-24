/**
 * Report-tab block renderers: Business Profile, Coverage, Dashboard, Health,
 * Trends, Risk Clusters and the Audit-Planning Matrix, plus the ReportPane
 * that drives the streamed report generation. Split out of `FdrAnalysis.jsx`
 * — see that file's header for why; nothing here changed shape or behavior.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  downloadReport,
  fetchReportManifest,
  generateReportStream,
} from './api';
import { Empty, TileSource } from './FdrShared';

const PROFILE_GRID_ORDER = [
  'model', 'financing', 'revenue', 'value_drivers', 'cost', 'inherent_risk_map',
];

/**
 * Business profile — §5's interpretive lens, folded into block 1.
 *
 * Every field is either grounded (a citation into the filing, or a computed
 * figure the rest of the report already trusts) or states plainly that it
 * could not be — never left blank, which would read as "nothing to say"
 * rather than "not found this run".
 */
export function BusinessProfileCard({ profile }) {
  // Not formed this run — usually a generation-endpoint outage, an
  // operational fact about the tool, not something an auditor reading the
  // report needs to see or can act on. Grounded figures live in the other
  // blocks regardless, so there is nothing else this card can show here; it
  // draws nothing rather than a technical error a reader cannot use.
  if (!profile.formed) return null;

  const fields = profile.fields || [];
  const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));
  const cited = (profile.citations || []).filter((c) => c.cited);

  return (
    <div className="fdr-bp">
      <div className="fdr-bp__head">The interpretive lens</div>
      {fields.length > 0 ? (
        <>
          <dl className="fdr-bp__grid">
            {PROFILE_GRID_ORDER.map((key) => {
              const f = byKey[key];
              if (!f) return null;
              return (
                <div className="fdr-bp__field" key={key}>
                  <dt>{f.label}</dt>
                  <dd>{f.text}</dd>
                </div>
              );
            })}
          </dl>
          {cited.length > 0 && (
            <details className="fdr-cov__quote">
              <summary>Sources</summary>
              <ul className="fdr-bp__sources">
                {cited.map((c) => (
                  <li key={c.n}>
                    [{c.n}] {c.citation}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {profile.reason && <p className="fdr-bp__note">{profile.reason}</p>}
        </>
      ) : null}
    </div>
  );
}

export function CoverageBlock({ payload }) {
  const { entity, statement_flavour: flavour, reporting_framework: framework } = payload;
  const fw = payload.framework_entity_type;
  const businessProfile = payload.business_profile || { formed: false, fields: [] };
  // Three states, not two: fully classified, the legal form read but the
  // business model still unclassified ("partial"), or nothing read at all. A
  // partial read is real progress and should not look like the empty state —
  // but it is not a green "done" either, since interpretation is still
  // withheld downstream until the business model is known too.
  const isOpen = fw.state === 'not_formed';
  const isPartial = fw.state === 'partial';

  return (
    <div className="fdr-blk__body">
      <div
        className={`fdr-cov__lead ${isOpen ? 'is-open' : ''} ${
          isPartial ? 'is-partial' : ''
        }`}
      >
        <div className="fdr-cov__leadhead">
          <span className="fdr-cov__leadlabel">Framework &amp; entity type</span>
          <span className="fdr-cov__leadvalue">{fw.headline}</span>
          {fw.entity_type_confidence && (
            <span className="fdr-cov__conf">{fw.entity_type_confidence} confidence</span>
          )}
        </div>
        <p className="fdr-cov__leaddetail">{fw.detail}</p>
        {fw.entity_type_evidence && (
          <details className="fdr-cov__quote fdr-cov__quote--onlead">
            <summary>Read from the filing</summary>
            <blockquote>{fw.entity_type_evidence}</blockquote>
            {fw.entity_type_source?.doc_id && (
              <cite>
                {fw.entity_type_source.doc_id}
                {fw.entity_type_source.page ? `, page ${fw.entity_type_source.page}` : ''}
              </cite>
            )}
          </details>
        )}
      </div>

      <dl className="fdr-cov__facts">
        <div className="fdr-cov__fact">
          <dt>Entity</dt>
          <dd>
            <span className="fdr-cov__factvalue">{entity.name.replace(/_/g, ' ')}</span>
            <span className="fdr-cov__factnote">
              {entity.filings_label} read · {entity.period_label} · {entity.comparable_label}
            </span>
          </dd>
        </div>
        <div className="fdr-cov__fact">
          <dt>Statement flavour</dt>
          <dd>
            <span className="fdr-cov__factvalue">{flavour.value}</span>
            <span className="fdr-cov__factnote">{flavour.meaning}</span>
          </dd>
        </div>
        <div className="fdr-cov__fact">
          <dt>Reporting framework</dt>
          <dd>
            <span className="fdr-cov__factline">
              <span
                className={`fdr-cov__factvalue ${
                  framework.state === 'read' ? '' : 'fdr-cov__factvalue--absent'
                }`}
              >
                {framework.label}
              </span>
              {framework.confidence && (
                <span className="fdr-cov__conf">{framework.confidence} confidence</span>
              )}
            </span>
            <span className="fdr-cov__factnote">{framework.detail}</span>
            {framework.mixed && (
              <span className="fdr-cov__mixed">{framework.mixed}</span>
            )}
            {/* The sentence the framework was read from. A stated framework the
                reader cannot check is a claim, not a reading — so the filing's
                own words travel with it, behind a disclosure so they do not
                crowd the field they support. */}
            {framework.evidence && (
              <details className="fdr-cov__quote">
                <summary>Read from the filing</summary>
                <blockquote>{framework.evidence}</blockquote>
                {framework.source?.doc_id && (
                  <cite>
                    {framework.source.doc_id}
                    {framework.source.page ? `, page ${framework.source.page}` : ''}
                  </cite>
                )}
              </details>
            )}
          </dd>
        </div>
      </dl>

      <BusinessProfileCard profile={businessProfile} />

      <details className="fdr-cov__why">
        <summary>Why this matters for planning</summary>
        <p>{payload.why_it_matters}</p>
      </details>
    </div>
  );
}

/**
 * Block 2 — Executive dashboard.
 *
 * Every number here is a pure formula over bound, verified figures — nothing
 * is generated. A tile that could not compute is drawn rather than dropped
 * (same rule as block 1): a dashboard silently missing a figure reads as a
 * complete dashboard, which is the one failure this layout exists to avoid.
 */
export function DashboardBlock({ payload }) {
  return (
    <div className="fdr-blk__body">
      <p className="fdr-dash__lede">{payload.lede}</p>

      <div className="fdr-dash__grid">
        {payload.tiles.map((t) => (
          <div
            key={t.id}
            className={`fdr-tile ${t.computed ? '' : 'is-uncomputed'} ${
              t.attention ? 'is-flagged' : ''
            }`}
          >
            <div className="fdr-tile__head">
              <span className="fdr-tile__id">{t.id}</span>
              {t.attention && <span className="fdr-tile__mark" aria-label="Marked for attention">!</span>}
            </div>
            <div className="fdr-tile__label">{t.label}</div>
            {t.computed ? (
              <>
                <div className="fdr-tile__value">
                  {t.display}
                  {t.unit_label && <span className="fdr-tile__unit"> {t.unit_label}</span>}
                </div>
                {t.movement_reason ? (
                  <div className="fdr-tile__suspect">Movement withheld — {t.movement_reason}</div>
                ) : (
                  <div className="fdr-tile__move">
                    {t.movement_label || 'no comparable year'}
                    {t.context_display && (
                      <span className="fdr-tile__ctx"> · {t.context_display}</span>
                    )}
                  </div>
                )}
                {t.citations?.length > 0 && <TileSource citations={t.citations} />}
              </>
            ) : (
              <div className="fdr-tile__reason">{t.reason}</div>
            )}
          </div>
        ))}
      </div>

      {payload.total_count > 0 && payload.computed_count < payload.total_count && (
        <p className="fdr-dash__note">
          {payload.total_count - payload.computed_count} of {payload.total_count} figures
          could not be computed for this entity — see each tile above for why.
        </p>
      )}
    </div>
  );
}

/**
 * Block 4 — Financial health summary: structure & performance, interpreted.
 *
 * Two cards, each one paragraph. Nothing here is generated: "Structure" is
 * plain single-year arithmetic over bound figures, and "Performance" reuses
 * the exact movement Block 2 already computed and gated, plus the rule
 * engine's own decomposition and cash-quality wording — same discipline as
 * every other block, just read together instead of as separate tiles.
 */
export function HealthBlock({ payload }) {
  if (!payload?.formed) {
    return (
      <div className="fdr-blk__body">
        <p className="fdr-health__empty">
          {payload?.reason || 'Not built for this run.'}
        </p>
      </div>
    );
  }
  const { structure, performance } = payload;
  return (
    <div className="fdr-blk__body">
      <div className="fdr-health__grid">
        <div className="fdr-health__card">
          <h4 className="fdr-health__title">Structure</h4>
          <p className="fdr-health__text">{structure.text}</p>
          {structure.citations?.length > 0 && <TileSource citations={structure.citations} />}
        </div>
        <div className="fdr-health__card">
          <h4 className="fdr-health__title">Performance (decomposed)</h4>
          <p className="fdr-health__text">{performance.text}</p>
          {performance.citations?.length > 0 && <TileSource citations={performance.citations} />}
        </div>
      </div>
    </div>
  );
}

/**
 * Block 5 — Key trends & structural drift.
 *
 * One card per Layer 2-4 signal the rule engine actually evaluated this run
 * (`rules.py`) — every evaluated signal is shown, whichever way it read, so a
 * movement under threshold stays visible rather than silently dropped.
 */
export function TrendsBlock({ payload }) {
  const cards = payload?.cards || [];
  return (
    <div className="fdr-blk__body">
      <p className="fdr-dash__lede">{payload?.lede}</p>
      {cards.length > 0 && (
        <div className="fdr-trend__grid">
          {cards.map((c) => (
            <div key={c.signal_id} className={`fdr-trend__card ${c.fired ? 'is-fired' : ''}`}>
              <div className="fdr-trend__head">
                <span className="fdr-trend__cluster">{c.cluster}</span>
                {c.fired && (
                  <span className="fdr-trend__mark" aria-label="Crossed threshold">!</span>
                )}
              </div>
              <div className="fdr-trend__title">{c.title}</div>
              <p className="fdr-trend__obs">{c.observation}</p>
              {(c.severity || c.confidence) && (
                <div className="fdr-trend__meta">
                  {c.severity && <span className="fdr-trend__badge">Severity: {c.severity}</span>}
                  {c.confidence && (
                    <span className="fdr-trend__badge">Confidence: {c.confidence}</span>
                  )}
                </div>
              )}
              {(c.trace || c.evidence?.length > 0) && (
                <details className="fdr-trend__src">
                  <summary>How this was measured</summary>
                  {c.trace && <p className="fdr-trend__trace">{c.trace}</p>}
                  {c.evidence?.length > 0 && (
                    <ul>
                      {c.evidence.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  )}
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One clustered theme's full §10.3 planning package, collapsed behind its
 * own disclosure. Nothing here is generated — clustering, interaction
 * resolution and prioritisation all ran once inside the audit spine; this
 * only reshapes what `ClusterPackage` already carries. A theme that did not
 * raise still gets a card, with the reason it did not, rather than
 * disappearing — the same "never silently blank" rule as every other block.
 */
export function ClusterCard({ cluster }) {
  const resp = cluster.recommended_response || {};
  const hasResponse = resp.nature || resp.timing || resp.extent;
  // Fired first, and marked as such below — a signal that ran and came back
  // clean sitting next to one that fired, unmarked, reads as if either could
  // be the reason the theme raised. It is not: only the fired one is.
  const readSignals = (cluster.contributing_signals || [])
    .filter((s) => s.status === 'FIRED' || s.status === 'NOT_FIRED')
    .sort((a, b) => (a.fired === b.fired ? 0 : a.fired ? -1 : 1));

  return (
    <details className={`fdr-rc ${cluster.raised ? 'is-raised' : ''}`}>
      <summary className="fdr-rc__summary">
        <div className="fdr-rc__summary-main">
          <span className="fdr-rc__theme">{cluster.theme}</span>
          <span className="fdr-rc__id">{cluster.cluster_id}</span>
        </div>
        <div className="fdr-rc__summary-badges">
          {cluster.raised && cluster.severity_band ? (
            <span
              className={`fdr-rc__badge fdr-rc__badge--${cluster.severity_band.toLowerCase()}`}
            >
              Severity: {cluster.severity_band.toUpperCase()}
            </span>
          ) : (
            !cluster.raised && (
              <span className="fdr-rc__badge fdr-rc__badge--muted">Not raised</span>
            )
          )}
        </div>
      </summary>

      <div className="fdr-rc__body">
        {!cluster.raised ? (
          <p className="fdr-rc__note">{cluster.reason}</p>
        ) : (
          <>
            {cluster.interpretation_withheld && (
              <p className="fdr-rc__note">{cluster.interpretation_withheld}</p>
            )}

            {readSignals.length > 0 && (
              <div className="fdr-rc__section">
                <h5>Contributing signals</h5>
                <ul>
                  {readSignals.map((s) => (
                    <li key={s.signal_id} className={s.fired ? 'fdr-rc__signal--fired' : ''}>
                      <strong>{s.title}</strong> ({s.signal_id})
                      {s.fired && <span className="fdr-rc__fired-tag">FIRED</span>}: {s.observation}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {cluster.alt_explanations?.length > 0 && (
              <div className="fdr-rc__section">
                <h5>Plausible alternative explanations</h5>
                <ul>
                  {cluster.alt_explanations.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            )}

            {cluster.affected_assertions?.length > 0 && (
              <div className="fdr-rc__section">
                <h5>Affected assertions</h5>
                <p>{cluster.affected_assertions.join(', ')}</p>
              </div>
            )}

            {hasResponse && (
              <div className="fdr-rc__section">
                <h5>Candidate audit response (nature · timing · extent)</h5>
                <ul>
                  {resp.nature && (
                    <li>
                      <strong>Nature:</strong> {resp.nature}
                    </li>
                  )}
                  {resp.timing && (
                    <li>
                      <strong>Timing:</strong> {resp.timing}
                    </li>
                  )}
                  {resp.extent && (
                    <li>
                      <strong>Extent:</strong> {resp.extent}
                    </li>
                  )}
                </ul>
              </div>
            )}

            {cluster.evidence_request?.length > 0 && (
              <div className="fdr-rc__section">
                <h5>
                  Evidence request{' '}
                  <span className="fdr-rc__hint">
                    — a management explanation is a lead to test, not evidence
                  </span>
                </h5>
                <ul>
                  {cluster.evidence_request.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}

            {cluster.interactions?.length > 0 && (
              <div className="fdr-rc__section">
                <h5>Interactions</h5>
                <ul>
                  {cluster.interactions.map((it, i) => (
                    <li key={i}>{it}</li>
                  ))}
                </ul>
              </div>
            )}

            {cluster.priority_reasoning && (
              <p className="fdr-rc__note">{cluster.priority_reasoning}</p>
            )}
          </>
        )}
      </div>
    </details>
  );
}

/**
 * Block 6 — Risk clusters with interactions.
 *
 * Seven-or-so clustered themes, de-duplicated from the Layer 2-4 signals
 * Block 5 already words. Each carries a severity band and a diagnostic-
 * confidence stamp, kept separate (§4.4). Tap a cluster to open its full
 * planning package.
 */
export function ClustersBlock({ payload }) {
  const clusters = payload?.clusters || [];
  return (
    <div className="fdr-blk__body">
      <p className="fdr-dash__lede">{payload?.lede}</p>
      {clusters.length > 0 && (
        <div className="fdr-rc__list">
          {clusters.map((c) => (
            <ClusterCard key={c.cluster_id} cluster={c} />
          ))}
        </div>
      )}
    </div>
  );
}

const _RISK_BADGE_CLASS = {
  'Significant risk': 'fdr-mx__risk--significant',
  'Significant by nature': 'fdr-mx__risk--significant',
  'Inherent risk': 'fdr-mx__risk--inherent',
};

/**
 * Block 7 — Audit-planning matrix, the principal deliverable (§10.4).
 *
 * Every row is a cluster Block 6 already carries, condensed to one line.
 * Nothing is computed here either: the risk category is `significant_risk`
 * and the priority engine's own `by_nature` flag, read straight off.
 */
export function MatrixBlock({ payload }) {
  const rows = payload?.rows || [];
  return (
    <div className="fdr-blk__body">
      <p className="fdr-dash__lede">{payload?.lede}</p>
      {rows.length > 0 && (
        <div className="fdr-mx__scroll">
          <table className="fdr-mx">
            <thead>
              <tr>
                <th>#</th>
                <th>Cluster</th>
                <th>Affected assertions</th>
                <th>Risk</th>
                <th>Candidate response (N·T·E)</th>
                <th>Specialist</th>
                <th>Conf.</th>
                <th>Corrob.</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.cluster_id}>
                  <td className="fdr-mx__rank">{r.rank}</td>
                  <td>
                    <div className="fdr-mx__theme">{r.theme}</div>
                    <div className="fdr-mx__id">{r.cluster_id}</div>
                  </td>
                  <td>{r.affected_assertions.join(' · ') || '—'}</td>
                  <td>
                    <span
                      className={`fdr-mx__risk ${_RISK_BADGE_CLASS[r.risk_category] || ''}`}
                    >
                      {r.risk_category}
                    </span>
                  </td>
                  <td className="fdr-mx__response">
                    {r.response_nature && <p>{r.response_nature}</p>}
                    {r.response_timing && <p>{r.response_timing}</p>}
                    {r.response_extent && <p>{r.response_extent}</p>}
                    {!r.response_nature && !r.response_timing && !r.response_extent && '—'}
                  </td>
                  <td>{r.specialist_referral.join(', ') || '—'}</td>
                  <td>{r.diagnostic_confidence || '—'}</td>
                  <td>
                    <span className="fdr-mx__corrob">{r.corroboration}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {payload?.closing_note && <p className="fdr-mx__closing">{payload.closing_note}</p>}
    </div>
  );
}

const BLOCK_RENDERERS = {
  coverage: CoverageBlock,
  dashboard: DashboardBlock,
  health: HealthBlock,
  trends: TrendsBlock,
  clusters: ClustersBlock,
  matrix: MatrixBlock,
};

/**
 * One block, in whichever of its three states it is in.
 *
 * A block that has not arrived is drawn as a placeholder rather than omitted:
 * the manifest is fetched before the read starts precisely so the reader can
 * see the shape of what is coming and tell "still building" apart from
 * "finished, and this is all there is".
 */
export function ReportBlock({ spec, block }) {
  const Renderer = BLOCK_RENDERERS[spec.id];
  const state = block?.error ? 'error' : block ? 'ready' : 'pending';

  return (
    <section className={`fdr-blk is-${state}`}>
      <header className="fdr-blk__head">
        <span className="fdr-blk__num">{String(spec.number).padStart(2, '0')}</span>
        <h3 className="fdr-blk__title">{spec.title}</h3>
        {state === 'pending' && <span className="fdr-blk__state">building…</span>}
      </header>
      {state === 'ready' && Renderer && <Renderer payload={block.payload} />}
      {state === 'ready' && !Renderer && (
        <div className="fdr-blk__body">
          <p className="fdr-blk__fallback">
            This section was built but this version of the interface cannot draw it yet.
          </p>
        </div>
      )}
      {state === 'error' && (
        <div className="fdr-blk__body">
          <p className="fdr-blk__error">{block.error}</p>
        </div>
      )}
    </section>
  );
}

export function ReportPane({ entityId }) {
  const [manifest, setManifest] = useState([]);
  const [blocks, setBlocks] = useState({});
  const [status, setStatus] = useState('idle'); // idle | running | done | error
  const [progress, setProgress] = useState([]);
  const [error, setError] = useState('');
  const [meta, setMeta] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const abortRef = useRef(null);

  // The manifest costs no database and is the same for every entity, so it is
  // fetched once on mount rather than per run — it exists to lay out the
  // placeholders before anything is read.
  useEffect(() => {
    let live = true;
    fetchReportManifest()
      .then((res) => live && setManifest(res.blocks || []))
      .catch(() => live && setManifest([]));
    return () => {
      live = false;
    };
  }, []);

  // Switching entity discards the previous report rather than leaving it on
  // screen under a new name — the one failure this mode exists to avoid.
  useEffect(() => {
    abortRef.current?.abort();
    setBlocks({});
    setStatus('idle');
    setProgress([]);
    setError('');
    setMeta(null);
  }, [entityId]);

  const run = useCallback(async () => {
    if (!entityId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setStatus('running');
    setBlocks({});
    setProgress([]);
    setError('');
    setMeta(null);

    try {
      const completion = await generateReportStream(
        { entityId },
        {
          signal: controller.signal,
          onProgress: (message) =>
            setProgress((prev) => [...prev.slice(-3), message]),
          onBlock: (block) =>
            setBlocks((prev) => ({ ...prev, [block.id]: block })),
        }
      );
      setMeta(completion);
      setStatus('done');
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setError(err.message || 'The report could not be built.');
      setStatus('error');
    }
  }, [entityId]);

  const save = useCallback(async () => {
    if (!entityId) return;
    setDownloading(true);
    try {
      const { blob, filename } = await downloadReport({ entityId });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || 'The file could not be built.');
    } finally {
      setDownloading(false);
    }
  }, [entityId]);

  const built = Object.keys(blocks).length;

  return (
    <div className="fdr-report">
      <div className="fdr-report__controls">
        <p className="fdr-report__note">
          {status === 'running'
            ? progress[progress.length - 1] || 'Reading the filings…'
            : status === 'done'
              ? `${built} of ${manifest.length || built} section${built === 1 ? '' : 's'} built${
                  meta?.elapsed_seconds ? ` in ${meta.elapsed_seconds.toFixed(1)}s` : ''
                }.`
              : 'Builds from a live read of this entity’s filings. Sections appear as they are built.'}
        </p>
        {/* Generate/rebuild is the primary action and download is secondary to
            it, so they are stacked — not spread across the row — with the
            primary one on top, in the tightest space the two buttons need. */}
        <div className="fdr-report__actions">
          <button
            type="button"
            className="fdr-btn fdr-btn--primary"
            onClick={run}
            disabled={!entityId || status === 'running'}
          >
            {status === 'running'
              ? 'Building…'
              : status === 'done'
                ? 'Rebuild report'
                : 'Generate report'}
          </button>
          <button
            type="button"
            className="fdr-btn fdr-btn--ghost"
            onClick={save}
            disabled={!entityId || status === 'running' || downloading}
          >
            {downloading ? 'Preparing…' : 'Download'}
          </button>
        </div>
      </div>

      {error && <div className="fdr-report__error">{error}</div>}

      {status === 'idle' && !error ? (
        <Empty
          text={
            entityId
              ? `No report built for ${entityId.replace(/_/g, ' ')} yet.`
              : 'Select an entity to build its report.'
          }
          grow
        />
      ) : (
        <div className="fdr-report__blocks">
          {manifest.map((spec) => (
            <ReportBlock key={spec.id} spec={spec} block={blocks[spec.id]} />
          ))}
          {manifest.length > 0 && (() => {
            const built = new Set(manifest.map((m) => m.number));
            const missing = [];
            for (let n = 1; n <= 10; n += 1) if (!built.has(n)) missing.push(n);
            if (missing.length === 0) return null;
            return (
              <div className="fdr-report__upcoming">
                {`Section${missing.length === 1 ? '' : 's'} ${missing.join(', ')} — risk `
                  + `clusters, the audit-planning matrix and the rest — `
                  + `${missing.length === 1 ? 'is' : 'are'} not built yet.`}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
