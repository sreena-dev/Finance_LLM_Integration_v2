"""Request/response contracts for the Financial Diagnostic Report mode.

This mode answers only from as_db, the hosted Postgres of parsed MCA XBRL
filings. Models are scoped per filing (`doc_id`), not per multi-year entity:
as_db's multi-filing CINs are same-year standalone/consolidated pairs, not a
history.
"""

from __future__ import annotations

from pydantic import BaseModel

# --- XBRL direct-fetch path (as_db) -----------------------------------------------
# An XbrlEntity is one FILING (`doc_id`), not one entity with a multi-year
# filing count: as_db's 48 multi-filing CINs are same-year
# standalone/consolidated pairs, not a history.

class XbrlEntity(BaseModel):
    doc_id: str
    entity_cin: str
    company_name: str
    fy_label: str
    filing_type: str | None = None
    usable: bool


class XbrlEntitiesResponse(BaseModel):
    entities: list[XbrlEntity]
    count: int


class XbrlTile(BaseModel):
    id: str
    label: str
    computed: bool
    value: float | None = None
    display: str
    unit_label: str = ""
    movement_label: str = ""
    direction: str = ""
    attention: bool = False
    context_display: str = ""
    series_years: int = 0
    reason: str = ""
    citations: list[dict] = []


class XbrlDashboardResponse(BaseModel):
    doc_id: str
    entity_cin: str = ""
    company_name: str = ""
    fy_label: str = ""
    tiles: list[XbrlTile]
    computed_count: int
    total_count: int
    flagged_ids: list[str] = []
    scale_label: str = ""
    lede: str = ""


class XbrlProfileField(BaseModel):
    key: str
    label: str
    text: str


class XbrlGroundedFigure(BaseModel):
    id: str
    label: str
    value: float | None = None   # None: undefined (e.g. D/E on negative equity), not zero
    display: str
    unit: str


class XbrlBusinessProfileResponse(BaseModel):
    doc_id: str
    company_name: str
    cin: str
    fy_label: str
    formed: bool
    fields: list[XbrlProfileField]
    citations: list[dict] = []
    grounded_figures: list[XbrlGroundedFigure] = []
    reason: str = ""


# --- Block 4: Financial Health Summary (Structure & Performance) ---

class XbrlHealthSummaryResponse(BaseModel):
    doc_id: str
    company_name: str
    cin: str
    fy_label: str
    formed: bool = True
    business_type: str = ""
    structure: str = ""
    performance: str = ""
    citations: list[dict] = []
    reason: str = ""


# --- Block 6: Key Risk Clusters with Interactions ---

class XbrlRiskSignal(BaseModel):
    signal: str
    source_trace: str = ""


class XbrlRecommendedResponse(BaseModel):
    nature: str = ""
    timing: str = ""
    extent: str = ""


class XbrlRiskMetric(BaseModel):
    label: str
    value: str


class XbrlRiskCluster(BaseModel):
    id: str
    theme: str
    raised: bool = True
    reason: str = ""
    contributing_signals: list[XbrlRiskSignal] = []
    alt_explanations: list[str] = []
    # The figures the raise/no-raise call was actually made on — populated for a
    # "Not Raised" cluster too (currently only RC06), not only when it raises. See
    # `xbrl_risk_deterministic._rc06_metrics`.
    metrics: list[XbrlRiskMetric] = []
    affected_assertions: list[str] = []
    inherent_risk: str = "medium"
    significant_risk: bool = False
    control_implications: str = ""
    recommended_response: XbrlRecommendedResponse | dict = {}
    specialist_referral: str = "None"
    evidence_request: str = ""
    diagnostic_confidence: str = "high"
    priority_rank: int | None = None
    priority_reasoning: str = ""



class XbrlRiskInteraction(BaseModel):
    cluster_a: str
    cluster_b: str
    relationship: str
    rationale: str


