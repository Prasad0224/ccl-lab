"""
Fundora Auto DD Engine — PostgreSQL Persistence Adapter
=====================================================
Database insertion logic for persisting the AutoDDResponse payload into 
the hybrid relational-JSONB structure defined in auto_dd_reports.sql.
"""

import os
import json
import logging
from typing import Optional
import psycopg2
from psycopg2.extras import Json

from models.schemas import AutoDDResponse

logger = logging.getLogger("auto_dd.db_postgres")

def save_report_to_db(payload: AutoDDResponse, investor_id: str) -> str:
    """
    Intercepts the final JSON output of the AutoDD pipeline and writes it
    to the auto_dd_reports PostgreSQL table.
    
    Extracts top-level metadata into relational columns, and dumps the 
    remaining 7 pillars into the report_data JSONB column.
    
    Args:
        payload: The fully assembled AutoDDResponse Pydantic model.
        investor_id: The custom alphanumeric ID of the investor running the analysis.
        
    Returns:
        The alphanumeric report_id (e.g. 'RPT-S-001')
    """
    
    # 1. Enforce strict alphanumeric ID extraction for relational columns
    startup_id = payload.startup_id
    if not startup_id:
        raise ValueError("startup_id is required to persist the report to PostgreSQL")
        
    report_id = payload.report_id or f"RPT-{startup_id.strip().upper()}"
    investor_readiness_score = payload.investor_readiness
    confidence_interval = payload.confidence_interval
    
    # 2. Extract the remaining 7 pillars for the JSONB payload
    report_data = {
        "business_model": payload.business_model.model_dump(),
        "financials": payload.financials.model_dump(),
        "traction": payload.traction.model_dump(),
        "market_size": payload.market_size.model_dump(),
        "team": payload.team.model_dump(),
        "cap_table": payload.cap_table.model_dump(),
        "risk_flags": payload.risk_flags.model_dump()
    }
    
    # 3. Parameterized query to prevent SQL injection
    query = """
        INSERT INTO auto_dd_reports (
            report_id, 
            startup_id, 
            investor_id, 
            investor_readiness_score, 
            confidence_interval, 
            report_data
        ) VALUES (
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (report_id) DO UPDATE SET
            investor_id = EXCLUDED.investor_id,
            investor_readiness_score = EXCLUDED.investor_readiness_score,
            confidence_interval = EXCLUDED.confidence_interval,
            report_data = EXCLUDED.report_data,
            created_at = CURRENT_TIMESTAMP;
    """
    
    conn = None
    try:
        # Assumes DATABASE_URL is provided in the environment (e.g. via .env)
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
            
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute(query, (
                report_id,
                startup_id,
                investor_id,
                investor_readiness_score,
                confidence_interval,
                Json(report_data)
            ))
        conn.commit()
        logger.info(f"Successfully persisted report {report_id} to PostgreSQL")
        
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to persist report {report_id} to PostgreSQL: {e}")
        raise e
        
    finally:
        if conn:
            conn.close()
            
    return report_id
