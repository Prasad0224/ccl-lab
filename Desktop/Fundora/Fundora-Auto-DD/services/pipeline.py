"""
Fundora Auto DD Engine — 5-Layer Processing Pipeline
=====================================================
Orchestrates the complete due diligence analysis across five sequential layers:

    Layer 1: Deterministic Rules Engine (Python/Pandas)
             Parse documents, compute formulas (runway, ESOP%, burn rate)

    Layer 2: AI Extraction & Verification (Gemini preferred)
             Extract TAM, business model, positioning from pitch deck.
             Cross-reference P&L vs Bank Statements vs GST returns.

    Layer 3: Benchmarking
             Compare metrics against industry baselines.

    Layer 4: Risk Scoring (OpenAI preferred)
             Calculate category scores with stage-adaptive weights.

    Layer 5: Output Generation
             Generate insights narrative, assemble AutoDDResponse.

Each layer enriches a shared PipelineContext object. If a layer fails,
the pipeline continues with degraded results rather than crashing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

from fastapi import UploadFile

from models.schemas import (
    AIExtractionResult,
    AutoDDResponse,
    BenchmarkResult,
    ParsedDocument,
    PipelineContext,
    BusinessModel,
    Financials,
    Traction,
    MarketSize,
    Team,
    CapTable,
    RiskFlags,
    ExtractedMetrics,
    Verification,
)
from services.stages import DocumentType, StartupStage, classify_stage
from prompts.templates import (
    ANOMALY_DETECTION_PROMPT, REVENUE_VERIFICATION_PROMPT,
    FINANCIAL_EXTRACTION_PROMPT, PITCH_DECK_EXTRACTION_PROMPT,
    EXECUTIVE_SUMMARY_PROMPT, INSIGHT_GENERATION_PROMPT
)
from services.scoring import benchmark_metrics, calculate_all_scores
from utils.llm_orchestrator import get_orchestrator
from utils.parsers import parse_uploaded_file

logger = logging.getLogger("auto_dd.pipeline")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def run_pipeline(
    files: List[UploadFile],
    startup_id: Optional[str] = None,
) -> AutoDDResponse:
    """
    Run the complete 5-layer due diligence pipeline.

    This is the main entry point called by the /analyze-startup endpoint.

    Args:
        files:      List of uploaded startup documents.
        startup_id: Optional alphanumeric startup identifier (e.g. "S-001").
                    Used to name the persisted report file as RPT-{startup_id}.json.

    Returns:
        Complete AutoDDResponse matching the frontend schema.
    """
    start_time = time.time()

    # Initialize the pipeline context, threading the caller-supplied ID
    ctx = PipelineContext(startup_id=startup_id)

    logger.info(
        "═══ Starting Auto DD Pipeline (files: %d, startup_id: %s) ═══",
        len(files),
        startup_id or "<unset>",
    )

    # ── Layer 0: Parse & Classify Documents ──────────────────────────────
    ctx = await _layer_0_parse_documents(ctx, files)

    # ── Stage Classification ─────────────────────────────────────────────
    ctx = await _classify_stage(ctx)

    # ── Layer 1: Deterministic Rules Engine ──────────────────────────────
    ctx = _layer_1_deterministic_extraction(ctx)

    # ── Layer 2: AI Extraction & Verification (parallel LLM calls) ───────
    ctx = await _layer_2_ai_extraction(ctx)

    # ── Layer 3: Benchmarking ────────────────────────────────────────────
    ctx = _layer_3_benchmarking(ctx)

    # ── Layer 4: Risk Scoring ────────────────────────────────────────────
    ctx = _layer_4_scoring(ctx)

    # ── Layer 5: Output Generation ───────────────────────────────────────
    ctx = await _layer_5_output_generation(ctx)

    # ── Confidence Interval (deterministic — based on successfully parsed docs) ──
    ctx.confidence_interval = _compute_confidence_interval(len(ctx.parsed_documents))

    # ── Assemble Final Response ──────────────────────────────────────────
    ctx.processing_time_seconds = round(time.time() - start_time, 2)

    response = _assemble_response(ctx)

    logger.info(
        "═══ Pipeline complete in %.2fs | IRS=%d | confidence=%s ═══",
        ctx.processing_time_seconds,
        response.investor_readiness,
        ctx.confidence_interval,
    )

    return response


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 0: Document Parsing
# ═══════════════════════════════════════════════════════════════════════════

async def _layer_0_parse_documents(
    ctx: PipelineContext,
    files: List[UploadFile],
) -> PipelineContext:
    """Parse all uploaded files and classify document types."""
    logger.info("── Layer 0: Parsing %d documents ──", len(files))

    for file in files:
        try:
            parsed = await parse_uploaded_file(file)
            ctx.parsed_documents.append(parsed)

            if parsed.parse_errors:
                for err in parsed.parse_errors:
                    ctx.warnings.append(f"[{parsed.filename}] {err}")

            logger.info(
                "  Parsed: %s → type=%s, text=%d chars, tables=%d",
                parsed.filename,
                parsed.document_type,
                len(parsed.raw_text) if parsed.raw_text else 0,
                len(parsed.tables) if parsed.tables else 0,
            )
        except Exception as e:
            err_msg = f"Failed to parse '{file.filename}': {e}"
            logger.error(err_msg)
            ctx.errors.append(err_msg)

    logger.info("  Parsed %d/%d documents successfully", len(ctx.parsed_documents), len(files))
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# STAGE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

async def _classify_stage(ctx: PipelineContext) -> PipelineContext:
    """Classify the startup's maturity stage."""
    logger.info("── Stage Classification ──")

    try:
        stage, confidence = await classify_stage(ctx.parsed_documents)
        ctx.detected_stage = stage.label
        ctx.stage_confidence = confidence
        logger.info("  Stage: %s (confidence: %d%%)", stage.label, confidence)
    except Exception as e:
        logger.error("  Stage classification failed: %s", e)
        ctx.detected_stage = StartupStage.IDEA.label
        ctx.stage_confidence = 30
        ctx.errors.append(f"Stage classification failed: {e}")

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1: Deterministic Rules Engine
# ═══════════════════════════════════════════════════════════════════════════