class XbrlRiskClustersResponse(BaseModel):
    doc_id: str
    company_name: str
    cin: str
    fy_label: str
    formed: bool
    risk_clusters: list[XbrlRiskCluster] = []
    interactions: list[XbrlRiskInteraction] = []
    diagnostics_not_run: list[dict] = []
    caveats: list[str] = []
    reason: str = ""


# --- Block 1: Company Overview (xbrl_overview) — additive, standalone ---

class XbrlOverviewEntity(BaseModel):
    name: str
    filings_read: int = 0
    filings_label: str = ""
    period_label: str = ""
    comparable_years: int = 0
    comparable_label: str = ""


class XbrlOverviewFlavour(BaseModel):
    value: str = ""
    meaning: str = ""


class XbrlOverviewFrameworkSource(BaseModel):
    doc_id: str = ""
    page: int | None = None


class XbrlOverviewFramework(BaseModel):
    state: str = "absent"
    label: str = ""
    confidence: str | None = None
    confidence_basis: str = ""
    detail: str = ""
    evidence: str | None = None
    source: XbrlOverviewFrameworkSource | None = None


class XbrlCompanyOverviewResponse(BaseModel):
    doc_id: str
    cin: str
    fy_label: str
    entity: XbrlOverviewEntity
    flavour: XbrlOverviewFlavour
    framework: XbrlOverviewFramework


# --- S01-S27 signal library (xbrl_signal_engine) — additive, standalone from Block 6 ---

class XbrlSignalResult(BaseModel):
    signal_id: str
    cluster_id: str
    status: str                         # FIRED | NOT_FIRED | ABSTAIN | NOT_APPLICABLE
    reason: str = ""
    reason_code: str = ""
    severity: str | None = None
    confidence: str | None = None
    confidence_basis: str = ""
    observation: str = ""
    trace: str = ""
    formula: str = ""
    source_trace: list[str] = []
    missing_inputs: list[str] = []
    proxy_used: str = ""
    is_by_nature_material: bool = False


class XbrlSignalsResponse(BaseModel):
    doc_id: str
    company_name: str
    cin: str
    fy_label: str
    signals: list[XbrlSignalResult] = []
    caveats: list[str] = []


# --- Block 5: Key Trends & Structural Drift ---

class XbrlStructuralDriftCard(BaseModel):
    signal_id: str
    title: str
    drift_type: str = "structural_shift"
    severity: str = "medium"
    observation: str
    audit_lead: str
    evidence_lead: str = ""


class XbrlCommonSizeHighlight(BaseModel):
    item: str
    category: str
    drift_pp: float
    direction: str
    narrative: str


class XbrlTrendsResponse(BaseModel):
    doc_id: str
    company_name: str
    cin: str
    fy_label: str
    series_years: int
    period_labels: list[str] = []
    summary_lede: str = ""
    dupont_narrative: str = ""
    structural_drift_cards: list[XbrlStructuralDriftCard] = []
    common_size_highlights: list[XbrlCommonSizeHighlight] = []
    common_size_schedule: list[dict] = []
    dupont_schedule: list[dict] = []
    formed: bool = True
    reason: str = ""




# --- Auditor-tunable thresholds ---------------------------------------------------------

class XbrlThresholdItem(BaseModel):
    key: str
    label: str
    group: str
    used_in: str
    unit: str
    control: str           # "slider" | "stepper"
    min: float
    max: float
    step: float
    default: float
    value: float
    overridden: bool
    hint: str = ""


class XbrlThresholdsResponse(BaseModel):
    version: int
    updated_at: str | None = None
    updated_by: str | None = None
    groups: list[str]
    items: list[XbrlThresholdItem]
    overridden_count: int


class XbrlThresholdUpdate(BaseModel):
    # key -> value in the units the UI shows (e.g. 25 for 25 %), never the code's units.
    values: dict[str, float]


class XbrlThresholdReset(BaseModel):
    keys: list[str] | None = None    # None / empty -> reset everything
