"""
Fundora Auto DD Engine — Stage Configuration
=============================================
Defines the startup maturity stages (0-3), their required documents,
and the scoring weight distributions used by the risk scoring engine.

Stage Definitions:
    Stage 0 (Idea)          — No product, no revenue. Just a pitch.
    Stage 1 (MVP)           — Prototype exists, early users, no/low revenue.
    Stage 2 (Early Revenue) — Paying customers, some financial history.
    Stage 3 (Growth)        — Scaling, significant revenue, full financial trail.

The scoring weights shift dramatically between stages — an Idea-stage startup
is scored 40% on founder/deck quality with 0% on financials, while a Growth-
stage startup puts 30% weight on financial health.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, Set, Optional, Tuple
from models.schemas import ExtractedMetrics, ParsedDocument
from prompts.templates import STAGE_CLASSIFICATION_PROMPT
import logging
logger = logging.getLogger('auto_dd.stages')


# ---------------------------------------------------------------------------
# Document Types
# ---------------------------------------------------------------------------

class DocumentType(str):
    """
    Known document types that the parser can classify.
    
    These are string constants (not an Enum) to allow easy comparison with
    parsed filename heuristics.
    """
    PITCH_DECK = "pitch_deck"
    CAP_TABLE = "cap_table"
    KPI_SHEET = "kpi_sheet"
    FINANCIAL_PROJECTIONS = "financial_projections"
    HISTORICAL_PNL = "historical_pnl"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    BANK_STATEMENTS = "bank_statements"
    GST_RETURNS = "gst_returns"
    OTHER = "other"


# All recognized document types for validation
ALL_DOCUMENT_TYPES: Set[str] = {
    DocumentType.PITCH_DECK,
    DocumentType.CAP_TABLE,
    DocumentType.KPI_SHEET,
    DocumentType.FINANCIAL_PROJECTIONS,
    DocumentType.HISTORICAL_PNL,
    DocumentType.BALANCE_SHEET,
    DocumentType.CASH_FLOW,
    DocumentType.BANK_STATEMENTS,
    DocumentType.GST_RETURNS,
}


# ---------------------------------------------------------------------------
# Startup Stages
# ---------------------------------------------------------------------------

class StartupStage(IntEnum):
    """
    Maturity stage of a startup, determining document requirements and
    scoring weight distributions.
    """
    IDEA = 0           # No product/revenue
    MVP = 1            # Prototype, early users, no/low revenue
    EARLY_REVENUE = 2  # Paying customers, financial history emerging
    GROWTH = 3         # Scaling, significant revenue, full financial trail

    @property
    def label(self) -> str:
        """Human-readable stage name for API responses."""
        return _STAGE_LABELS[self]

    @property
    def description(self) -> str:
        """Detailed description of what this stage means."""
        return _STAGE_DESCRIPTIONS[self]


_STAGE_LABELS: Dict[StartupStage, str] = {
    StartupStage.IDEA: "Idea Stage",
    StartupStage.MVP: "MVP Stage",
    StartupStage.EARLY_REVENUE: "Early Revenue Stage",
    StartupStage.GROWTH: "Growth Stage",
}

_STAGE_DESCRIPTIONS: Dict[StartupStage, str] = {
    StartupStage.IDEA: (
        "Pre-product stage. The startup has a concept and pitch but no working "
        "product or revenue. Evaluation focuses on the founder team, market "
        "opportunity, and vision articulation."
    ),
    StartupStage.MVP: (
        "Minimum Viable Product exists. There may be early users or beta testers "
        "but little to no revenue. Evaluation adds product traction and KPI "
        "trajectory to the mix."
    ),
    StartupStage.EARLY_REVENUE: (
        "The startup has paying customers and some financial history. Revenue is "
        "emerging but may not yet be substantial. Financial verification becomes "
        "meaningful at this stage."
    ),
    StartupStage.GROWTH: (
        "The startup is scaling with significant revenue and a full financial "
        "paper trail. Due diligence is comprehensive, including bank statement "
        "cross-referencing and GST verification."
    ),
}


# ---------------------------------------------------------------------------
# Document Requirements per Stage
# ---------------------------------------------------------------------------

class StageDocumentConfig:
    """Document requirements for a specific stage."""

    def __init__(
        self,
        required: List[str],
        optional: List[str],
        description: str,
    ):
        self.required = set(required)
        self.optional = set(optional)
        self.all_accepted = self.required | self.optional
        self.description = description

    def validate_documents(
        self, provided_types: Set[str]
    ) -> tuple[bool, List[str], List[str]]:
        """
        Check which required documents are present/missing.
        
        Returns:
            (is_complete, missing_required, extra_documents)
        """
        missing = self.required - provided_types
        extra = provided_types - self.all_accepted - {DocumentType.OTHER}
        return len(missing) == 0, sorted(missing), sorted(extra)


STAGE_DOCUMENT_REQUIREMENTS: Dict[StartupStage, StageDocumentConfig] = {
    # ------------------------------------------------------------------
    # Stage 0: Idea — Just a pitch deck and cap table
    # ------------------------------------------------------------------
    StartupStage.IDEA: StageDocumentConfig(
        required=[
            DocumentType.PITCH_DECK,
            DocumentType.CAP_TABLE,
        ],
        optional=[
            DocumentType.FINANCIAL_PROJECTIONS,  # Some idea-stage founders have projections
        ],
        description="Idea stage requires only a Pitch Deck and Cap Table.",
    ),

    # ------------------------------------------------------------------
    # Stage 1: MVP — Add KPI sheet and projections
    # ------------------------------------------------------------------
    StartupStage.MVP: StageDocumentConfig(
        required=[
            DocumentType.PITCH_DECK,
            DocumentType.CAP_TABLE,
            DocumentType.KPI_SHEET,
            DocumentType.FINANCIAL_PROJECTIONS,
        ],
        optional=[
            DocumentType.HISTORICAL_PNL,
        ],
        description="MVP stage requires Pitch Deck, Cap Table, KPI Sheet, and Financial Projections.",
    ),

    # ------------------------------------------------------------------
    # Stage 2: Early Revenue — Full financial docs
    # ------------------------------------------------------------------
    StartupStage.EARLY_REVENUE: StageDocumentConfig(
        required=[
            DocumentType.PITCH_DECK,
            DocumentType.CAP_TABLE,
            DocumentType.KPI_SHEET,
            DocumentType.FINANCIAL_PROJECTIONS,
            DocumentType.HISTORICAL_PNL,
            DocumentType.BALANCE_SHEET,
            DocumentType.CASH_FLOW,
            DocumentType.BANK_STATEMENTS,
            DocumentType.GST_RETURNS,
        ],
        optional=[],
        description=(
            "Early Revenue stage requires full financial documentation including "
            "bank statements and GST returns for cross-verification."
        ),
    ),

    # ------------------------------------------------------------------
    # Stage 3: Growth — Same as Early Revenue (all docs required)
    # ------------------------------------------------------------------
    StartupStage.GROWTH: StageDocumentConfig(
        required=[
            DocumentType.PITCH_DECK,
            DocumentType.CAP_TABLE,
            DocumentType.KPI_SHEET,
            DocumentType.FINANCIAL_PROJECTIONS,
            DocumentType.HISTORICAL_PNL,
            DocumentType.BALANCE_SHEET,
            DocumentType.CASH_FLOW,
            DocumentType.BANK_STATEMENTS,
            DocumentType.GST_RETURNS,
        ],
        optional=[],
        description=(
            "Growth stage requires the complete document set for thorough "
            "due diligence, including cross-verification of all financial claims."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Scoring Weight Distributions per Stage
# ---------------------------------------------------------------------------
# Weights are percentages (sum to 100) across five scoring categories.
# These shift to reflect what matters most at each stage.

class ScoringWeights:
    """Scoring weight distribution for a specific stage."""

    def __init__(
        self,
        founder: float,
        market: float,
        product: float,
        financial: float,
        unit_economics: float,
    ):
        # Validate weights sum to 100
        total = founder + market + product + financial + unit_economics
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"Scoring weights must sum to 100, got {total}: "
                f"founder={founder}, market={market}, product={product}, "
                f"financial={financial}, unit_economics={unit_economics}"
            )

        self.founder = founder
        self.market = market
        self.product = product
        self.financial = financial
        self.unit_economics = unit_economics

    def as_dict(self) -> Dict[str, float]:
        """Return weights as a dictionary for programmatic access."""
        return {
            "founder": self.founder,
            "market": self.market,
            "product": self.product,
            "financial": self.financial,
            "unit_economics": self.unit_economics,
        }

    def weighted_score(self, scores: Dict[str, float]) -> float:
        """
        Compute the weighted composite score.
        
        Args:
            scores: Dict with keys matching weight categories, values 0-100.
            
        Returns:
            Weighted composite score (0-100).
        """
        weights = self.as_dict()
        total = 0.0
        weight_sum = 0.0
        for category, weight in weights.items():
            if category in scores and scores[category] is not None:
                total += scores[category] * (weight / 100.0)
                weight_sum += weight
            # If a category is missing, we redistribute its weight proportionally
        
        # Normalize if some categories were missing
        if weight_sum > 0 and weight_sum < 100:
            total = total * (100.0 / weight_sum)

        return round(min(max(total, 0), 100))


STAGE_SCORING_WEIGHTS: Dict[StartupStage, ScoringWeights] = {
    # ------------------------------------------------------------------
    # Idea Stage: Founder & market vision dominate (no financials to score)
    # ------------------------------------------------------------------
    StartupStage.IDEA: ScoringWeights(
        founder=40.0,
        market=30.0,
        product=20.0,
        financial=0.0,
        unit_economics=10.0,
    ),

    # ------------------------------------------------------------------
    # MVP Stage: Product gains importance, financials start mattering
    # ------------------------------------------------------------------
    StartupStage.MVP: ScoringWeights(
        founder=25.0,
        market=20.0,
        product=30.0,
        financial=10.0,
        unit_economics=15.0,
    ),

    # ------------------------------------------------------------------
    # Early Revenue Stage: Financials become significant
    # ------------------------------------------------------------------
    StartupStage.EARLY_REVENUE: ScoringWeights(
        founder=15.0,
        market=15.0,
        product=20.0,
        financial=30.0,
        unit_economics=20.0,
    ),

    # ------------------------------------------------------------------
    # Growth Stage: Financials and unit economics dominate
    # ------------------------------------------------------------------
    StartupStage.GROWTH: ScoringWeights(
        founder=10.0,
        market=15.0,
        product=15.0,
        financial=35.0,
        unit_economics=25.0,
    ),
}


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def get_stage_config(stage: StartupStage) -> StageDocumentConfig:
    """Get document requirements for a given stage."""
    return STAGE_DOCUMENT_REQUIREMENTS[stage]


def get_scoring_weights(stage: StartupStage) -> ScoringWeights:
    """Get scoring weight distribution for a given stage."""
    return STAGE_SCORING_WEIGHTS[stage]


def infer_stage_from_documents(provided_types: Set[str]) -> StartupStage:
    """
    Heuristic: infer the most likely startup stage from the set of
    documents provided.
    
    Logic:
        - If bank statements or GST returns are present → Stage 2/3
        - If KPI sheet or P&L is present → Stage 1
        - Otherwise → Stage 0 (Idea)
    
    This is a preliminary classification refined by Layer 2 AI analysis.
    """
    has_bank = DocumentType.BANK_STATEMENTS in provided_types
    has_gst = DocumentType.GST_RETURNS in provided_types
    has_pnl = DocumentType.HISTORICAL_PNL in provided_types
    has_balance = DocumentType.BALANCE_SHEET in provided_types
    has_kpi = DocumentType.KPI_SHEET in provided_types
    has_projections = DocumentType.FINANCIAL_PROJECTIONS in provided_types

    # Stage 2/3: Has full financial documentation
    if has_bank or has_gst:
        if has_pnl and has_balance:
            return StartupStage.GROWTH
        return StartupStage.EARLY_REVENUE

    # Stage 1: Has KPI or projections but no bank/GST
    if has_kpi or has_projections or has_pnl:
        return StartupStage.MVP

    # Stage 0: Minimal docs (pitch deck + cap table only)
    return StartupStage.IDEA


# --- CLASSIFIER ---



async def classify_stage(
    parsed_documents: List[ParsedDocument],
    metrics: Optional[ExtractedMetrics] = None,
    use_ai: bool = True,
) -> Tuple[StartupStage, int]:
    """
    Classify the startup's maturity stage.
    
    Strategy:
        1. Run heuristic classification based on document types (always)
        2. Refine with AI if available and use_ai=True
        3. Apply metric-based adjustments
    
    Args:
        parsed_documents: List of parsed documents.
        metrics: Extracted financial metrics (may be None if Layer 1 hasn't run).
        use_ai: Whether to use AI for refinement.
        
    Returns:
        Tuple of (StartupStage, confidence_score).
    """
    # --- Step 1: Heuristic classification from document types ---
    doc_types: Set[str] = {doc.document_type for doc in parsed_documents}
    heuristic_stage = infer_stage_from_documents(doc_types)
    heuristic_confidence = _compute_heuristic_confidence(doc_types, heuristic_stage)

    logger.info(
        "Heuristic stage classification: %s (confidence: %d%%, doc types: %s)",
        heuristic_stage.label,
        heuristic_confidence,
        ", ".join(sorted(doc_types)),
    )

    # --- Step 2: Metric-based adjustment ---
    adjusted_stage, metric_confidence = _adjust_by_metrics(
        heuristic_stage, metrics
    )

    if adjusted_stage != heuristic_stage:
        logger.info(
            "Stage adjusted by metrics: %s → %s",
            heuristic_stage.label,
            adjusted_stage.label,
        )

    # Combine confidences (weighted average)
    combined_confidence = int(
        heuristic_confidence * 0.6 + metric_confidence * 0.4
    )

    # --- Step 3: AI refinement (optional) ---
    if use_ai:
        try:
            ai_stage, ai_confidence = await _refine_with_ai(
                parsed_documents, metrics, adjusted_stage
            )
            if ai_stage is not None:
                # AI gets a heavier vote if its confidence is high
                if ai_confidence >= 75:
                    final_stage = ai_stage
                    final_confidence = int(
                        combined_confidence * 0.3 + ai_confidence * 0.7
                    )
                else:
                    final_stage = adjusted_stage
                    final_confidence = int(
                        combined_confidence * 0.6 + ai_confidence * 0.4
                    )

                logger.info(
                    "AI refinement: stage=%s, confidence=%d%%",
                    final_stage.label, final_confidence,
                )
                return final_stage, min(99, final_confidence)

        except Exception as e:
            logger.warning("AI stage refinement failed, using heuristic: %s", e)

    return adjusted_stage, min(99, combined_confidence)


def _compute_heuristic_confidence(
    doc_types: Set[str],
    stage: StartupStage,
) -> int:
    """
    Compute confidence in the heuristic classification.
    
    Higher confidence when:
        - More documents are provided (more signals)
        - Documents align well with the detected stage
    """
    base_confidence = 50

    # More documents → more confidence
    doc_count_bonus = min(25, len(doc_types) * 4)

    # Check alignment: are the right docs present for this stage?
    config = get_stage_config(stage)
    is_complete, missing, _ = config.validate_documents(doc_types)

    if is_complete:
        alignment_bonus = 20
    else:
        # Penalize based on how many required docs are missing
        alignment_bonus = max(-10, 15 - len(missing) * 5)

    return min(95, base_confidence + doc_count_bonus + alignment_bonus)


def _adjust_by_metrics(
    heuristic_stage: StartupStage,
    metrics: Optional[ExtractedMetrics],
) -> Tuple[StartupStage, int]:
    """
    Adjust the heuristic stage based on actual financial metrics.
    
    For example, if documents suggest Stage 0 (Idea) but metrics show
    monthly revenue, bump up to Stage 1 or 2.
    """
    if metrics is None:
        return heuristic_stage, 50  # No metrics → can't adjust, moderate confidence

    confidence = 60
    stage = heuristic_stage

    # Revenue-based adjustments
    has_revenue = (
        (metrics.monthly_revenue is not None and metrics.monthly_revenue > 0)
        or (metrics.mrr is not None and metrics.mrr > 0)
        or (metrics.annual_revenue is not None and metrics.annual_revenue > 0)
    )

    significant_revenue = (
        (metrics.mrr is not None and metrics.mrr > 50_000)
        or (metrics.monthly_revenue is not None and metrics.monthly_revenue > 50_000)
        or (metrics.annual_revenue is not None and metrics.annual_revenue > 500_000)
    )

    has_customers = (
        metrics.paying_customers is not None and metrics.paying_customers > 0
    )

    # Upgrade logic
    if significant_revenue and stage.value < StartupStage.GROWTH.value:
        stage = StartupStage.GROWTH
        confidence = 80
    elif has_revenue and has_customers and stage.value < StartupStage.EARLY_REVENUE.value:
        stage = StartupStage.EARLY_REVENUE
        confidence = 75
    elif has_revenue and stage.value < StartupStage.MVP.value:
        stage = StartupStage.MVP
        confidence = 70

    # Downgrade logic — if staged high but no revenue proof
    if not has_revenue and stage.value >= StartupStage.EARLY_REVENUE.value:
        stage = StartupStage.MVP
        confidence = 55  # Lower confidence because of mismatch

    return stage, confidence


async def _refine_with_ai(
    parsed_documents: List[ParsedDocument],
    metrics: Optional[ExtractedMetrics],
    preliminary_stage: StartupStage,
) -> Tuple[Optional[StartupStage], int]:
    """
    Use AI to refine the stage classification.
    
    Passes document types, metrics summary, and pitch deck content to the LLM.
    """
    from utils.llm_orchestrator import get_orchestrator
    orchestrator = get_orchestrator()

    # Build the prompt context
    doc_types = [f"- {doc.document_type} ({doc.filename})" for doc in parsed_documents]
    doc_types_str = "\n".join(doc_types)

    # Metrics summary
    metrics_lines = []
    if metrics:
        for field_name, value in metrics.model_dump().items():
            if value is not None and field_name != "raw_metrics":
                metrics_lines.append(f"- {field_name}: {value}")
    metrics_str = "\n".join(metrics_lines) if metrics_lines else "No metrics extracted yet."

    # Pitch deck summary (first 2000 chars)
    pitch_deck_summary = "No pitch deck content available."
    for doc in parsed_documents:
        if doc.document_type == DocumentType.PITCH_DECK and doc.raw_text:
            pitch_deck_summary = doc.raw_text[:2000] + "..."
            break

    prompt = STAGE_CLASSIFICATION_PROMPT.format(
        document_types=doc_types_str,
        metrics_summary=metrics_str,
        pitch_deck_summary=pitch_deck_summary,
    )

    result = await orchestrator.call_extraction_llm(prompt)

    if "_error" in result:
        return None, 0

    # Parse the AI response
    try:
        ai_stage_value = int(result.get("stage", preliminary_stage.value))
        ai_confidence = int(result.get("confidence_score", 50))

        # Map to StartupStage enum
        stage_map = {
            0: StartupStage.IDEA,
            1: StartupStage.MVP,
            2: StartupStage.EARLY_REVENUE,
            3: StartupStage.GROWTH,
        }
        ai_stage = stage_map.get(ai_stage_value, preliminary_stage)

        logger.info(
            "AI stage classification: %s (confidence: %d%%, reasoning: %s)",
            ai_stage.label,
            ai_confidence,
            result.get("reasoning", "N/A"),
        )

        return ai_stage, ai_confidence

    except (ValueError, KeyError) as e:
        logger.warning("Failed to parse AI stage response: %s", e)
        return None, 0
