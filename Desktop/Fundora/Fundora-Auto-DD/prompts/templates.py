"""
Fundora Auto DD — Prompt Templates
===================================
Contains all LLM prompts used across the Auto DD engine.
"""

# ---------------------------------------------------------------------------
# Pitch Deck Extraction
# ---------------------------------------------------------------------------
PITCH_DECK_EXTRACTION_PROMPT = """You are a senior venture capital analyst performing due diligence on a startup.
Analyze the following pitch deck content and extract structured information.

## PITCH DECK CONTENT:
{pitch_deck_text}

## ADDITIONAL METRICS (from parsed financial documents):
{parsed_metrics}

## INSTRUCTIONS:
Extract the following fields. If a field cannot be determined from the provided content, set it to null.
Respond ONLY with valid JSON — no markdown, no commentary.

{{
    "legal_entity_name": "Exact registered company / legal entity name as stated in the deck (string or null)",
    "tam": "Total Addressable Market (string with currency and description, e.g. '$50B global SaaS market')",
    "sam": "Serviceable Addressable Market (string, e.g. '$5B in India')",
    "som": "Serviceable Obtainable Market (string, e.g. '$500M targeting SMBs')",
    "business_model": "How the startup makes money (e.g. 'B2B SaaS subscription model with tiered pricing')",
    "competitive_positioning": "How they differentiate vs competitors (2-3 sentences)",
    "key_differentiators": ["list", "of", "unique", "advantages"],
    "founder_count": 2,
    "founder_backgrounds": [
        "Founder 1: Name — background summary (education, prior experience, domain expertise)",
        "Founder 2: Name — background summary"
    ],
    "domain_expertise_score": 75,
    "product_stage": "Description of current product stage (e.g. 'Live product with 500 paying customers')",
    "traction_highlights": ["key traction point 1", "key traction point 2"],
    "funding_ask": "Amount being raised and valuation if mentioned",
    "use_of_funds": "How the raised capital will be deployed"
}}

IMPORTANT:
- legal_entity_name must be the EXACT name used on letterheads or registration (e.g. "Acme Technologies Pvt. Ltd.")
- domain_expertise_score is 0-100 based on how relevant the founders' backgrounds are to the startup's domain
- Be factual — only extract what's explicitly stated or strongly implied
- For TAM/SAM/SOM, include the source if mentioned (e.g., "per XYZ Research 2024")
"""

# ---------------------------------------------------------------------------
# Financial Data Extraction
# ---------------------------------------------------------------------------
FINANCIAL_EXTRACTION_PROMPT = """You are a financial analyst reviewing a startup's financial documents for due diligence.
Analyze the following financial data and extract key metrics.

## FINANCIAL DATA:
{financial_data}

## DOCUMENT TYPES PROVIDED:
{document_types}

## INSTRUCTIONS:
Extract and compute the following metrics. Use the raw numbers from the documents.
If a metric cannot be determined, set it to null.
Respond ONLY with valid JSON — no markdown, no commentary.

{{
    "monthly_revenue": null,
    "annual_revenue": null,
    "revenue_growth_mom_pct": null,
    "gross_margin_pct": null,
    "net_margin_pct": null,
    "monthly_burn_rate": null,
    "runway_months": null,
    "mrr": null,
    "arr": null,
    "cac": null,
    "ltv": null,
    "ltv_cac_ratio": null,
    "churn_rate_pct": null,
    "arpu": null,
    "total_customers": null,
    "paying_customers": null,
    "cash_on_hand": null,
    "total_debt": null,
    "debt_to_equity_ratio": null,
    "operating_expenses_monthly": null,
    "revenue_concentration_top_customer_pct": null,
    "notes": "Any important observations about the financial data quality or consistency"
}}

IMPORTANT:
- All monetary values should be in the original currency (typically INR or USD)
- Percentages should be expressed as numbers (e.g., 75 for 75%)
- If data spans multiple periods, use the most recent period
- Flag any inconsistencies you notice between different documents
"""

# ---------------------------------------------------------------------------
# Stage Classification (AI-assisted)
# ---------------------------------------------------------------------------
STAGE_CLASSIFICATION_PROMPT = """You are classifying a startup's maturity stage for due diligence purposes.

## AVAILABLE INFORMATION:
Documents provided: {document_types}
Key metrics:
{metrics_summary}

Pitch deck summary:
{pitch_deck_summary}

## STAGE DEFINITIONS:
- Stage 0 (Idea Stage): No working product, no revenue. Concept/pitch only.
- Stage 1 (MVP Stage): Working prototype or MVP exists. Early users possible. No or minimal revenue (<$1K MRR).
- Stage 2 (Early Revenue Stage): Paying customers exist. Revenue is growing but still early ($1K-$50K MRR). Some financial history.
- Stage 3 (Growth Stage): Significant revenue (>$50K MRR). Scaling operations. Full financial paper trail.

## INSTRUCTIONS:
Classify this startup into one of the stages above.
Respond ONLY with valid JSON:

{{
    "stage": 0,
    "stage_label": "Idea Stage",
    "confidence_score": 85,
    "reasoning": "Brief explanation of why this stage was chosen",
    "signals": ["signal 1 supporting this classification", "signal 2"]
}}

IMPORTANT:
- confidence_score is 0-100
- Consider BOTH the documents provided AND the metrics available
- A startup claiming revenue but providing no financial proof should be classified lower
"""