def _layer_1_deterministic_extraction(ctx: PipelineContext) -> PipelineContext:
    """
    Extract metrics deterministically from parsed tabular data.
    
    Computes:
        - Runway = Cash / Monthly Burn
        - ESOP %, Founder %, Investor %
        - Revenue, Margins, Burn Rate
        - Unit Economics (CAC, LTV, Churn, ARPU)
    """
    logger.info("── Layer 1: Deterministic Extraction ──")

    metrics = ExtractedMetrics()

    for doc in ctx.parsed_documents:
        if not doc.tables:
            continue

        try:
            if doc.document_type == DocumentType.CAP_TABLE:
                _extract_cap_table_metrics(doc, metrics)
            elif doc.document_type == DocumentType.KPI_SHEET:
                _extract_kpi_metrics(doc, metrics)
            elif doc.document_type == DocumentType.HISTORICAL_PNL:
                _extract_pnl_metrics(doc, metrics)
            elif doc.document_type == DocumentType.BALANCE_SHEET:
                _extract_balance_sheet_metrics(doc, metrics)
            elif doc.document_type == DocumentType.FINANCIAL_PROJECTIONS:
                _extract_projection_metrics(doc, metrics)
        except Exception as e:
            err_msg = f"Layer 1 extraction failed for {doc.filename}: {e}"
            logger.warning(err_msg)
            ctx.warnings.append(err_msg)

    # Computed metrics
    _compute_derived_metrics(metrics)

    ctx.extracted_metrics = metrics
    logger.info("  Extracted metrics: %s", _summarize_metrics(metrics))
    return ctx


