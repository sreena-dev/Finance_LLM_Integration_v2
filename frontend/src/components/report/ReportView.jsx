import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { fetchCatalog, generateReport } from '../../api/client';
import Icon from '../common/Icon';
import Select from '../common/Select';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import ReportDocument from './ReportDocument';
import './ReportView.css';

const PIPELINE_STEPS = [
  'Resolving the document',
  'Fetching report sections and financials',
  'Running deterministic pre-flight checks',
  'Extracting opinion, CARO and IFC',
  'Running coherence checks',
  'Drafting the review memorandum',
];

const SCOPES = [
  { value: 'standalone', label: 'Standalone' },
  { value: 'consolidated', label: 'Consolidated' },
];

/**
 * Statutory Auditor's Report mode.
 *
 * Deliberately form-driven rather than conversational: the pipeline's only
 * inputs are an entity, a financial year and a scope. The year list is nested
 * under the chosen entity in the catalog response, so the second dropdown can
 * only ever offer a combination that has a document behind it.
 */
export default function ReportView({ mode, state, setState }) {
  const { entities, catalogError, entity, fy, scope, report, error } = state;

  const [loadingCatalog, setLoadingCatalog] = useState(entities === null);
  const [generating, setGenerating] = useState(false);

  const patch = (fields) => setState((prev) => ({ ...prev, ...fields }));

  // Load the catalog once per session.
  useEffect(() => {
    if (entities !== null) return;
    let cancelled = false;

    (async () => {
      setLoadingCatalog(true);
      try {
        const list = await fetchCatalog(mode);
        if (!cancelled) patch({ entities: list, catalogError: null });
      } catch (err) {
        if (!cancelled) patch({ entities: [], catalogError: err.message });
      } finally {
        if (!cancelled) setLoadingCatalog(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.id]);

  const entityOptions = useMemo(
    () =>
      (entities || []).map((e) => ({
        value: e.entity,
        label: e.entity,
        sublabel: `${e.years.length} ${e.years.length === 1 ? 'year' : 'years'}`,
      })),
    [entities]
  );

  const selectedEntity = useMemo(
    () => (entities || []).find((e) => e.entity === entity) || null,
    [entities, entity]
  );

  const yearOptions = useMemo(
    () =>
      (selectedEntity?.years || []).map((y) => ({
        value: `${y.fy_start}-${y.fy_end}`,
        label: y.label,
        sublabel: y.doc_name || undefined,
      })),
    [selectedEntity]
  );

  function onEntityChange(next) {
    // Clearing the year is what prevents an entity/FY pair that has no
    // document — the years belong to the previously selected entity.
    patch({ entity: next, fy: null });
  }

  async function generate() {
    if (!entity || !fy || generating) return;
    const [fyStart, fyEnd] = fy.split('-').map(Number);

    patch({ error: null });
    setGenerating(true);
    try {
      const result = await generateReport(mode, { entity, fyStart, fyEnd, scope });
      patch({ report: result, error: null });
    } catch (err) {
      patch({ error: err.message, report: null });
    } finally {
      setGenerating(false);
    }
  }

  const canGenerate = Boolean(entity && fy) && !generating;

  return (
    <div className="report">
      <div className="report__scroll">
        <div className="report__inner">
          <motion.section
            className="setup card"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
          >
            <header className="setup__head">
              <span className="setup__mark">
                <Icon name="seal" size={18} />
              </span>
              <div>
                <h2 className="setup__title">Generate a statutory auditor's report</h2>
                <p className="setup__sub">
                  Select the entity and financial year to produce its SAR review
                  memorandum.
                </p>
              </div>
            </header>

            {catalogError ? (
              <div className="setup__body">
                <Notice tone="error" title="Could not load the entity catalog">
                  {catalogError}
                </Notice>
              </div>
            ) : (
              <div className="setup__body">
                <div className="setup__grid">
                  <Select
                    label="Entity name"
                    icon="doc"
                    value={entity}
                    options={entityOptions}
                    onChange={onEntityChange}
                    loading={loadingCatalog}
                    placeholder="Select an entity"
                    emptyText="No entities ingested"
                    disabled={generating}
                  />

                  <Select
                    label="Financial year"
                    icon="ledger"
                    value={fy}
                    options={yearOptions}
                    onChange={(v) => patch({ fy: v })}
                    placeholder={entity ? 'Select a financial year' : 'Select an entity first'}
                    emptyText="No financial years for this entity"
                    disabled={!entity || generating}
                  />
                </div>

                <div className="setup__row">
                  <div className="scope">
                    <span className="scope__label">Scope</span>
                    <div className="scope__group" role="radiogroup" aria-label="Scope">
                      {SCOPES.map((s) => (
                        <button
                          key={s.value}
                          type="button"
                          role="radio"
                          aria-checked={scope === s.value}
                          className={`scope__btn ${scope === s.value ? 'is-on' : ''}`}
                          onClick={() => patch({ scope: s.value })}
                          disabled={generating}
                        >
                          {scope === s.value && (
                            <motion.span
                              className="scope__bg"
                              layoutId="scope-active"
                              transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                            />
                          )}
                          <span className="scope__text">{s.label}</span>
                        </button>
                      ))}
                    </div>
                  </div>

                  <motion.button
                    type="button"
                    className="btn btn--primary setup__go"
                    onClick={generate}
                    disabled={!canGenerate}
                    whileHover={canGenerate ? { y: -1 } : {}}
                    whileTap={canGenerate ? { y: 1 } : {}}
                  >
                    {generating ? (
                      <>
                        <span className="setup__spinner" />
                        Generating…
                      </>
                    ) : (
                      <>
                        <Icon name="sparkle" size={16} />
                        Generate report
                      </>
                    )}
                  </motion.button>
                </div>
              </div>
            )}
          </motion.section>

          <AnimatePresence mode="wait">
            {generating && (
              <motion.div
                key="progress"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <ProgressSteps steps={PIPELINE_STEPS} intervalMs={6000} />
              </motion.div>
            )}

            {!generating && error && (
              <motion.div
                key="error"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
              >
                <Notice tone="error" title="Report generation failed">
                  {error}
                </Notice>
              </motion.div>
            )}

            {!generating && report && (
              <motion.div
                key={`${report.entity}-${report.fy_label}-${report.scope}`}
                initial={{ opacity: 0, y: 18 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
              >
                <ReportDocument report={report} />
              </motion.div>
            )}

            {!generating && !report && !error && !catalogError && (
              <motion.div
                key="idle"
                className="report__idle"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.35 }}
              >
                <Icon name="doc" size={28} className="report__idle-icon" />
                <p className="report__idle-text">
                  The generated memorandum will appear here.
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