# ---------------------------------------------------------------------------
# Revenue Cross-Verification
# ---------------------------------------------------------------------------
REVENUE_VERIFICATION_PROMPT = """You are a forensic financial analyst performing revenue verification for startup due diligence.
Your task is to cross-reference declared revenue against bank credits and GST filings.

## DECLARED REVENUE (from P&L / Financial Statements):
{pnl_data}

## BANK STATEMENT DATA:
{bank_data}

## GST RETURN DATA:
{gst_data}

## INSTRUCTIONS:
Cross-reference the three data sources and assess revenue credibility.
Respond ONLY with valid JSON — no markdown, no commentary.

{{
    "mrr_verified": true,
    "confidence_percentage": 85,
    "declared_monthly_revenue": null,
    "bank_credits_monthly_avg": null,
    "gst_declared_revenue": null,
    "variance_pnl_vs_bank_pct": null,
    "variance_pnl_vs_gst_pct": null,
    "verification_notes": "Detailed explanation of findings",
    "discrepancies": [
        {{
            "type": "revenue_mismatch",
            "severity": "medium",
            "description": "P&L shows ₹12L monthly revenue but bank credits average only ₹9.5L",
            "affected_period": "Q3 2024"
        }}
    ],
    "assessment": "verified | partially_verified | unverified | insufficient_data"
}}

## VERIFICATION RULES:
1. VARIANCE THRESHOLDS:
   - <5% variance between sources → "verified" (high confidence)
   - 5-15% variance → "partially_verified" (medium confidence, could be timing differences)
   - >15% variance → "unverified" (red flag, needs explanation)

2. COMMON LEGITIMATE EXPLANATIONS:
   - Timing differences (revenue recognized vs cash received)
   - GST filing delays (quarterly vs monthly)
   - Multi-account deposits (startup may have multiple bank accounts)
   - Pre-payments or advances

3. RED FLAGS:
   - Bank credits significantly LOWER than declared revenue
   - GST filings showing much lower taxable value than P&L revenue
   - Sudden spikes in bank credits not reflected in P&L
   - Round-number deposits that look like capital infusions counted as revenue

IMPORTANT: Be fair but rigorous. Legitimate businesses can have small variances.
Flag only genuine concerns, not trivial timing differences.
"""

# ---------------------------------------------------------------------------
# General Anomaly Detection
# ---------------------------------------------------------------------------
ANOMALY_DETECTION_PROMPT = """You are performing anomaly detection across a startup's complete financial documentation.
Look for red flags, inconsistencies, and unusual patterns.

## COMPLETE FINANCIAL SUMMARY:
{financial_summary}

## EXTRACTED METRICS:
{metrics}

## DOCUMENT TYPES ANALYZED:
{document_types}

## INSTRUCTIONS:
Analyze all provided data for anomalies and red flags.
Respond ONLY with valid JSON — no markdown, no commentary.

{{
    "anomalies_found": true,
    "overall_severity": "low",
    "anomaly_count": 2,
    "findings": [
        {{
            "category": "revenue_concentration",
            "severity": "high",
            "title": "Extreme Revenue Concentration",
            "description": "62% of revenue comes from a single customer, creating significant dependency risk",
            "evidence": "P&L breakdown shows Customer A contributing ₹X of total ₹Y revenue",
            "recommendation": "Request customer diversification plan and pipeline details"
        }},
        {{
            "category": "runway_risk",
            "severity": "medium",
            "title": "Dangerously Low Runway",
            "description": "Current cash position supports only 3.2 months of operations at current burn rate",
            "evidence": "Cash: ₹X (Balance Sheet), Monthly Burn: ₹Y (P&L)",
            "recommendation": "Urgent need for funding or cost reduction measures"
        }}
    ]
}}

## ANOMALY CATEGORIES TO CHECK:
1. **Revenue Anomalies**: Unusual spikes/drops, concentration risk, seasonality mismatch
2. **Burn Rate Anomalies**: Unsustainable burn, sudden increases in expenses
3. **Cash Flow Issues**: Negative operating cash flow, reliance on financing
4. **Cap Table Red Flags**: Excessive dilution, unusual share structures, missing ESOP
5. **Growth Inconsistencies**: Claimed growth rate vs actual financial trajectory
6. **Unit Economics**: Negative LTV/CAC ratio, increasing CAC trend
7. **Debt Concerns**: High debt-to-equity, undisclosed liabilities
8. **Projection Realism**: Projections wildly inconsistent with historical performance

## SEVERITY LEVELS:
- "critical": Deal-breaker level concern requiring immediate attention
- "high": Significant risk that should factor heavily into investment decision
- "medium": Notable concern that needs monitoring or explanation
- "low": Minor observation, may be explained by context
"""