def _extract_cap_table_metrics(doc: ParsedDocument, metrics: ExtractedMetrics) -> None:
    """Extract equity distribution from cap table."""
    import pandas as pd

    for table_data in doc.tables:
        df = pd.DataFrame(table_data)
        columns_lower = {str(c).lower(): c for c in df.columns}

        # Look for percentage/equity columns
        pct_col = None
        for keyword in ["percentage", "percent", "%", "equity", "share"]:
            for col_lower, col_orig in columns_lower.items():
                if keyword in col_lower:
                    pct_col = col_orig
                    break
            if pct_col:
                break

        if pct_col is None:
            continue

        # Look for type/category column
        type_col = None
        for keyword in ["type", "category", "name", "shareholder", "holder"]:
            for col_lower, col_orig in columns_lower.items():
                if keyword in col_lower:
                    type_col = col_orig
                    break
            if type_col:
                break

        if type_col is None:
            type_col = df.columns[0]  # Default to first column

        # Parse rows
        for _, row in df.iterrows():
            try:
                label = str(row[type_col]).lower()
                value = pd.to_numeric(row[pct_col], errors="coerce")
                if pd.isna(value):
                    continue

                if any(k in label for k in ["esop", "option", "pool"]):
                    metrics.total_esop_pct = (metrics.total_esop_pct or 0) + value
                elif any(k in label for k in ["founder", "promoter"]):
                    metrics.founder_equity_pct = (metrics.founder_equity_pct or 0) + value
                elif any(k in label for k in ["investor", "vc", "angel", "fund"]):
                    metrics.investor_equity_pct = (metrics.investor_equity_pct or 0) + value
            except Exception:
                continue

    # Compute total dilution
    if metrics.founder_equity_pct is not None:
        metrics.total_dilution_pct = round(100 - metrics.founder_equity_pct, 2)


def _extract_kpi_metrics(doc: ParsedDocument, metrics: ExtractedMetrics) -> None:
    """Extract KPI metrics (users, churn, ARPU, etc.)."""
    import pandas as pd

    for table_data in doc.tables:
        df = pd.DataFrame(table_data)

        # Search for metric values in the table
        for _, row in df.iterrows():
            try:
                row_str = " ".join(str(v).lower() for v in row.values if pd.notna(v))

                # Try to extract specific metrics
                _try_extract_metric(row, row_str, "dau", "mau", "users", "active",
                                    target=lambda v: setattr(metrics, "total_users", int(v)))
                _try_extract_metric(row, row_str, "paying", "customer",
                                    target=lambda v: setattr(metrics, "paying_customers", int(v)))
                _try_extract_metric(row, row_str, "churn",
                                    target=lambda v: setattr(metrics, "churn_rate_pct", float(v)))
                _try_extract_metric(row, row_str, "arpu",
                                    target=lambda v: setattr(metrics, "arpu", float(v)))
                _try_extract_metric(row, row_str, "cac", "acquisition cost",
                                    target=lambda v: setattr(metrics, "cac", float(v)))
                _try_extract_metric(row, row_str, "ltv", "lifetime value",
                                    target=lambda v: setattr(metrics, "ltv", float(v)))
                _try_extract_metric(row, row_str, "mrr", "monthly recurring",
                                    target=lambda v: setattr(metrics, "mrr", float(v)))
            except Exception:
                continue


def _try_extract_metric(row, row_str: str, *keywords, target) -> None:
    """Try to extract a numeric metric from a row if keywords match."""
    import pandas as pd

    if not any(k in row_str for k in keywords):
        return

    # Find the last numeric value in the row (usually the most recent period)
    for val in reversed(list(row.values)):
        numeric = pd.to_numeric(val, errors="coerce")
        if pd.notna(numeric) and numeric != 0:
            target(numeric)
            return


def _extract_pnl_metrics(doc: ParsedDocument, metrics: ExtractedMetrics) -> None:
    """Extract P&L metrics (revenue, expenses, margins)."""
    import pandas as pd

    for table_data in doc.tables:
        df = pd.DataFrame(table_data)

        for _, row in df.iterrows():
            try:
                row_str = " ".join(str(v).lower() for v in row.values if pd.notna(v))

                # Get the last numeric value (most recent period)
                numerics = []
                for val in row.values:
                    n = pd.to_numeric(val, errors="coerce")
                    if pd.notna(n):
                        numerics.append(n)

                if not numerics:
                    continue

                latest = numerics[-1]

                if any(k in row_str for k in ["total revenue", "net revenue", "gross revenue"]):
                    metrics.monthly_revenue = latest
                elif any(k in row_str for k in ["gross profit", "gross margin"]):
                    if metrics.monthly_revenue and metrics.monthly_revenue > 0:
                        metrics.gross_margin_pct = round(
                            (latest / metrics.monthly_revenue) * 100, 2
                        )
                elif any(k in row_str for k in ["net income", "net profit", "net loss", "pat"]):
                    if metrics.monthly_revenue and metrics.monthly_revenue > 0:
                        metrics.net_margin_pct = round(
                            (latest / metrics.monthly_revenue) * 100, 2
                        )
                elif any(k in row_str for k in ["operating expense", "total expense", "opex"]):
                    metrics.monthly_burn_rate = abs(latest)
            except Exception:
                continue


