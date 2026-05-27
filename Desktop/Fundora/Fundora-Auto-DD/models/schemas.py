"""
Fundora Auto DD Engine — Pydantic Schemas
==========================================
Strict output schemas that enforce the exact JSON structure consumed by the
Fundora frontend (investor view). All fields are validated, documented, and
typed for production use.

The top-level response model is ``AutoDDResponse``, which mirrors:
    {
        "report_id": "RPT-S-001",
        "startup_id": "S-001",
        "confidence_interval": "Moderate Confidence (70-85%)",
        "executive_summary": { ... },
        "scores": { ... },
        "verification": { ... },
        "insights": { ... }
    }

Schema version: 1.1
Changes from 1.0:
    - Added reasoning: List[str] to BusinessModel, MarketSize, Team
    - Added confidence_interval: str to AutoDDResponse
    - Added report_id: Optional[str] to AutoDDResponse
    - Added startup_id: Optional[str] to AutoDDResponse and PipelineContext
    - Added Verification model (was referenced but never defined — bug fix)
    - Added verification: Optional[Verification] to PipelineContext
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-Models — Public API Response
# ---------------------------------------------------------------------------

class BusinessModel(BaseModel):
    score: int = 0
    summary: str = ""
    reasoning: List[str] = Field(
        default_factory=list,
        description="Up to 3 concise bullet-point justifications for this score.",
    )

class Financials(BaseModel):
    mrr: float = 0.0
    burn_rate: float = 0.0
    runway_months: float = 0.0

class Traction(BaseModel):
    growth_rate: str = ""
    retention_notes: str = ""

class MarketSize(BaseModel):
    tam: str = ""
    sam: str = ""
    claims_validity: str = ""
    reasoning: List[str] = Field(
        default_factory=list,
        description="Up to 3 concise bullet-point justifications for market claims.",
    )

class Team(BaseModel):
    founders_strength: str = ""
    identified_gaps: str = ""
    reasoning: List[str] = Field(
        default_factory=list,
        description="Up to 3 concise bullet-point justifications for team assessment.",
    )

class CapTable(BaseModel):
    ownership_clarity: str = ""
    notes: str = ""

class RiskFlags(BaseModel):
    red_signals: List[str] = Field(default_factory=list)
    amber_signals: List[str] = Field(default_factory=list)


class AutoDDResponse(BaseModel):
    # --- Identity & Persistence ---
    report_id: Optional[str] = Field(
        default=None,
        description="Persisted report identifier (e.g. RPT-S-001). Set after save.",
    )
    startup_id: Optional[str] = Field(
        default=None,
        description="Caller-provided alphanumeric startup identifier (e.g. S-001).",
    )

    # --- Confidence Signal ---
    confidence_interval: str = Field(
        default="",
        description=(
            "Deterministic confidence band based on document completeness. "
            "E.g. 'High Confidence (90-95%)', 'Moderate Confidence (70-85%)', "
            "'Low Confidence (40-65%)'."
        ),
    )

    # --- Core Analysis ---
    investor_readiness: int = 0
    business_model: BusinessModel = Field(default_factory=BusinessModel)
    financials: Financials = Field(default_factory=Financials)
    traction: Traction = Field(default_factory=Traction)
    market_size: MarketSize = Field(default_factory=MarketSize)
    team: Team = Field(default_factory=Team)
    cap_table: CapTable = Field(default_factory=CapTable)
    risk_flags: RiskFlags = Field(default_factory=RiskFlags)


# ---------------------------------------------------------------------------
# Internal Pipeline Data Models
# ---------------------------------------------------------------------------
# These are used internally between pipeline layers and are NOT part of the
# public API response.

class ParsedDocument(BaseModel):
    """Result of parsing a single uploaded document."""

    filename: str
    document_type: str  # e.g., "pitch_deck", "cap_table", "pnl", etc.
    raw_text: Optional[str] = None
    tables: Optional[List[List[dict]]] = None  # List of tables, each table is a list of row dicts (from DataFrame.to_dict("records"))
    parse_errors: List[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ExtractedMetrics(BaseModel):
    """Deterministic metrics computed in Layer 1."""

    # Financial metrics
    monthly_burn_rate: Optional[float] = None
    runway_months: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    monthly_revenue: Optional[float] = None
    annual_revenue: Optional[float] = None
    revenue_growth_mom: Optional[float] = None  # Month-over-month growth %

    # Cap table
    total_esop_pct: Optional[float] = None
    founder_equity_pct: Optional[float] = None
    investor_equity_pct: Optional[float] = None
    total_dilution_pct: Optional[float] = None

    # Unit economics
    cac: Optional[float] = None              # Customer Acquisition Cost
    ltv: Optional[float] = None              # Lifetime Value
    ltv_cac_ratio: Optional[float] = None
    churn_rate_pct: Optional[float] = None
    arpu: Optional[float] = None             # Average Revenue Per User

    # Traction
    total_users: Optional[int] = None
    paying_customers: Optional[int] = None
    mrr: Optional[float] = None              # Monthly Recurring Revenue

    # Cash position (from Balance Sheet)
    cash_on_hand: Optional[float] = None
    total_debt: Optional[float] = None

    # Additional metadata
    raw_metrics: dict = Field(
        default_factory=dict,
        description="Catch-all for metrics that don't fit standard fields.",
    )


class AIExtractionResult(BaseModel):
    """Results from Layer 2 AI extraction."""

    # Market analysis (from Pitch Deck)
    legal_entity_name: Optional[str] = None
    tam: Optional[str] = None
    sam: Optional[str] = None
    som: Optional[str] = None
    business_model: Optional[str] = None
    competitive_positioning: Optional[str] = None
    key_differentiators: List[str] = Field(default_factory=list)

    # Team analysis
    founder_count: Optional[int] = None
    founder_backgrounds: List[str] = Field(default_factory=list)
    domain_expertise_score: Optional[int] = None  # 0-100

    # Anomaly detection
    revenue_discrepancies: List[str] = Field(default_factory=list)
    gst_discrepancies: List[str] = Field(default_factory=list)
    bank_statement_flags: List[str] = Field(default_factory=list)
    anomaly_severity: Optional[str] = None  # "none", "low", "medium", "high"

    # Raw AI response for debugging
    raw_ai_response: Optional[str] = None


class BenchmarkResult(BaseModel):
    """Results from Layer 3 benchmarking."""

    category: str           # e.g., "gross_margin", "burn_multiple"
    actual_value: Optional[float] = None
    benchmark_low: Optional[float] = None
    benchmark_high: Optional[float] = None
    assessment: str = "unknown"  # "excellent", "good", "average", "below_average", "concerning"
    notes: str = ""


class Verification(BaseModel):
    """Revenue verification result from Layer 2 cross-reference."""

    mrr_verified: bool = False
    confidence_percentage: int = 0
    verification_notes: str = ""


class PipelineContext(BaseModel):
    """
    Accumulated context passed between pipeline layers.

    This is the central data structure that gets enriched as each layer runs.
    Layer N reads from previous layers and writes its own results here.
    """

    # --- Identity Threading ---
    # startup_id is passed in by the API caller (e.g. "S-001").
    # It threads through the pipeline and becomes part of the persisted filename.
    startup_id: Optional[str] = None

    # Layer 0: Document classification
    detected_stage: Optional[str] = None
    stage_confidence: int = 0
    parsed_documents: List[ParsedDocument] = Field(default_factory=list)

    # Layer 1: Deterministic extraction
    extracted_metrics: Optional[ExtractedMetrics] = None

    # Layer 2: AI extraction
    ai_extraction: Optional[AIExtractionResult] = None

    # Layer 2: Revenue verification (was missing — bug fix)
    verification: Optional[Verification] = None

    # Layer 3: Benchmarking
    benchmarks: List[BenchmarkResult] = Field(default_factory=list)

    # Final 8-output fields
    investor_readiness: int = 50
    business_model: BusinessModel = Field(default_factory=BusinessModel)
    financials: Financials = Field(default_factory=Financials)
    traction: Traction = Field(default_factory=Traction)
    market_size: MarketSize = Field(default_factory=MarketSize)
    team: Team = Field(default_factory=Team)
    cap_table: CapTable = Field(default_factory=CapTable)
    risk_flags: RiskFlags = Field(default_factory=RiskFlags)

    # Confidence interval (computed in pipeline from doc count)
    confidence_interval: str = ""

    # Pipeline metadata
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    processing_time_seconds: Optional[float] = None
