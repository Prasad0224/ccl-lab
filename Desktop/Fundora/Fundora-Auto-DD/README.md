# Fundora Auto DD Engine

**Automated Due Diligence Engine** for the Fundora platform. Ingests founder documents, classifies startup maturity stage, extracts financial/qualitative data, runs anomaly detection, and outputs a structured JSON evaluation for investors.

The platform is designed with a decoupled architecture, pairing a stateless **FastAPI** backend microservice with a premium, interactive **Next.js (App Router)** Single Page Application.

---

## Technology Stack

### Frontend (SPA Layer)
- **Framework**: **Next.js (App Router)** & **React** — powering a fast, modern Single Page Application (SPA).
- **Styling**: **Tailwind CSS** — providing a sleek, responsive, and glassmorphic user interface.
- **Animations**: **Framer Motion** — driving fluid micro-animations, interactive state transitions, and visually masking API latency.
- **Data Visualization**: **Recharts** — rendering dynamic, interactive investment metrics and stage-benchmarked data charts.

### Backend (Microservice Layer)
- **Framework**: **FastAPI** — high-performance, asynchronous Python microservice routing all core DD computations.
- **Deterministic Rules & Parsing**: **Pandas** and **pdfplumber** — cleaning, normalising, and extracting tabular financial data with strict type coercion.
- **AI Orchestration**: **Gemini 1.5 Pro** & **GPT-4o** — utilizing a dual-provider orchestration strategy for deep context ingestion and structured JSON synthesis.

---

## Architecture

```
5-Layer Processing Pipeline
═══════════════════════════

Layer 0 ─► Document Parsing & Classification
Layer 1 ─► Deterministic Rules Engine (Pandas, formulas)
Layer 2 ─► AI Extraction & Verification (Gemini preferred)
Layer 3 ─► Industry Benchmarking
Layer 4 ─► Risk Scoring Model (OpenAI preferred)
Layer 5 ─► Output Generation (structured JSON)
```

---

## Core Features

### 1. Stateless SaaS Utility
The engine is a frictionless, public-facing stateless microservice. It no longer requires rigid internal database IDs or tracking tokens, meaning founders can drop documents into the engine at any time for instant, on-the-fly analysis.

### 2. Stage-Adaptive Document Routing
The frontend UI acts as a dynamic state machine that calculates a startup's maturity before requiring files:
- **Stage 0 (Idea)**: 2 documents required (e.g. Pitch Deck, Cap Table)
- **Stage 1 (MVP)**: 4 documents required (including KPI sheets/projections)
- **Stage 2/3 (Growth)**: 9 core documents required (including P&Ls, Bank Statements, GST Returns)

The "Run DD" submission button strictly gates users who do not provide structurally mandatory files for their computed stage.

### 3. Zero-Dependency Demo Mode
The Next.js frontend contains a built-in **"Demo Mode"** toggle. Clicking this allows investors and developers to preview the premium glassmorphic dashboard and interactive charts instantly with high-fidelity mockup data, requiring zero API keys or backend setup.

### 4. Enterprise-Grade Fraud Prevention
- **Heuristic Content Scan**: A fast parser automatically scans uploaded PDFs for VC-standard keywords. Fake or irrelevant uploads are aggressively blocked before wasting LLM tokens.
- **Entity Resolution**: The AI securely cross-references the official legal entity name found in the pitch deck against all supplied financial documents to detect document mismatching and flag potential fraud.

---

## Quick Start (Dual-Boot Sequence)

To run the full Fundora Auto DD Engine locally, you must launch both the FastAPI backend and the Next.js frontend.

### 1. Configure Environment (API Keys)

Initialize the environment file in the backend directory:
```powershell
cd c:\Users\prash\Desktop\Fundora\Fundora-Auto-DD
cp .env.example .env
# Edit .env and inject your LLM API keys:
# GEMINI_API_KEY=your-key-here
# OPENAI_API_KEY=your-key-here
```

### 2. Boot the Python Backend (Terminal 1)

Open a new terminal window and run:
```powershell
cd c:\Users\prash\Desktop\Fundora\Fundora-Auto-DD
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```
The FastAPI server will boot and listen on **http://localhost:8001**. You can view interactive Swagger documentation at `http://localhost:8001/docs`.