def _extract_balance_sheet_metrics(doc: ParsedDocument, metrics: ExtractedMetrics) -> None:
    """Extract balance sheet metrics (cash, debt)."""
    import pandas as pd

    for table_data in doc.tables:
        df = pd.DataFrame(table_data)

        for _, row in df.iterrows():
            try:
                row_str = " ".join(str(v).lower() for v in row.values if pd.notna(v))
                numerics = [
                    pd.to_numeric(v, errors="coerce")
                    for v in row.values
                    if pd.notna(pd.to_numeric(v, errors="coerce"))
                ]

                if not numerics:
                    continue

                latest = numerics[-1]

                if any(k in row_str for k in ["cash", "bank balance", "cash equivalent"]):
                    metrics.cash_on_hand = latest
                elif any(k in row_str for k in ["total debt", "borrowing", "loan"]):
                    metrics.total_debt = abs(latest)
            except Exception:
                continue


def _extract_projection_metrics(doc: ParsedDocument, metrics: ExtractedMetrics) -> None:
    """Extract financial projection data (primarily for MoM growth estimation)."""
    import pandas as pd

    for table_data in doc.tables:
        df = pd.DataFrame(table_data)

        # Look for revenue row with multiple period columns
        for _, row in df.iterrows():
            try:
                row_str = " ".join(str(v).lower() for v in row.values if pd.notna(v))
                if not any(k in row_str for k in ["revenue", "sales", "income"]):
                    continue

                numerics = [
                    pd.to_numeric(v, errors="coerce")
                    for v in row.values
                    if pd.notna(pd.to_numeric(v, errors="coerce")) and pd.to_numeric(v, errors="coerce") > 0
                ]

                if len(numerics) >= 2:
                    # Calculate MoM growth from last two periods
                    prev, curr = numerics[-2], numerics[-1]
                    if prev > 0:
                        growth = ((curr - prev) / prev) * 100
                        metrics.revenue_growth_mom = round(growth, 2)
                    if metrics.annual_revenue is None:
                        metrics.annual_revenue = curr * 12  # Annualize latest
            except Exception:
                continue


def _compute_derived_metrics(metrics: ExtractedMetrics) -> None:
    """Compute derived metrics from the extracted raw values."""
    # Runway = Cash / Monthly Burn
    if metrics.cash_on_hand is not None and metrics.monthly_burn_rate is not None:
        if metrics.monthly_burn_rate > 0:
            metrics.runway_months = round(
                metrics.cash_on_hand / metrics.monthly_burn_rate, 1
            )

    # LTV/CAC Ratio
    if metrics.ltv is not None and metrics.cac is not None and metrics.cac > 0:
        metrics.ltv_cac_ratio = round(metrics.ltv / metrics.cac, 2)

    # Annual revenue from monthly
    if metrics.annual_revenue is None and metrics.monthly_revenue is not None:
        metrics.annual_revenue = metrics.monthly_revenue * 12

    # MRR from monthly revenue if not set
    if metrics.mrr is None and metrics.monthly_revenue is not None:
        metrics.mrr = metrics.monthly_revenue


def _summarize_metrics(metrics: ExtractedMetrics) -> str:
    """Create a short summary of which metrics were extracted."""
    fields = metrics.model_dump(exclude={"raw_metrics"})
    extracted = [k for k, v in fields.items() if v is not None]
    return f"{len(extracted)} metrics: {', '.join(extracted[:8])}{'...' if len(extracted) > 8 else ''}"


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2: AI Extraction & Verification  (asyncio.gather parallel calls)
# ═══════════════════════════════════════════════════════════════════════════