# ---------------------------------------------------------------------------
# Insight Generation (Red Flags + Strengths + Explainability)
# ---------------------------------------------------------------------------
INSIGHT_GENERATION_PROMPT = """You are a senior investment analyst preparing a due diligence summary for an investor.
Based on the complete analysis below, map the data directly into our strictly defined 8-section JSON structure.

## STARTUP STAGE: {stage}

## EXTRACTED METRICS (Deterministic — from parsed financial documents):
{metrics}

## AI EXTRACTION RESULTS (from pitch deck):
{ai_extraction}

## ANOMALY DETECTION RESULTS:
{anomalies}

## BENCHMARK COMPARISONS:
{benchmarks}

## CROSS-DOCUMENT VERIFICATION MANDATE:
You MUST cross-reference the deterministic financial metrics (extracted from financial statements)
against the claims made in the pitch deck (from AI extraction). Explicitly flag any numerical
contradictions between documents. Examples of contradictions to detect:
- Pitch deck claims "$2M ARR" but parsed P&L shows monthly revenue of $50K (implies $600K ARR)
- Pitch deck states "500 paying customers" but KPI sheet shows 120 paying customers
- Pitch deck claims "30% MoM growth" but financial projections show flat or declining revenue
Any such contradiction MUST be added to risk_flags.red_signals with the prefix "CONTRADICTION:"

## INSTRUCTIONS:
Synthesize the quantitative and qualitative data and output ONLY a valid JSON object.
For each pillar with a reasoning field, provide UP TO 3 concise, evidence-backed bullet points.

{{
    "investor_readiness": 75,
    "business_model": {{
        "score": 80,
        "summary": "B2B SaaS with strong margins",
        "reasoning": [
            "Gross margin of 72% is above the 65% SaaS benchmark",
            "Subscription model provides predictable recurring revenue",
            "Land-and-expand motion evident from NDR exceeding 100%"
        ]
    }},
    "traction": {{"growth_rate": "15% MoM", "retention_notes": "NDR is 110%"}},
    "market_size": {{
        "tam": "$5B",
        "sam": "$1B",
        "claims_validity": "Realistic based on bottom-up analysis",
        "reasoning": [
            "TAM figure sourced from Gartner 2024 report — credible",
            "SAM derived from India-specific regulatory data — plausible",
            "No conflicting data found between deck and financial documents"
        ]
    }},
    "team": {{
        "founders_strength": "Strong domain expertise",
        "identified_gaps": "Missing technical co-founder",
        "reasoning": [
            "CEO has 8 years in fintech — directly relevant domain",
            "CTO background is generalist — technical depth may be a risk",
            "No prior exit experience — first-time founders"
        ]
    }},
    "cap_table": {{"ownership_clarity": "Clear", "notes": "10% ESOP allocated"}},
    "risk_flags": {{
        "red_signals": ["CONTRADICTION: Deck claims $2M ARR but P&L implies $600K ARR", "Runway under 4 months"],
        "amber_signals": ["High CAC relative to LTV"]
    }}
}}

CRITICAL RULES:
1. reasoning arrays are MANDATORY for business_model, market_size, and team. Max 3 bullets each.
2. Each reasoning bullet must cite a specific data point or document — no vague statements.
3. Cross-document contradictions must appear in risk_flags.red_signals with "CONTRADICTION:" prefix.
4. Do not include the financials object — it is injected deterministically from Layer 1.
5. Ensure BS Detector findings are mapped strictly into risk_flags.red_signals.
"""


# ---------------------------------------------------------------------------
# Executive Summary Narrative
# ---------------------------------------------------------------------------
EXECUTIVE_SUMMARY_PROMPT = """You are preparing the executive summary section of an automated due diligence report.

## DETECTED STAGE: {stage}
## STAGE CONFIDENCE: {confidence}%

## KEY SCORES:
- Investment Readiness: {investment_readiness}/100
- Founder Strength: {founder_strength}/100
- Financial Health: {financial_health}/100
- Revenue Credibility: {revenue_credibility}/100
- Product Maturity: {product_maturity}/100
- Market Opportunity: {market_opportunity}/100

## TOP RED FLAGS:
{red_flags}

## TOP STRENGTHS:
{strengths}

## INSTRUCTIONS:
This information has already been computed. Your task is simply to confirm the
detected stage and confidence score are reasonable.

Respond ONLY with valid JSON:
{{
    "detected_stage": "{stage}",
    "classification_confidence_score": {confidence},
    "stage_adjustment_needed": false,
    "adjusted_stage": null,
    "adjusted_confidence": null,
    "adjustment_reason": null
}}

Only set stage_adjustment_needed to true if the scores and insights strongly
contradict the initial stage classification. For example, if staged as "Growth"
but financial_health is 20 and there are multiple revenue credibility red flags.
"""
