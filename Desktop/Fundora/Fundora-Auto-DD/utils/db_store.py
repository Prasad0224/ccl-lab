"""
Fundora Auto DD Engine — JSON Document Store
=============================================
Implements a per-file JSON document store for persisting due diligence reports.

Design Decisions:
    - Each report is stored as a separate file: data/reports/RPT-{startup_id}.json
    - This avoids async concurrency conflicts of a single shared reports.json file.
    - Compatible with future migration to Firestore/MongoDB (each report is a document).
    - IDs strictly follow the project convention: RPT-{startup_id}
      where startup_id is the caller-provided alphanumeric identifier
      (e.g. S-001, S-002, INV001, INV-042).

Usage:
    from utils.db_store import save_report, get_report, list_reports

    report_id = save_report(response, startup_id="S-001")
    # → Writes to: data/reports/RPT-S-001.json
    # → Returns:   "RPT-S-001"

    report = get_report("RPT-S-001")
    all_reports = list_reports()
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("auto_dd.db_store")

# ---------------------------------------------------------------------------
# Storage Root
# ---------------------------------------------------------------------------

# Resolve relative to this file's project root, not cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = _PROJECT_ROOT / "data" / "reports"


def _ensure_reports_dir() -> None:
    """Ensure the reports directory exists (idempotent)."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# ID Construction
# ---------------------------------------------------------------------------

def build_report_id(startup_id: str) -> str:
    """
    Build a report ID from a startup_id using the strict project convention.

    Format: RPT-{startup_id}
    Examples:
        build_report_id("S-001")   → "RPT-S-001"
        build_report_id("INV001")  → "RPT-INV001"
        build_report_id("S-042")   → "RPT-S-042"

    Args:
        startup_id: The caller-provided alphanumeric startup identifier.

    Returns:
        A formatted report ID string.
    """
    clean_id = startup_id.strip().upper()
    return f"RPT-{clean_id}"


def build_report_filename(report_id: str) -> Path:
    """
    Resolve the absolute path for a report file.

    Args:
        report_id: The full report ID (e.g. "RPT-S-001").

    Returns:
        Absolute Path to the JSON file.
    """
    return REPORTS_DIR / f"{report_id}.json"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save_report(report_dict: Dict[str, Any], startup_id: str) -> str:
    """
    Persist a due diligence report as an individual JSON file.

    The filename is deterministically derived from the startup_id:
        data/reports/RPT-{startup_id}.json

    If a report already exists for this startup_id, it is overwritten
    (ensures idempotency for re-runs on the same startup).

    Args:
        report_dict: The serialised AutoDDResponse dict.
        startup_id:  The alphanumeric startup identifier (e.g. "S-001").

    Returns:
        The report_id string (e.g. "RPT-S-001").
    """
    _ensure_reports_dir()

    report_id = build_report_id(startup_id)
    file_path = build_report_filename(report_id)

    # Inject persistence metadata
    envelope = {
        "report_id": report_id,
        "startup_id": startup_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.1",
        "report": report_dict,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False)

        logger.info(
            "Report persisted: %s → %s",
            report_id,
            file_path,
        )
    except OSError as e:
        # Log but do not raise — persistence failure must never crash the API response
        logger.error("Failed to persist report %s: %s", report_id, e)

    return report_id


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_report(report_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a persisted report by its report_id.

    Args:
        report_id: The full report ID (e.g. "RPT-S-001").
                   Accepts both "RPT-S-001" and "S-001" (auto-prefixes if needed).

    Returns:
        The full envelope dict (including metadata + report), or None if not found.
    """
    # Normalise: accept "S-001" shorthand by auto-prefixing
    if not report_id.upper().startswith("RPT-"):
        report_id = build_report_id(report_id)

    file_path = build_report_filename(report_id)

    if not file_path.exists():
        logger.warning("Report not found: %s", report_id)
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read report %s: %s", report_id, e)
        return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_reports() -> List[Dict[str, Any]]:
    """
    Return an index of all persisted reports.

    Reads each JSON file in the reports directory and extracts lightweight
    metadata — does NOT load the full report payload.

    Returns:
        List of dicts, each with: report_id, startup_id, saved_at,
        investor_readiness, confidence_interval.
        Sorted by saved_at descending (newest first).
    """
    _ensure_reports_dir()

    index: List[Dict[str, Any]] = []

    for file_path in sorted(REPORTS_DIR.glob("RPT-*.json")):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                envelope = json.load(f)

            report_payload = envelope.get("report", {})
            index.append({
                "report_id": envelope.get("report_id"),
                "startup_id": envelope.get("startup_id"),
                "saved_at": envelope.get("saved_at"),
                "investor_readiness": report_payload.get("investor_readiness"),
                "confidence_interval": report_payload.get("confidence_interval"),
                "schema_version": envelope.get("schema_version"),
            })
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping unreadable report file %s: %s", file_path.name, e)
            continue

    # Sort newest first
    index.sort(key=lambda x: x.get("saved_at") or "", reverse=True)
    return index