async def _layer_2_ai_extraction(ctx: PipelineContext) -> PipelineContext:
    """
    AI-powered extraction from pitch deck + revenue verification.

    Performance optimisation (asyncio.gather):
        The two LLM network calls — pitch deck extraction (Gemini) and revenue
        cross-verification — have NO data dependency on each other. They are
        fired concurrently using asyncio.gather, cutting Layer 2 wall-clock
        time by ~50% on runs with both pitch deck + financial documents.

        Layer 5 (synthesis) depends on the combined output of this layer, so
        L2↔L5 concurrency is intentionally NOT attempted.
    """
    logger.info("── Layer 2: AI Extraction & Verification (parallel) ──")

    orchestrator = get_orchestrator()
    ai_result = AIExtractionResult()

    # Build task inputs
    pitch_deck_doc  = _find_document(ctx, DocumentType.PITCH_DECK)
    pnl_doc         = _find_document(ctx, DocumentType.HISTORICAL_PNL)
    bank_doc        = _find_document(ctx, DocumentType.BANK_STATEMENTS)
    gst_doc         = _find_document(ctx, DocumentType.GST_RETURNS)

    # ── Async Task Definitions ────────────────────────────────────────────
    async def _task_pitch_deck() -> dict:
        """Extract market, team, and positioning data from the pitch deck."""
        if not (pitch_deck_doc and pitch_deck_doc.raw_text):
            return {}
        metrics_summary = ""
        if ctx.extracted_metrics:
            fields = ctx.extracted_metrics.model_dump(exclude={"raw_metrics"})
            metrics_summary = "\n".join(
                f"- {k}: {v}" for k, v in fields.items() if v is not None
            )
        prompt = PITCH_DECK_EXTRACTION_PROMPT.format(
            pitch_deck_text=pitch_deck_doc.raw_text[:15_000],
            parsed_metrics=metrics_summary or "No metrics available yet.",
        )
        return await orchestrator.call_extraction_llm(prompt)

    async def _task_revenue_verification() -> dict:
        """Cross-reference P&L vs Bank vs GST for revenue credibility."""
        if not (pnl_doc and (bank_doc or gst_doc)):
            return {}
        prompt = REVENUE_VERIFICATION_PROMPT.format(
            pnl_data=_get_doc_content(pnl_doc, max_length=5000),
            bank_data=_get_doc_content(bank_doc, max_length=5000) if bank_doc else "Not provided",
            gst_data=_get_doc_content(gst_doc, max_length=5000) if gst_doc else "Not provided",
        )
        return await orchestrator.call_verification_llm(prompt)

    # ── Fire both LLM calls concurrently ─────────────────────────────────
    logger.info("  Firing pitch deck extraction + revenue verification in parallel...")
    pitch_result, verify_result = await asyncio.gather(
        _task_pitch_deck(),
        _task_revenue_verification(),
        return_exceptions=True,  # Ensures one failure does not cancel the other
    )

    # ── Process Pitch Deck Result ─────────────────────────────────────────
    if isinstance(pitch_result, Exception):
        logger.error("  Pitch deck extraction task raised: %s", pitch_result)
        ctx.warnings.append(f"Pitch deck AI extraction failed: {pitch_result}")
    elif pitch_result and "_error" not in pitch_result:
        ai_result.tam                   = pitch_result.get("tam")
        ai_result.sam                   = pitch_result.get("sam")
        ai_result.som                   = pitch_result.get("som")
        ai_result.business_model        = pitch_result.get("business_model")
        ai_result.competitive_positioning = pitch_result.get("competitive_positioning")
        ai_result.key_differentiators   = pitch_result.get("key_differentiators", [])
        ai_result.founder_count         = pitch_result.get("founder_count")
        ai_result.founder_backgrounds   = pitch_result.get("founder_backgrounds", [])
        ai_result.domain_expertise_score = pitch_result.get("domain_expertise_score")
        ai_result.legal_entity_name     = pitch_result.get("legal_entity_name")
        logger.info("  ✅ Pitch deck extracted (entity: %s)", ai_result.legal_entity_name or "N/A")
    elif pitch_deck_doc and pitch_deck_doc.raw_text:
        ctx.warnings.append("AI pitch deck extraction unavailable")

    # ── Process Revenue Verification Result ───────────────────────────────
    if isinstance(verify_result, Exception):
        logger.error("  Revenue verification task raised: %s", verify_result)
        ctx.warnings.append(f"Revenue verification failed: {verify_result}")
        ctx.verification = Verification(
            mrr_verified=False,
            confidence_percentage=0,
            verification_notes=f"Verification failed: {verify_result}",
        )
    elif verify_result and "_error" not in verify_result:
        ctx.verification = Verification(
            mrr_verified=verify_result.get("mrr_verified", False),
            confidence_percentage=verify_result.get("confidence_percentage", 50),
            verification_notes=verify_result.get("verification_notes", "Verification incomplete."),
        )
        for disc in verify_result.get("discrepancies", []):
            desc = disc.get("description", "")
            if disc.get("type") == "revenue_mismatch":
                ai_result.revenue_discrepancies.append(desc)
            elif "gst" in desc.lower():
                ai_result.gst_discrepancies.append(desc)
            elif "bank" in desc.lower():
                ai_result.bank_statement_flags.append(desc)

        severity_count = len(ai_result.revenue_discrepancies) + len(ai_result.gst_discrepancies)
        if severity_count == 0:
            ai_result.anomaly_severity = "none"
        elif severity_count <= 2:
            ai_result.anomaly_severity = "low"
        elif severity_count <= 4:
            ai_result.anomaly_severity = "medium"
        else:
            ai_result.anomaly_severity = "high"

        logger.info(
            "  ✅ Revenue verification complete (verified: %s, severity: %s)",
            ctx.verification.mrr_verified,
            ai_result.anomaly_severity,
        )
    else:
        # Insufficient docs or no LLM — set safe defaults
        ctx.verification = Verification(
            mrr_verified=False,
            confidence_percentage=0,
            verification_notes=(
                "Insufficient documents for revenue verification. "
                "Provide P&L along with Bank Statements and/or GST Returns."
            ),
        )
        ai_result.anomaly_severity = "none"

    # --- Entity Resolution: Cross-reference company name across documents ---
    ctx = _run_entity_resolution(ctx, ai_result)

    ctx.ai_extraction = ai_result
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3: Benchmarking
# ═══════════════════════════════════════════════════════════════════════════

