# Fundora Auto DD — Architecture Report

## Decoupled System Architecture

The Fundora Auto DD Engine operates as a decoupled web application comprising:
1. **Frontend SPA Layer**: A Next.js (App Router) client application utilizing Tailwind CSS, Framer Motion, and Recharts.
2. **Backend Microservice**: A stateless, sequential 5-layer pipeline microservice built with **FastAPI**.

This decoupled architecture allows the UI to run as a rich, interactive client experience while the backend remains a stateless SaaS utility focused purely on parsing, processing, and auditing documents.

```
┌──────────────────────────────────────────────┐
│            Next.js Frontend (SPA)            │
│          (Runs on http://localhost:3000)     │
└──────────────────────┬───────────────────────┘
                       │
                       │ multipart/form-data
                       │ (CORS Enabled)
                       ▼
┌──────────────────────────────────────────────┐
│           FastAPI Backend Service            │
│          (Runs on http://localhost:8001)     │
└──────────────────────────────────────────────┘
```

---

## 1. Backend Microservice Architecture

The backend consists of **10 core modules**:
1. `main.py`: Entry point, FastAPI application startup, CORS middleware configuration, and `/analyze-startup` route.
2. `config.py`: Environment variables, size limits, and active LLM provider selection.
3. `services/pipeline.py`: Pipeline orchestrator. Instantiates and threads `PipelineContext` through the 5 layers.
4. `services/stages.py`: Evaluates file requirements per startup stage and computes stage classification.
5. `services/scoring.py`: Computes deterministic financial metrics, baselines, and category scoring weights.
6. `utils/parsers.py`: Ingests and cleans files. Uses `pdfplumber` for PDF text and `pandas` for CSV/Excel data. Normalizes financial tables and coerces `NaN` values to `None` for valid JSON output.
7. `utils/llm_orchestrator.py`: API abstraction. Manages Gemini and OpenAI calls, handles retries with exponential backoffs, and enforces structured JSON configurations.
8. `models/schemas.py`: Pydantic models validating inputs and forcing output compatibility with the frontend schema.
9. `prompts/templates.py`: Centralized prompt templates for extraction, anomaly detection, and insights generation.
10. `requirements.txt`: Python package dependencies.

### The 5-Layer Processing Pipeline
- **Layer 0 (Ingestion & Classification)**: FastAPI parses the incoming `multipart/form-data` payload. Files are cleaned via `parsers.py`. Heuristic scanning occurs here to reject invalid documents before processing.
- **Layer 1 (Deterministic Extraction)**: Extracts cash balance, runway, and burn rate directly from spreadsheets using Pandas and regex selectors.
- **Layer 2 (AI Extraction & Anomaly)**: Uses Gemini 1.5 Pro to extract qualitative information (TAM, team backgrounds) and runs entity resolution to cross-reference legal entity names and detect fraud.
- **Layer 3 (Benchmarking)**: Benchmarks the startup's metrics against industry SaaS baselines based on the determined maturity stage.
- **Layer 4 (Scoring Engine)**: Applies stage-adaptive weights to calculate individual category scores and a composite Investment Readiness Score (0-100).
- **Layer 5 (Narrative Generation)**: Uses GPT-4o to synthesize key strengths, critical red flags, and verification notes into a clean JSON structure.

---

## 2. Frontend SPA Architecture (Next.js client)

The frontend is built with **Next.js (App Router)** and **TypeScript**. It is designed as a dynamic, interactive state machine that manages the user journey.

### The Client State Machine
The client application progresses sequentially through three distinct UX states:

```
┌──────────────┐      Submit Files      ┌─────────────────┐      Complete      ┌───────────────────┐
│  1. Intake   ├───────────────────────►│ 2. Loading CSS  ├───────────────────►│ 3. Results Screen │
│  Maturity    │     (Multipart form)   │ Matrix Spinner  │    (JSON Parse)   │ Glassmorphic dashboard│
└──────────────┘                        └─────────────────┘                    └───────────────────┘
```

1. **Intake Stage**: 
   - Captures metadata from the user ("Is your product live?", "Do you have revenue?").
   - Dynamically calculates the startup's maturity stage (Idea, MVP, or Growth) on the client side.
   - Enforces **Stage-Adaptive Document Routing**. The UI dynamically updates to require specific files (2 for Idea, 4 for MVP, 9 for Growth). The "Run DD" button remains disabled until all required files are supplied.
2. **Loading Matrix Stage**:
   - Displays a dynamic loading grid using Framer Motion animations.
   - Masks backend latency (which takes 10-15 seconds due to document parsing and sequential LLM calls) by showing sequential processing steps (e.g., "Parsing Documents...", "Verifying Stated MRR...", "Benchmarking SaaS Metrics...").
3. **Results Dashboard**:
   - Displays the Pydantic-validated payload.
   - Uses **Recharts** to plot startup evaluation scores, category breakdowns, and financial runway projections.
   - Highlights red flags and strengths in high-contrast visual alert cards.

---

## 3. Communication and Data Payload Flow

### CORS Handling
Because the frontend runs on `http://localhost:3000` and the backend runs on `http://localhost:8001`, the FastAPI app configures `CORSMiddleware` in `main.py` to allow direct client-side requests:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### FormData Construction & Serialization
The Next.js client uses native browser `FormData` to construct a multipart payload.
- Files are appended directly as blobs/files.
- Metadatas (e.g., stage answers) are stringified and appended as form fields.
- The request is dispatched via standard `fetch` with the body containing the `FormData`.

Upon receiving the payload, FastAPI parses the files, feeds them into the 5-layer pipeline, and returns a verified JSON response mapping directly to the client's `AutoDDResponse` interface.

---

## 4. Token Consumption & Latency Estimates

### Rough API Cost per Startup
Based on current API pricing models:
- **Gemini 1.5 Pro**: $3.50 / 1M input + $10.50 / 1M output
  - Input: (18,000 / 1,000,000) * $3.50 = $0.063
  - Output: (1,500 / 1,000,000) * $10.50 = $0.015
  - *Gemini Cost*: ~$0.078
- **GPT-4o**: $5.00 / 1M input + $15.00 / 1M output
  - Input: (3,000 / 1,000,000) * $5.00 = $0.015
  - Output: (500 / 1,000,000) * $15.00 = $0.007
  - *OpenAI Cost*: ~$0.022

**Total Estimated LLM Cost = ~$0.10 per startup analysis.**

### Latency Bottlenecks
1. **Document Parsing (Layer 0)**: `pdfplumber` relies heavily on single-core CPU processing. Dense PDFs can take 5-10 seconds to parse.
2. **Sequential LLM Calls**: Currently sequential (Layer 2 must finish before Layer 5 begins).
**Expected Total Latency**: ~10-15 seconds per startup. This latency is managed on the frontend via the animated Loading Matrix.
