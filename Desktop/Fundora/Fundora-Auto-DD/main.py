"""
Fundora Auto DD Engine — FastAPI Application
=============================================
Main application entry point. Sets up the FastAPI app with:
    - CORS middleware (configured for internal VPC operation)
    - Analysis endpoint (/analyze-startup)
    - Health check endpoint (/health)
    - Startup event for LLM provider validation

# ============================================================================
# LOCAL TESTING (PowerShell)
# ============================================================================
#
# Step 1: Install dependencies
#   cd c:\\Users\\prash\\Desktop\\Fundora\\Fundora-Auto-DD
#   pip install -r requirements.txt
#
# Step 2: Create .env file (copy from .env.example and add your API keys)
#   cp .env.example .env
#
# Step 3: Generate test fixtures
#   python scripts/generate_fixtures.py
#
# Step 4: Start the server
#   uvicorn main:app --reload --port 8001
#
# Step 5: Test health endpoint
#   Invoke-RestMethod -Uri "http://localhost:8001/health" -Method GET
#
# Step 6: Test analysis with sample files
#   curl -X POST "http://localhost:8001/analyze-startup" `
#     -F "files=@tests/sample_data/sample_pitch_deck.pdf" `
#     -F "files=@tests/sample_data/sample_cap_table.csv"
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import LLMMode, settings
from models.schemas import AutoDDResponse
from services.pipeline import run_pipeline
from utils.db_store import save_report, get_report, list_reports
from utils.parsers import ALL_SUPPORTED_EXTENSIONS
# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("auto_dd")


# ---------------------------------------------------------------------------
# Application Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    logger.info("=" * 70)
    logger.info("  Fundora Auto DD Engine — Starting Up")
    logger.info("=" * 70)

    mode = settings.llm_mode
    if mode == LLMMode.NONE:
        logger.warning(
            "⚠️  WARNING: No LLM API keys configured!\n"
            "   AI-dependent layers (2, 4, 5) will use fallback logic.\n"
            "   Add GEMINI_API_KEY and/or OPENAI_API_KEY to .env for full functionality."
        )
    else:
        logger.info("✅ LLM Provider Mode: %s", mode.value)

    temp_dir = settings.temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("📁 Temp upload directory: %s", temp_dir.absolute())

    logger.info("🌐 Server: http://%s:%d", settings.host, settings.port)
    logger.info("📚 API Docs: http://%s:%d/docs", settings.host, settings.port)
    logger.info("=" * 70)

    yield

    # ── Shutdown ──
    logger.info("Shutting down Auto DD Engine...")
    try:
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Cleaned up temp directory: %s", temp_dir)
    except Exception as e:
        logger.warning("Failed to clean up temp directory: %s", e)


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fundora Auto DD Engine",
    description="Automated Due Diligence engine for the Fundora platform.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# File Validation Helpers
# ---------------------------------------------------------------------------

def _validate_files(files: List[UploadFile]) -> None:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "no_files",
                "message": "At least one file must be uploaded for analysis.",
                "accepted_extensions": sorted(ALL_SUPPORTED_EXTENSIONS),
            },
        )

    total_size = 0
    validation_errors = []

    for f in files:
        filename = f.filename or "unknown"
        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[-1].lower()

        if ext not in ALL_SUPPORTED_EXTENSIONS:
            validation_errors.append(
                f"'{filename}': Unsupported file type '{ext}'. "
                f"Accepted: {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}"
            )

        if f.size is not None:
            if f.size > settings.max_file_size_bytes:
                validation_errors.append(
                    f"'{filename}': File too large ({f.size / 1024 / 1024:.1f} MB). "
                    f"Maximum: {settings.max_file_size_mb} MB."
                )
            total_size += f.size

    if total_size > settings.max_total_upload_bytes:
        validation_errors.append(
            f"Total upload size ({total_size / 1024 / 1024:.1f} MB) exceeds "
            f"the limit of {settings.max_total_upload_mb} MB."
        )

    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "validation_failed",
                "message": "One or more file validation errors occurred.",
                "errors": validation_errors,
            },
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/analyze-startup",
    response_model=AutoDDResponse,
    summary="Run Automated Due Diligence Analysis",
    tags=["Analysis"],
)
async def analyze_startup(
    files: List[UploadFile] = File(...),
    startup_id: Optional[str] = Form(default=None),
) -> AutoDDResponse:
    """
    Execute the 5-layer due diligence pipeline.

    Args:
        files:      One or more startup documents (PDF, XLSX, XLS, CSV).
        startup_id: Optional alphanumeric startup identifier (e.g. "S-001").
                    If provided, the report is persisted as:
                    data/reports/RPT-{startup_id}.json
    """
    logger.info(
        "=== Analysis request: files=%d startup_id=%s ===",
        len(files),
        startup_id or "<unset>",
    )

    _validate_files(files)

    try:
        response = await run_pipeline(
            files=files,
            startup_id=startup_id,
        )

        # ── Persist report if a startup_id was provided ──
        if startup_id:
            report_id = save_report(
                report_dict=response.model_dump(),
                startup_id=startup_id,
            )
            # Inject the report_id back into the response
            response.report_id = report_id
            logger.info("Report persisted: %s", report_id)
        else:
            logger.info(
                "No startup_id supplied — report not persisted. "
                "Pass startup_id form field (e.g. S-001) to enable persistence."
            )

        logger.info(
            "=== Analysis complete: IRS=%d confidence=%s ===",
            response.investor_readiness,
            response.confidence_interval,
        )
        return response
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "pipeline_error",
                "message": f"Analysis pipeline encountered an error: {str(e)}",
            },
        )


@app.get("/health", tags=["System"], summary="Health Check")
async def health_check():
    return {
        "status": "healthy",
        "service": "fundora-auto-dd",
        "version": "1.1.0",
        "llm_provider": settings.llm_mode.value,
        "has_gemini": settings.has_gemini,
        "has_openai": settings.has_openai,
        "max_file_size_mb": settings.max_file_size_mb,
        "max_total_upload_mb": settings.max_total_upload_mb,
        "cors_origins": settings.cors_origins,
    }


@app.get("/reports", tags=["Reports"], summary="List All Persisted Reports")
async def list_all_reports():
    """
    Return a lightweight index of all persisted due diligence reports.

    Each entry includes: report_id (e.g. RPT-S-001), startup_id, saved_at,
    investor_readiness score, and confidence_interval.
    """
    try:
        reports = list_reports()
        return {"count": len(reports), "reports": reports}
    except Exception as e:
        logger.error("Failed to list reports: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "store_error", "message": str(e)},
        )


@app.get("/reports/{report_id}", tags=["Reports"], summary="Retrieve a Persisted Report")
async def get_persisted_report(report_id: str):
    """
    Retrieve a specific due diligence report by its alphanumeric report_id.

    Accepts both full IDs (RPT-S-001) and shorthand startup IDs (S-001).
    The file on disk is: data/reports/RPT-{startup_id}.json
    """
    report = get_report(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": f"No report found for ID '{report_id}'. "
                           f"Check that the startup_id was passed during analysis.",
            },
        )
    return report




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