def _layer_3_benchmarking(ctx: PipelineContext) -> PipelineContext:
    """Compare extracted metrics against industry baselines."""
    logger.info("── Layer 3: Benchmarking ──")

    if ctx.extracted_metrics is None:
        ctx.warnings.append("No metrics available for benchmarking")
        return ctx

    try:
        # Detect industry from AI extraction
        industry = "default"
        if ctx.ai_extraction and ctx.ai_extraction.business_model:
            bm_lower = ctx.ai_extraction.business_model.lower()
            if "saas" in bm_lower:
                industry = "saas"
            elif "ecommerce" in bm_lower or "e-commerce" in bm_lower:
                industry = "ecommerce"
            elif "fintech" in bm_lower:
                industry = "fintech"
            elif "marketplace" in bm_lower:
                industry = "marketplace"

        stage = _stage_from_label(ctx.detected_stage)
        ctx.benchmarks = benchmark_metrics(stage, ctx.extracted_metrics, industry)
        logger.info("  Benchmarked %d metrics (industry: %s)", len(ctx.benchmarks), industry)

    except Exception as e:
        logger.error("  Benchmarking failed: %s", e)
        ctx.warnings.append(f"Benchmarking failed: {e}")

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 4: Risk Scoring
# ═══════════════════════════════════════════════════════════════════════════

def _layer_4_scoring(ctx: PipelineContext) -> PipelineContext:
    """Calculate category scores using the scoring engine."""
    logger.info("── Layer 4: Risk Scoring ──")

    try:
        stage = _stage_from_label(ctx.detected_stage)
        ctx.investor_readiness = calculate_all_scores(
            stage=stage,
            metrics=ctx.extracted_metrics,
            ai_extraction=ctx.ai_extraction,
            benchmarks=ctx.benchmarks,
        )
        logger.info(
            "  Scores: IRS=%d",
            ctx.investor_readiness
        )
    except Exception as e:
        logger.error("  Scoring failed: %s", e)
        ctx.errors.append(f"Scoring failed: {e}")
        ctx.investor_readiness = 50

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 5: Output Generation
# ═══════════════════════════════════════════════════════════════════════════