### 3. Boot the Next.js Frontend (Terminal 2)

Open a second terminal window and run:
```powershell
cd c:\Users\prash\Desktop\Fundora\fundora-frontend
npm install
npm run dev
```
The Next.js application will boot and run on **http://localhost:3000**.

### 4. Access the Application

Open your browser and navigate to **http://localhost:3000**.
- **Live Mode**: Upload documents corresponding to your calculated stage and submit to run a live analysis through the FastAPI backend on port 8001.
- **Demo Mode**: Toggle "Demo Mode" at the top of the interface to instantly explore the dashboard using pre-loaded high-fidelity due diligence data.

---

## LLM Provider Configuration & State

The system is provider-agnostic and uses the `LLMMode` Enum to seamlessly route traffic depending on available API keys:

| API Keys Available | Behavior |
|---|---|
| **Both** (Gemini + OpenAI) | Gemini → extraction (Layer 2), OpenAI → scoring/narrative (Layer 4/5). Optimized for best results. |
| **Gemini only** | Gemini handles all AI layers (Layer 2, 4, and 5) seamlessly. |
| **OpenAI only** | OpenAI handles all AI layers (Layer 2, 4, and 5) seamlessly. |
| **None** | Deterministic layers (1, 3) run; AI layers gracefully degrade and return fallback deterministic results. |

---

## Unit Economics (FinOps)
The optimized dual-provider mode (Gemini for context, GPT-4o for synthesis) costs roughly **$0.05 per startup analysis**. This allows the processing of **1,000 pitch decks for approximately $50**, representing a massive cost reduction compared to human auditing.

---

## Project Structure

```
Fundora-Auto-DD/
├── main.py                  # FastAPI entry point
├── config.py                # Settings & LLM provider detection
├── requirements.txt         # Dependencies
├── .env.example             # Environment template
├── routers/
│   └── analyze.py           # POST /analyze-startup
├── services/
│   ├── pipeline.py          # 5-layer pipeline orchestrator
│   ├── stages.py            # Stage detection and requirements
│   └── scoring.py           # Risk scoring math and industry baseline comparisons
├── models/
│   ├── schemas.py           # Pydantic input/output models
│   └── stage_config.py      # Stage requirements & weights
├── utils/
│   ├── parsers.py           # PDF/CSV/Excel parsing
│   ├── llm_orchestrator.py  # Gemini/OpenAI fallback logic
│   └── scoring_engine.py    # Risk scoring math
├── prompts/
│   ├── extraction.py        # Layer 2 prompts
│   ├── anomaly_detection.py # Revenue verification prompts
│   └── narrative.py         # Layer 5 insight prompts
├── scripts/
│   └── (deleted)            # Mock generators removed for production
└── tests/
    └── (deleted)            # Sample data removed for production
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/analyze-startup` | Run full DD analysis |
| `GET` | `/health` | Service health check |
| `GET` | `/docs` | Interactive API documentation |

---

## Output Schema

```json
{
  "executive_summary": {
    "detected_stage": "Early Revenue Stage",
    "classification_confidence_score": 92
  },
  "scores": {
    "investment_readiness_score": 76,
    "founder_strength": 84,
    "financial_health": 71,
    "revenue_credibility": 66,
    "product_maturity": 79,
    "market_opportunity": 82
  },
  "verification": {
    "mrr_verified": true,
    "confidence_percentage": 98,
    "verification_notes": "Invoice data, bank credits, and GST declarations match."
  },
  "insights": {
    "red_flags": ["62% revenue concentrated in 1 customer"],
    "strengths": ["Strong gross margin", "Efficient CAC"]
  }
}
```

---

## Integration Notes

This service runs as a standalone microservice on port 8001. When integrating with the main Fundora-Backend (Node.js/Express on port 5000):

1. The backend or frontend can call `POST http://localhost:8001/analyze-startup` using a multipart form payload.
2. Cross-Origin Resource Sharing (CORS) is enabled on the FastAPI service to permit direct browser queries from `http://localhost:3000`.
3. No authentication is enforced on this service (recommended to keep within a secure VPC for staging/production).
