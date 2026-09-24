"""Request/response contracts for the Financial Diagnostic Report mode.

Defined here rather than added to `app/schemas.py` because this mode's query is
not the shared chat shape: it is scoped to an entity the operator selected, and
its response carries the provenance of the read and the classified intent, both
of which the generic `QueryResponse` has nowhere to put. Keeping them local also
means this mode can evolve its contract without touching a file three other
modes depend on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _normalise(value: str) -> str:
    """Strip trailing whitespace per line, not just from the whole string.

    Same normalisation, and for the same measured reason, as
    `app.schemas.QueryRequest`: an invisible space before a newline survives
    `.strip()` and changes downstream tokenisation. This path has no LLM in it,
    but the text is matched against derived vocabularies where a stray token is
    equally capable of shifting a score across the margin.
    """
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


class FDRQueryRequest(BaseModel):
    """One question about one entity.

    `entity_id` is required and is never inferred from the question text. The UI
    has an entity picker, so the entity is a selection rather than a guess —
    which removes the entire class of failure where one entity's figures are
    served under another's name.
    """

    entity_id: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1, max_length=2000)
    # Bypass the in-process latency cache and re-read the filings. For the case
    # where the corpus changed under a long-lived process.
    refresh: bool = False

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        return _normalise(value)

    @field_validator("entity_id")
    @classmethod
    def _clean_entity(cls, value: str) -> str:
        return value.strip()


class FDRQueryResponse(BaseModel):
    mode: str
    entity_id: str
    query: str
    # overview | cluster | signal | figure | coverage | blocked
    # | ambiguous | refused | unsupported
    kind: str
    answer: str
    rows: list[dict] = []
    # Where the figures came from and how old the read is. Present on every
    # answer that touched the corpus, so a stale read is visible in the response
    # rather than inferred from its absence.
    provenance: dict = {}
    # Numbered sources behind a generated answer, each with the filing, the
    # chunk it came from, its page and its rerank score. Empty on the
    # deterministic path, which cites the statement inside its own prose.
    sources: list[dict] = []
    # The classified subject, its closed reason code, and the evidence it was
    # read from — so a wrong answer can be diagnosed as a wrong reading rather
    # than guessed at.
    intent: dict = {}
    elapsed_seconds: float = 0.0


class FDRReportRequest(BaseModel):
    """Build the report for one entity.

    Deliberately smaller than `FDRQueryRequest`: a report has no question in it.
    The entity is the whole input, and it is a selection rather than free text.
    """

    entity_id: str = Field(..., min_length=1, max_length=200)
    refresh: bool = False

    @field_validator("entity_id")
    @classmethod
    def _clean_entity(cls, value: str) -> str:
        return value.strip()


class FDRReportBlock(BaseModel):
    """One block. `payload` is shaped by the block's own builder, so it is left
    open rather than modelled per block — the alternative is a schema change in
    this file every time a block gains a field, for no validation gained."""

    id: str
    number: int
    title: str
    payload: dict | None = None
    report_version: str = ""
    # Present only when the block failed. The report still returns; this section
    # says what went wrong in its place.
    error: str = ""


class FDRReportResponse(BaseModel):
    mode: str
    entity_id: str
    blocks: list[FDRReportBlock] = []
    provenance: dict = {}
    versions: dict = {}
    report_version: str = ""
    elapsed_seconds: float = 0.0


class FDRReportManifestResponse(BaseModel):
    blocks: list[dict] = []
    report_version: str = ""


class FDREntity(BaseModel):
    entity_id: str
    filings: int
    first_fy: int | None = None
    last_fy: int | None = None
    first_fy_label: str = ""
    last_fy_label: str = ""
    trend_capable: bool = False
    min_trend_years: int = 3


class FDREntitiesResponse(BaseModel):
    entities: list[FDREntity]
    count: int


# --- XBRL direct-fetch path (as_db) -----------------------------------------------
# Separate models from the fs_db-backed ones above on purpose: an XbrlEntity is one
# FILING (`doc_id`), not one entity with a multi-year filing count, because as_db's
# 48 multi-filing CINs are same-year standalone/consolidated pairs, not a history —
# collapsing them the way FDREntity does would hide a real choice.

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
    value: float
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