async def _layer_5_output_generation(ctx: PipelineContext) -> PipelineContext:
    """Generate insights (red flags + strengths) using AI."""
    logger.info("── Layer 5: Output Generation ──")

    orchestrator = get_orchestrator()

    try:
        metrics_str = "No metrics extracted."
        if ctx.extracted_metrics:
            fields = ctx.extracted_metrics.model_dump(exclude={"raw_metrics"})
            metrics_str = "\n".join(f"- {k}: {v}" for k, v in fields.items() if v is not None)

        ai_extraction_str = "No AI extraction results."
        if ctx.ai_extraction:
            ai_extraction_str = ctx.ai_extraction.model_dump_json(indent=2)

        benchmarks_str = "No benchmarks available."
        if ctx.benchmarks:
            bench_lines = []
            for bm in ctx.benchmarks:
                bench_lines.append(
                    f"- {bm.category}: {bm.actual_value} → {bm.assessment} ({bm.notes})"
                )
            benchmarks_str = "\n".join(bench_lines)

        anomalies_str = "No anomaly detection performed."
        if ctx.ai_extraction:
            anomaly_items = (
                ctx.ai_extraction.revenue_discrepancies
                + ctx.ai_extraction.gst_discrepancies
                + ctx.ai_extraction.bank_statement_flags
            )
            if anomaly_items:
                anomalies_str = "\n".join(f"- {a}" for a in anomaly_items)
            else:
                anomalies_str = "No anomalies detected."

        prompt = INSIGHT_GENERATION_PROMPT.format(
            stage=ctx.detected_stage or "Unknown",
            scores="",
            metrics=metrics_str,
            ai_extraction=ai_extraction_str,
            anomalies=anomalies_str,
            benchmarks=benchmarks_str,
        )

        result = await orchestrator.call_scoring_llm(prompt)

        if "_error" not in result:
            ctx.investor_readiness = result.get("investor_readiness", 50)
            ctx.business_model = BusinessModel(**result.get("business_model", {}))
            ctx.traction = Traction(**result.get("traction", {}))
            ctx.market_size = MarketSize(**result.get("market_size", {}))
            ctx.team = Team(**result.get("team", {}))
            ctx.cap_table = CapTable(**result.get("cap_table", {}))
            ctx.risk_flags = RiskFlags(**result.get("risk_flags", {}))
            logger.info("  Generated final 8-section report")
        else:
            _generate_fallback_insights(ctx)
            ctx.warnings.append("AI insight generation unavailable, using rule-based fallback")

    except Exception as e:
        logger.error("  Insight generation failed: %s", e)
        _generate_fallback_insights(ctx)
        ctx.warnings.append(f"Insight generation failed: {e}")

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# RESPONSE ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def _run_entity_resolution(ctx: PipelineContext, ai_result: AIExtractionResult) -> PipelineContext:
    """
    Cross-reference the legal entity name extracted from the Pitch Deck
    against the account holder name found in Bank Statements or P&L data.
    If there is a mismatch, inject a critical red flag into the context.
    """
    extracted_name = (ai_result.legal_entity_name or "").strip().lower()
    if not extracted_name:
        return ctx

    financial_doc_types = [DocumentType.BANK_STATEMENTS, DocumentType.HISTORICAL_PNL]
    financial_texts = []
    for doc_type in financial_doc_types:
        doc = _find_document(ctx, doc_type)
        if doc and doc.raw_text:
            financial_texts.append(doc.raw_text[:3000].lower())

    if not financial_texts:
        return ctx

    full_financial_text = " ".join(financial_texts)[:20000].lower()
    name_tokens = [w for w in extracted_name.split() if len(w) > 3]
    if not name_tokens:
        return ctx

    matched = any(token in financial_text for token in name_tokens)
    if not matched:
        mismatch_flag = (
            f"CRITICAL — Entity Mismatch Detected: Pitch deck identifies company as "
            f"'{ai_result.legal_entity_name}', but no matching entity name was found in the "
            f"uploaded financial documents."
        )
        ai_result.revenue_discrepancies.insert(0, mismatch_flag)
        ctx.errors.append(mismatch_flag)

    return ctx


