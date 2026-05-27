"""
Fundora Auto DD Engine — Benchmarking Service (Layer 3)
=======================================================
Compares extracted startup metrics against industry baselines to provide
context for the scoring engine.

Baselines are segmented by:
    - Industry vertical (SaaS, E-commerce, Fintech, etc.)
    - Stage (Seed, Series A, Series B+)
    - Metric category (growth, margins, efficiency)

Currently uses hardcoded baselines. Future versions can pull from a database
or external API for more granular benchmarks.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from models.schemas import BenchmarkResult, ExtractedMetrics
from services.stages import StartupStage, get_scoring_weights
from models.schemas import AIExtractionResult

logger = logging.getLogger("auto_dd.benchmarking")


# ---------------------------------------------------------------------------
# Industry Baselines
# ---------------------------------------------------------------------------

# Baselines are structured as: { metric_name: { stage: (low, median, high) } }
# "low" = bottom quartile, "median" = 50th percentile, "high" = top quartile

BASELINES: Dict[str, Dict[str, tuple]] = {
    # -- Gross Margin (%) --
    "gross_margin": {
        "saas": (60.0, 72.0, 85.0),
        "ecommerce": (25.0, 40.0, 55.0),
        "fintech": (45.0, 60.0, 75.0),
        "marketplace": (15.0, 30.0, 50.0),
        "default": (30.0, 55.0, 75.0),
    },

    # -- Monthly Revenue Growth (% MoM) --
    "revenue_growth_mom": {
        "seed": (5.0, 15.0, 30.0),
        "series_a": (5.0, 10.0, 20.0),
        "series_b_plus": (3.0, 8.0, 15.0),
        "default": (5.0, 12.0, 25.0),
    },

    # -- Burn Multiple (Burn / Net New ARR) --
    # Lower is better → inverted scoring
    "burn_multiple": {
        "seed": (3.0, 2.0, 1.0),       # Note: inverted (high, median, low)
        "series_a": (2.5, 1.5, 0.8),
        "series_b_plus": (2.0, 1.2, 0.6),
        "default": (3.0, 2.0, 1.0),
    },

    # -- LTV/CAC Ratio --
    "ltv_cac_ratio": {
        "saas": (2.0, 3.5, 6.0),
        "ecommerce": (1.5, 2.5, 4.0),
        "default": (2.0, 3.0, 5.0),
    },

    # -- Churn Rate (% monthly) --
    # Lower is better → inverted scoring
    "churn_rate": {
        "saas_b2b": (5.0, 3.0, 1.5),   # Inverted
        "saas_b2c": (8.0, 5.0, 3.0),
        "default": (7.0, 4.0, 2.0),
    },

    # -- Runway (months) --
    "runway": {
        "seed": (6.0, 12.0, 24.0),
        "series_a": (9.0, 18.0, 30.0),
        "default": (6.0, 12.0, 24.0),
    },

    # -- Net Margin (%) --
    # Startups are often negative — benchmarks reflect that
    "net_margin": {
        "early_stage": (-60.0, -25.0, 5.0),
        "growth": (-30.0, -5.0, 15.0),
        "default": (-50.0, -15.0, 10.0),
    },

    # -- ESOP Pool (%) --
    "esop_pool": {
        "seed": (5.0, 10.0, 15.0),
        "series_a": (8.0, 12.0, 18.0),
        "default": (5.0, 10.0, 15.0),
    },
}


# ---------------------------------------------------------------------------
# Stage-to-Benchmark-Key Mapping
# ---------------------------------------------------------------------------

def _get_benchmark_key(stage: StartupStage) -> str:
    """Map a startup stage to a benchmark category key."""
    if stage == StartupStage.IDEA:
        return "seed"
    elif stage == StartupStage.MVP:
        return "seed"
    elif stage == StartupStage.EARLY_REVENUE:
        return "series_a"
    else:
        return "series_b_plus"


# ---------------------------------------------------------------------------
# Assessment Logic
# ---------------------------------------------------------------------------

def _assess(
    value: float,
    low: float,
    median: float,
    high: float,
    inverted: bool = False,
) -> str:
    """
    Assess a metric value against benchmark thresholds.
    
    Normal (higher is better):
        >= high   → "excellent"
        >= median → "good"
        >= low    → "average"
        < low     → "below_average" or "concerning"
    
    Inverted (lower is better, e.g., churn, burn multiple):
        <= high   → "excellent"   (high here is actually the lowest/best value)
        <= median → "good"
        <= low    → "average"
        > low     → "concerning"
    """
    if inverted:
        # For inverted metrics, the tuple is (worst, median, best)
        if value <= high:
            return "excellent"
        elif value <= median:
            return "good"
        elif value <= low:
            return "average"
        elif value <= low * 1.3:
            return "below_average"
        else:
            return "concerning"
    else:
        if value >= high:
            return "excellent"
        elif value >= median:
            return "good"
        elif value >= low:
            return "average"
        elif value >= low * 0.7:
            return "below_average"
        else:
            return "concerning"


# ---------------------------------------------------------------------------
# Main Benchmarking Function
# ---------------------------------------------------------------------------

def benchmark_metrics(
    stage: StartupStage,
    metrics: ExtractedMetrics,
    industry: str = "default",
) -> List[BenchmarkResult]:
    """
    Compare extracted metrics against industry baselines.
    
    This is the main entry point for Layer 3 benchmarking.
    
    Args:
        stage: Detected startup stage (affects which baselines to use).
        metrics: Extracted financial metrics from Layer 1.
        industry: Industry vertical hint (e.g., "saas", "ecommerce").
                  Defaults to "default" if unknown.
        
    Returns:
        List of BenchmarkResult objects, one per benchmarked metric.
    """
    results: List[BenchmarkResult] = []
    stage_key = _get_benchmark_key(stage)
    industry_lower = industry.lower().strip()

    logger.info(
        "Running benchmarks: stage=%s (key=%s), industry=%s",
        stage.label, stage_key, industry_lower,
    )

    # --- Gross Margin ---
    if metrics.gross_margin_pct is not None:
        baseline = _get_baseline("gross_margin", industry_lower)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.gross_margin_pct, low, median, high)
            results.append(BenchmarkResult(
                category="gross_margin",
                actual_value=metrics.gross_margin_pct,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"Industry median: {median}%. Startup: {metrics.gross_margin_pct}%.",
            ))

    # --- Revenue Growth (MoM) ---
    if metrics.revenue_growth_mom is not None:
        baseline = _get_baseline("revenue_growth_mom", stage_key)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.revenue_growth_mom, low, median, high)
            results.append(BenchmarkResult(
                category="revenue_growth",
                actual_value=metrics.revenue_growth_mom,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"Stage median growth: {median}% MoM. Startup: {metrics.revenue_growth_mom}%.",
            ))

    # --- Burn Multiple ---
    if (
        metrics.monthly_burn_rate is not None
        and metrics.monthly_revenue is not None
        and metrics.monthly_revenue > 0
    ):
        burn_multiple = metrics.monthly_burn_rate / metrics.monthly_revenue
        baseline = _get_baseline("burn_multiple", stage_key)
        if baseline:
            low, median, high = baseline
            assessment = _assess(burn_multiple, low, median, high, inverted=True)
            results.append(BenchmarkResult(
                category="burn_multiple",
                actual_value=round(burn_multiple, 2),
                benchmark_low=high,   # Note: inverted — "low" benchmark is best
                benchmark_high=low,
                assessment=assessment,
                notes=f"Burn multiple: {burn_multiple:.1f}x (median: {median}x). Lower is better.",
            ))

    # --- LTV/CAC Ratio ---
    if metrics.ltv_cac_ratio is not None:
        baseline = _get_baseline("ltv_cac_ratio", industry_lower)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.ltv_cac_ratio, low, median, high)
            results.append(BenchmarkResult(
                category="ltv_cac_ratio",
                actual_value=metrics.ltv_cac_ratio,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"LTV/CAC: {metrics.ltv_cac_ratio:.1f}x (healthy: >{median}x).",
            ))

    # --- Churn Rate ---
    if metrics.churn_rate_pct is not None:
        baseline = _get_baseline("churn_rate", industry_lower)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.churn_rate_pct, low, median, high, inverted=True)
            results.append(BenchmarkResult(
                category="churn_rate",
                actual_value=metrics.churn_rate_pct,
                benchmark_low=high,   # Inverted
                benchmark_high=low,
                assessment=assessment,
                notes=f"Monthly churn: {metrics.churn_rate_pct}% (median: {median}%). Lower is better.",
            ))

    # --- Runway ---
    if metrics.runway_months is not None:
        baseline = _get_baseline("runway", stage_key)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.runway_months, low, median, high)
            results.append(BenchmarkResult(
                category="runway",
                actual_value=metrics.runway_months,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"Runway: {metrics.runway_months:.1f} months (healthy: >{median} months).",
            ))

    # --- Net Margin ---
    if metrics.net_margin_pct is not None:
        nm_key = "early_stage" if stage.value <= 1 else "growth"
        baseline = _get_baseline("net_margin", nm_key)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.net_margin_pct, low, median, high)
            results.append(BenchmarkResult(
                category="net_margin",
                actual_value=metrics.net_margin_pct,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"Net margin: {metrics.net_margin_pct}% (stage median: {median}%).",
            ))

    # --- ESOP Pool ---
    if metrics.total_esop_pct is not None:
        baseline = _get_baseline("esop_pool", stage_key)
        if baseline:
            low, median, high = baseline
            assessment = _assess(metrics.total_esop_pct, low, median, high)
            results.append(BenchmarkResult(
                category="esop_pool",
                actual_value=metrics.total_esop_pct,
                benchmark_low=low,
                benchmark_high=high,
                assessment=assessment,
                notes=f"ESOP pool: {metrics.total_esop_pct}% (benchmark: {low}-{high}%).",
            ))

    logger.info("Benchmarking complete: %d metrics compared", len(results))
    return results


def _get_baseline(
    metric: str,
    key: str,
) -> Optional[tuple]:
    """
    Look up a baseline tuple (low, median, high) for a metric.
    
    Falls back to the "default" key if the specific key isn't found.
    """
    baselines = BASELINES.get(metric)
    if not baselines:
        return None

    if key in baselines:
        return baselines[key]
    return baselines.get("default")


# --- SCORING ENGINE ---



# ---------------------------------------------------------------------------
# Score Calculation Helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    """Clamp a float to [low, high] and round to int."""
    return int(round(max(low, min(high, value))))


def _score_from_range(
    value: Optional[float],
    bad: float,
    good: float,
    excellent: float,
    invert: bool = False,
) -> Optional[int]:
    """
    Convert a raw metric value to a 0-100 score using three thresholds.
    
    Normal (higher is better):
        value <= bad       → 20
        value == good      → 60
        value >= excellent → 95
        Linear interpolation between thresholds.
    
    Inverted (lower is better, e.g., churn rate):
        value >= bad       → 20
        value == good      → 60
        value <= excellent → 95
    """
    if value is None:
        return None

    if invert:
        value = -value
        bad = -bad
        good = -good
        excellent = -excellent

    if value <= bad:
        return 20
    elif value <= good:
        # Interpolate 20 → 60
        ratio = (value - bad) / (good - bad) if good != bad else 0
        return _clamp(20 + ratio * 40)
    elif value <= excellent:
        # Interpolate 60 → 95
        ratio = (value - good) / (excellent - good) if excellent != good else 0
        return _clamp(60 + ratio * 35)
    else:
        return 95


# ---------------------------------------------------------------------------
# Category Scorers
# ---------------------------------------------------------------------------

def score_founder_strength(
    ai_extraction: Optional[AIExtractionResult],
    metrics: Optional[ExtractedMetrics],
) -> int:
    """
    Score founder / team quality based on:
        - Domain expertise (from AI analysis of pitch deck)
        - Team size / founder count
        - Background quality
        - Cap table structure (ESOP allocation signals professionalism)
    """
    scores: List[int] = []

    if ai_extraction:
        # Domain expertise score from AI (0-100)
        if ai_extraction.domain_expertise_score is not None:
            scores.append(ai_extraction.domain_expertise_score)

        # Founder count — 2-3 is ideal
        if ai_extraction.founder_count is not None:
            fc = ai_extraction.founder_count
            if fc == 0:
                scores.append(10)
            elif fc == 1:
                scores.append(55)  # Solo founder — higher risk
            elif fc in (2, 3):
                scores.append(85)  # Sweet spot
            elif fc <= 5:
                scores.append(70)
            else:
                scores.append(50)  # Too many founders can be a red flag

        # Backgrounds provided — having documented backgrounds is a positive signal
        if ai_extraction.founder_backgrounds:
            bg_score = min(90, 50 + len(ai_extraction.founder_backgrounds) * 15)
            scores.append(bg_score)

    if metrics:
        # ESOP allocation — having a pool signals maturity
        if metrics.total_esop_pct is not None:
            esop_score = _score_from_range(metrics.total_esop_pct, 0, 8, 15)
            if esop_score is not None:
                scores.append(esop_score)

    if not scores:
        # No data → neutral score
        return 50

    return _clamp(sum(scores) / len(scores))


def score_financial_health(
    metrics: Optional[ExtractedMetrics],
    benchmarks: List[BenchmarkResult],
) -> int:
    """
    Score financial health based on:
        - Runway (months of cash remaining)
        - Burn rate vs revenue (burn multiple)
        - Gross margin
        - Cash position
        - Debt levels
    """
    scores: List[int] = []

    if metrics:
        # Runway scoring
        runway_score = _score_from_range(metrics.runway_months, 3, 12, 24)
        if runway_score is not None:
            scores.append(runway_score)

        # Gross margin scoring
        gm_score = _score_from_range(metrics.gross_margin_pct, 20, 55, 75)
        if gm_score is not None:
            scores.append(gm_score)

        # Net margin (many startups are negative — be lenient)
        if metrics.net_margin_pct is not None:
            nm = metrics.net_margin_pct
            if nm >= 10:
                scores.append(90)
            elif nm >= 0:
                scores.append(70)
            elif nm >= -20:
                scores.append(50)
            elif nm >= -50:
                scores.append(35)
            else:
                scores.append(20)

        # Burn multiple (burn / net new ARR) — lower is better
        if (
            metrics.monthly_burn_rate is not None
            and metrics.monthly_revenue is not None
            and metrics.monthly_revenue > 0
        ):
            burn_multiple = metrics.monthly_burn_rate / metrics.monthly_revenue
            bm_score = _score_from_range(burn_multiple, 4.0, 2.0, 1.0, invert=True)
            if bm_score is not None:
                scores.append(bm_score)

        # Debt-to-equity concern
        if metrics.total_debt is not None and metrics.cash_on_hand is not None:
            if metrics.cash_on_hand > 0:
                debt_ratio = metrics.total_debt / metrics.cash_on_hand
                if debt_ratio > 2:
                    scores.append(25)
                elif debt_ratio > 1:
                    scores.append(45)
                elif debt_ratio > 0.5:
                    scores.append(65)
                else:
                    scores.append(85)

    # Incorporate benchmark scores
    for bm in benchmarks:
        if bm.category in ("gross_margin", "burn_multiple", "runway"):
            assessment_scores = {
                "excellent": 95,
                "good": 80,
                "average": 60,
                "below_average": 40,
                "concerning": 20,
            }
            if bm.assessment in assessment_scores:
                scores.append(assessment_scores[bm.assessment])

    if not scores:
        return 50

    return _clamp(sum(scores) / len(scores))





def score_product_maturity(
    metrics: Optional[ExtractedMetrics],
    ai_extraction: Optional[AIExtractionResult],
    stage: StartupStage,
) -> int:
    """
    Score product maturity and traction based on:
        - User / customer count
        - Revenue per user (ARPU)
        - Churn rate
        - LTV/CAC ratio
        - Product stage (from AI analysis)
    """
    scores: List[int] = []

    if metrics:
        # Customer count (stage-adjusted)
        if metrics.paying_customers is not None:
            if stage == StartupStage.IDEA:
                # At idea stage, even 1 customer is impressive
                cust_score = _score_from_range(metrics.paying_customers, 0, 5, 20)
            elif stage == StartupStage.MVP:
                cust_score = _score_from_range(metrics.paying_customers, 5, 50, 200)
            else:
                cust_score = _score_from_range(metrics.paying_customers, 20, 200, 1000)
            if cust_score is not None:
                scores.append(cust_score)

        # LTV/CAC ratio — higher is better
        ltv_cac_score = _score_from_range(metrics.ltv_cac_ratio, 1.0, 3.0, 5.0)
        if ltv_cac_score is not None:
            scores.append(ltv_cac_score)

        # Churn rate — lower is better
        churn_score = _score_from_range(metrics.churn_rate_pct, 10, 5, 2, invert=True)
        if churn_score is not None:
            scores.append(churn_score)

        # ARPU — having measurable ARPU is a positive signal
        if metrics.arpu is not None and metrics.arpu > 0:
            scores.append(70)  # Base score for having ARPU data
        elif metrics.total_users and metrics.total_users > 0 and metrics.mrr:
            # Can compute ARPU
            arpu = metrics.mrr / metrics.total_users
            if arpu > 0:
                scores.append(65)

    # Product stage signals from AI
    if ai_extraction and ai_extraction.key_differentiators:
        diff_count = len(ai_extraction.key_differentiators)
        scores.append(min(85, 40 + diff_count * 12))

    if not scores:
        return 50

    return _clamp(sum(scores) / len(scores))


def score_market_opportunity(
    ai_extraction: Optional[AIExtractionResult],
    benchmarks: List[BenchmarkResult],
) -> int:
    """
    Score market opportunity based on:
        - TAM/SAM/SOM presence and reasonableness
        - Competitive positioning analysis
        - Market benchmark comparisons
    """
    scores: List[int] = []

    if ai_extraction:
        # TAM/SAM/SOM completeness
        tam_fields = [ai_extraction.tam, ai_extraction.sam, ai_extraction.som]
        provided_count = sum(1 for f in tam_fields if f)
        if provided_count == 3:
            scores.append(85)  # All three provided
        elif provided_count == 2:
            scores.append(65)
        elif provided_count == 1:
            scores.append(45)
        else:
            scores.append(25)  # No market sizing at all

        # Business model clarity
        if ai_extraction.business_model:
            scores.append(75)  # Having a clear business model documented
        else:
            scores.append(35)

        # Competitive positioning
        if ai_extraction.competitive_positioning:
            scores.append(75)
        else:
            scores.append(40)

    # Market-related benchmarks
    for bm in benchmarks:
        if bm.category in ("market_growth", "tam_reasonableness"):
            assessment_scores = {
                "excellent": 95,
                "good": 80,
                "average": 60,
                "below_average": 40,
                "concerning": 20,
            }
            if bm.assessment in assessment_scores:
                scores.append(assessment_scores[bm.assessment])

    if not scores:
        return 50

    return _clamp(sum(scores) / len(scores))


# ---------------------------------------------------------------------------
# Main Scoring Function
# ---------------------------------------------------------------------------

def calculate_all_scores(
    stage: StartupStage,
    metrics: Optional[ExtractedMetrics],
    ai_extraction: Optional[AIExtractionResult],
    benchmarks: List[BenchmarkResult],
) -> int:
    logger.info("Calculating scores for stage: %s", stage.label)

    # Calculate individual category scores
    founder = score_founder_strength(ai_extraction, metrics)
    financial = score_financial_health(metrics, benchmarks)
    product = score_product_maturity(metrics, ai_extraction, stage)
    market = score_market_opportunity(ai_extraction, benchmarks)
    revenue = 50  # Default or simple calculation since verification is gone

    # Calculate weighted composite score
    weights = get_scoring_weights(stage)
    category_scores = {
        "founder": float(founder),
        "market": float(market),
        "product": float(product),
        "financial": float(financial),
        "unit_economics": float(revenue),
    }

    investment_readiness = weights.weighted_score(category_scores)

    return investment_readiness