def _assemble_response(ctx: PipelineContext) -> AutoDDResponse:
    """Assemble the final AutoDDResponse from the pipeline context."""

    # Deterministically inject financials from Layer 1 extracted metrics
    mrr = 0.0
    burn_rate = 0.0
    runway_months = 0.0
    if ctx.extracted_metrics:
        mrr = ctx.extracted_metrics.mrr or 0.0
        burn_rate = ctx.extracted_metrics.monthly_burn_rate or 0.0
        runway_months = ctx.extracted_metrics.runway_months or 0.0

    ctx.financials = Financials(mrr=mrr, burn_rate=burn_rate, runway_months=runway_months)

    return AutoDDResponse(
        # Identity — threads the startup_id from the API caller through to the response
        startup_id=ctx.startup_id,
        confidence_interval=ctx.confidence_interval,
        # Core scoring
        investor_readiness=ctx.investor_readiness,
        business_model=ctx.business_model,
        financials=ctx.financials,
        traction=ctx.traction,
        market_size=ctx.market_size,
        team=ctx.team,
        cap_table=ctx.cap_table,
        risk_flags=ctx.risk_flags,
        # report_id is injected by main.py after persistence
        report_id=None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _find_document(
    ctx: PipelineContext,
    doc_type: str,
) -> Optional[ParsedDocument]:
    for doc in ctx.parsed_documents:
        if doc.document_type == doc_type:
            return doc
    return None


def _get_doc_content(doc: ParsedDocument, max_length: int = 5000) -> str:
    parts = []
    if doc.raw_text:
        parts.append(doc.raw_text[:max_length])
    return "\n".join(parts)[:max_length]


def _stage_from_label(label: Optional[str]) -> StartupStage:
    if label is None:
        return StartupStage.IDEA
    label_lower = label.lower()
    if "growth" in label_lower:
        return StartupStage.GROWTH
    elif "early" in label_lower or "revenue" in label_lower:
        return StartupStage.EARLY_REVENUE
    elif "mvp" in label_lower:
        return StartupStage.MVP
    return StartupStage.IDEA


def _generate_fallback_insights(ctx: PipelineContext):
    """Generate basic fallback data."""
    ctx.investor_readiness = 50
    ctx.business_model = BusinessModel(score=50, summary="Deterministic fallback data")
    ctx.traction = Traction(growth_rate="N/A", retention_notes="N/A")
    ctx.market_size = MarketSize(tam="N/A", sam="N/A", claims_validity="N/A")
    ctx.team = Team(founders_strength="N/A", identified_gaps="N/A")
    ctx.cap_table = CapTable(ownership_clarity="N/A", notes="N/A")

    red_flags = []
    if ctx.extracted_metrics and ctx.extracted_metrics.runway_months and ctx.extracted_metrics.runway_months < 6:
        red_flags.append(f"Runway critically low at {ctx.extracted_metrics.runway_months:.1f} months")

    ctx.risk_flags = RiskFlags(red_signals=red_flags, amber_signals=[])


def _compute_confidence_interval(parsed_doc_count: int) -> str:
    """
    Deterministic confidence interval heuristic.

    Calculates a confidence band based purely on the number of documents
    successfully parsed by Layer 0. More documents = more data sources =
    higher confidence in the synthesised report.

    Thresholds (per Lead Engineer directive):
        8-9 files parsed → High Confidence    (90-95%)
        4-7 files parsed → Moderate Confidence (70-85%)
        1-3 files parsed → Low Confidence     (40-65%)
        0   files parsed → No Data            (0%)

    Args:
        parsed_doc_count: Number of documents successfully parsed in Layer 0.

    Returns:
        A human-readable confidence interval string (e.g. "High Confidence (90-95%)").
    """
    if parsed_doc_count >= 8:
        return "High Confidence (90-95%)"
    elif parsed_doc_count >= 4:
        return "Moderate Confidence (70-85%)"
    elif parsed_doc_count >= 1:
        return "Low Confidence (40-65%)"
    else:
        return "No Data (0%)"

